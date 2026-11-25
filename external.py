"""``sync``: kanban cards become external links and forward execution moves (HP-12).

S5 fixes the direction of truth: a board column may drive the *execution* status
of the specs and features it names, and the store owns everything else. This
module is the whole verb:

* read every named board STRICTLY read-only (``kanban_reader.read_board_cards``,
  never a write, never the live file);
* resolve the ``product: F7, S12`` lines of each card against the store; an
  unresolved reference is reported, never an error;
* propose one ``external_link`` row per (card, entry) — ``implements`` for F/S,
  ``tracks`` for R/A — and leave existing rows alone;
* propose an execution status move for specs and features only, along the
  FURTHEST stage any of the entry's cards reached. Nothing ever sets ``verified``
  (A19) and nothing ever ships a feature; a backward card under a ``verified``
  entry warns instead of moving it (A20);
* with ``--apply``: all new link rows in ONE transaction, then each move through
  ``store.update_entity`` so every S2 exit condition applies. A refused move is
  reported and does not stop the others or undo the links;
* the report ends with a read-only ``reconcile`` block: every card of a feature
  or requirement is finished but the owner still has to ship it or run its
  criteria. It names that step and moves nothing.

Dry run is the default. The CLI (``cli._cmd_sync``) only parses and prints; the
output lines are built here, in the documented order.
"""

from __future__ import annotations

from typing import Iterable, Mapping, Optional, Sequence

try:  # package submodule (Hermes plugin loader)
    from . import kanban_reader, model, store
except ImportError:  # top-level module (test suite)
    import kanban_reader  # type: ignore[no-redef]
    import model  # type: ignore[no-redef]
    import store  # type: ignore[no-redef]

#: link-line order: R, then A, then F, then S (S1's kind order)
KIND_ORDER = {"requirement": 0, "criterion": 1, "feature": 2, "spec": 3}
PREFIX = {"requirement": "R", "criterion": "A", "feature": "F", "spec": "S"}

#: the role a card plays on an entry (S5)
ROLE = {"requirement": "tracks", "criterion": "tracks",
        "feature": "implements", "spec": "implements"}

#: kinds whose status a card may move
EXECUTION_KINDS = ("feature", "spec")

#: statuses sync never leaves (a terminal state, or one only evidence may grant)
NEVER_MOVED = ("verified", "shipped", "superseded", "cut")

#: stage -> the status a FEATURE takes for it (``implemented`` never ships)
FEATURE_TARGET: Mapping[str, Optional[str]] = {"approved": "planned",
                                              "in_progress": "in_progress",
                                              "implemented": None}

#: the forward chain of each movable kind, as a stage index
CHAIN: Mapping[str, tuple[str, ...]] = {"spec": ("draft", "approved", "implemented",
                                                 "verified"),
                                        "feature": ("planned", "in_progress", "shipped")}

#: the one status no column may map to (A19)
VERIFIED_MESSAGE = ("nothing maps to verified: that status comes only from criteria"
                    " with evidence")
MAP_TARGETS = ("approved", "in_progress", "implemented")

#: kinds the read-only ``reconcile`` block speaks about (a card finishes; the owner acts)
RECONCILE_KINDS = ("requirement", "feature")

#: criterion statuses that ask the owner for nothing more
CRITERION_CLOSED = ("passed", "waived")


def parse_map(pairs: Iterable[str]) -> dict[str, Optional[str]]:
    """``COLUMN=STATUS`` pairs -> ``{column: execution status or None}``.

    ``none`` or an empty value means the column drives nothing; ``verified`` is
    refused outright (A19); an unknown column name is accepted (boards add
    columns). A status the model does not have is refused naming the choices.
    """
    mapping: dict[str, Optional[str]] = {}
    for pair in pairs:
        if "=" not in str(pair):
            raise store.StoreError(f"--map expects COLUMN=STATUS, got {pair!r}")
        column, status = str(pair).split("=", 1)
        column, status = column.strip(), status.strip().lower()
        if not column:
            raise store.StoreError(f"--map expects COLUMN=STATUS, got {pair!r}: empty column")
        if status == "verified":
            raise store.StoreError(VERIFIED_MESSAGE)
        if status in ("", "none"):
            mapping[column] = None
        elif status in MAP_TARGETS:
            mapping[column] = status
        else:
            raise store.StoreError(
                f"--map {column}={status}: status must be approved, in_progress,"
                " implemented or none")
    return mapping


def _row_status(con, project: str, kind: str, entity_id: int) -> Optional[str]:
    row = con.execute(
        f"SELECT status FROM {store.ENTITY_TABLE[kind]} WHERE project = ? AND id = ?",
        (project, entity_id)).fetchone()
    return row["status"] if row is not None else None


def _resolve(con, project: str, token: str) -> tuple[Optional[tuple[str, int]], Optional[str]]:
    """``(kind, id)`` for a resolving ref, else ``(None, reason)``."""
    if token.startswith("DOC"):
        return None, "DOC cannot carry a card link"
    try:
        return store.resolve_entity(project, token, con=con), None
    except (store.StoreError, model.ValidationError) as exc:
        return None, str(exc)


def _read_boards(boards: Sequence[str]) -> list[tuple[str, list]]:
    """Every board's cards, all of them read before anything is written."""
    board_cards = []
    for board in boards:
        try:
            cards = kanban_reader.read_board_cards(board)
        except kanban_reader.KanbanReadError as exc:
            raise store.StoreError(str(exc)) from exc
        board_cards.append((board, cards))
    return board_cards


def linked_boards(con, project: str) -> list[str]:
    """The board slugs this project's kanban link rows name (sorted, unique).

    ``sync`` writes ``external_id`` as ``<board>/<card id>``; that prefix is the
    only place a project names a board. A row whose id has no ``/`` (older links)
    names no board and is ignored here.
    """
    boards: set[str] = set()
    for row in con.execute(
            "SELECT external_id FROM external_link"
            " WHERE project = ? AND system = 'kanban'", (project,)):
        board, separator, _card_id = str(row["external_id"] or "").partition("/")
        if separator and board:
            boards.add(board)
    return sorted(boards)


def reconcile_lines(entries: Sequence[Mapping]) -> list[str]:
    """The read-only ``reconcile`` block: the cards are finished, the entry is not.

    Report only — nothing here moves a status or writes a row. *entries* is the
    data ``sync`` already gathered, one mapping per linked entry:
    ``{kind, entity_id, status, cards, criteria}`` where ``cards`` holds
    ``(mapped execution status or None, board, card id, column)`` in read order
    and ``criteria`` the statuses of a requirement's criteria (empty for a
    feature). A card whose column maps to nothing (``archived``) is ignored, not
    counted as open.

    A *feature* prints when every card maps to ``implemented`` and the status is
    below ``shipped`` (``sync`` never ships one, so the owner decides). A
    *requirement* prints when every tracking card maps to ``implemented`` and one
    criterion is neither ``passed`` nor ``waived``. Entries with an open card
    print nothing; the lines follow the REF order of the link lines.
    """
    lines: list[str] = []
    for entry in sorted(entries, key=lambda one: (KIND_ORDER[one["kind"]],
                                                  one["entity_id"])):
        kind, status = entry["kind"], entry["status"]
        if kind not in RECONCILE_KINDS:
            continue                      # a card can finish a spec; only F/R lag
        cards = [one for one in entry["cards"] if one[0] is not None]
        if not cards or any(one[0] != "implemented" for one in cards):
            continue                      # at least one card is still open
        ref = f"{PREFIX[kind]}{entry['entity_id']}"
        count = len(cards)
        if kind == "feature":
            chain = CHAIN["feature"]
            if status not in chain or chain.index(status) >= chain.index("shipped"):
                continue                  # already shipped (or cut): nothing to do
            lines.append(f"reconcile {ref}  {status}, {count}/{count} cards done:"
                         " ready to ship? (owner decides)")
            continue
        criteria = list(entry["criteria"])
        if all(one in CRITERION_CLOSED for one in criteria):
            continue                      # no criterion left to run
        passed = sum(1 for one in criteria if one == "passed")
        lines.append(f"reconcile {ref}  tracked by {count}"
                     f" card{'' if count == 1 else 's'}, all done;"
                     f" criteria {passed}/{len(criteria)} passed: run the criteria")
    return lines


def sync(project: str, boards: Sequence[str], *, overrides: Iterable[str] = (),
         apply: bool = False) -> dict:
    """Propose (and with ``apply``, write) the links and execution moves.

    Returns ``{project, boards, cards, with_lines, lines, conflicts}``: ``lines``
    is the ordered output (links, statuses, warnings, unresolved) and
    ``conflicts`` the number of refused moves (the caller exits 3 when non-zero).
    """
    mapping = parse_map(overrides)
    if not store.project_exists(project):
        raise store.StoreError(f"project {project!r} does not exist")
    unique_boards = list(dict.fromkeys(str(board) for board in boards))
    if not unique_boards:
        raise store.StoreError("sync needs at least one --board")
    board_cards = _read_boards(unique_boards)

    cards_read = 0
    with_lines = 0
    link_rows: list[tuple] = []          # (kind, entity_id, board, card_id)
    unresolved: list[str] = []
    entry_cards: dict[tuple[str, int], list[tuple]] = {}
    all_cards: dict[tuple[str, int], list[tuple]] = {}
    con = store.connect()
    try:
        for board, cards in board_cards:
            for card in cards:
                cards_read += 1
                parsed = kanban_reader.parse_product_line(card.body)
                if not parsed.refs and not parsed.invalid:
                    continue             # no product line: ignored, but counted
                with_lines += 1
                mapped = kanban_reader.map_card_status(card.status, mapping)
                for token in parsed.refs:
                    resolved, reason = _resolve(con, project, token)
                    if resolved is None:
                        unresolved.append(f"unresolved  {board}/{card.id}  {token}"
                                          f"  {reason}")
                        continue
                    kind, entity_id = resolved
                    link_rows.append((kind, entity_id, board, card.id))
                    # every card of the entry, mapped or not: the reconcile block
                    # needs R and A too, and must see an archived card as ignored
                    all_cards.setdefault((kind, entity_id), []).append(
                        (mapped, board, card.id, card.status))
                    if kind in EXECUTION_KINDS and mapped is not None:
                        entry_cards.setdefault((kind, entity_id), []).append(
                            (kanban_reader.STAGE_ORDER[mapped], mapped, board,
                             card.id, card.status))

        existing = {(row["entity_type"], row["entity_id"], row["external_id"])
                    for row in con.execute(
                        "SELECT entity_type, entity_id, external_id FROM external_link"
                        " WHERE project = ? AND system = 'kanban'", (project,))}
        new_rows: list[tuple[str, int, str, str]] = []
        link_lines: list[str] = []
        for kind, entity_id, board, card_id in sorted(
                link_rows, key=lambda row: (KIND_ORDER[row[0]], row[1], row[2], row[3])):
            ref = f"{PREFIX[kind]}{entity_id}"
            external_id = f"{board}/{card_id}"
            key = (kind, entity_id, external_id)
            outcome = "exists" if key in existing else "new"
            if outcome == "new":
                new_rows.append((kind, entity_id, external_id, ROLE[kind]))
                existing.add(key)
            link_lines.append(f"link   {ref}  kanban:{external_id}  {ROLE[kind]}"
                              f"  {outcome}")

        proposals: list[dict] = []       # ordered by REF, one per linked F/S entry
        warning_lines: list[str] = []
        for (kind, entity_id), card_list in sorted(
                entry_cards.items(), key=lambda item: (KIND_ORDER[item[0][0]], item[0][1])):
            ref = f"{PREFIX[kind]}{entity_id}"
            current = _row_status(con, project, kind, entity_id)
            if current is None:          # pragma: no cover - resolved above
                continue
            stage, mapped, board, card_id, column = max(
                card_list, key=lambda item: (item[0], str(item[2]), str(item[3])))
            if current in NEVER_MOVED:
                if current == "verified" and stage < kanban_reader.STAGE_ORDER["implemented"]:
                    warning_lines.append(
                        f"warning: {ref} is verified but card {board}/{card_id} is in"
                        f" {column} (maps to {mapped}); entry unchanged")
                continue
            target = FEATURE_TARGET[mapped] if kind == "feature" else mapped
            if target is None:           # a done card never ships a feature
                continue
            chain = CHAIN[kind]
            forward = (target in chain and current in chain
                       and chain.index(target) > chain.index(current))
            proposals.append({"kind": kind, "entity_id": entity_id, "ref": ref,
                              "current": current, "target": target, "forward": forward,
                              "reason": f"card {board}/{card_id} in {column}"})

        conflicts = 0
        status_lines: list[str] = []
        if apply and new_rows:
            with con:                    # ONE transaction for every new link
                for kind, entity_id, external_id, role in new_rows:
                    con.execute(
                        "INSERT OR IGNORE INTO external_link(project, entity_type,"
                        " entity_id, system, external_id, url, role)"
                        " VALUES (?,?,?,?,?,NULL,?)",
                        (project, kind, entity_id, "kanban", external_id, role))
        for proposal in proposals:
            ref, current, target = proposal["ref"], proposal["current"], proposal["target"]
            if not proposal["forward"]:
                status_lines.append(f"status {ref}  {current} -> {target}  unchanged")
                continue
            if not apply:
                status_lines.append(f"status {ref}  {current} -> {target}  proposed")
                continue
            try:
                store.update_entity(project, ref, {"status": target},
                                    reason=proposal["reason"], con=con)
            except (store.StoreError, model.ValidationError) as exc:
                conflicts += 1
                status_lines.append(f"status {ref}  {current} -> {target}"
                                    f"  refused: {exc}")
                continue
            status_lines.append(f"status {ref}  {current} -> {target}  applied")

        reconcile_entries: list[dict] = []
        for (kind, entity_id), cards in sorted(
                all_cards.items(), key=lambda item: (KIND_ORDER[item[0][0]], item[0][1])):
            if kind not in RECONCILE_KINDS:
                continue
            status = _row_status(con, project, kind, entity_id)   # after any move
            if status is None:            # pragma: no cover - resolved above
                continue
            criteria: list[str] = []
            if kind == "requirement":
                criteria = [row["status"] for row in con.execute(
                    "SELECT status FROM criterion WHERE project = ? AND requirement_id = ?"
                    " ORDER BY id", (project, entity_id))]
            reconcile_entries.append({"kind": kind, "entity_id": entity_id,
                                      "status": status, "cards": cards,
                                      "criteria": criteria})
    finally:
        con.close()

    lines = [*link_lines, *status_lines, *warning_lines, *sorted(unresolved),
             *reconcile_lines(reconcile_entries)]
    return {"project": project, "boards": len(unique_boards), "cards": cards_read,
            "with_lines": with_lines, "lines": lines, "conflicts": conflicts}
