"""The kanban side of the external links (card HP-12a) — pure, plus one reader.

S5 gives the direction of truth: a card's column may drive the *execution* status
of the specs and features it names, and the store owns everything else. This
module is the whole pure core of that rule:

  * ``parse_product_line`` reads the ``product: F7, S12`` lines of a card body;
  * ``map_card_status`` maps a board column to an execution status. **Nothing
    maps to ``verified``** — a finished card is evidence, not proof (A19);
  * ``status_warning`` reports A20: a card that moves backward under a *verified*
    entry raises a warning and changes nothing;
  * ``propose_links`` proposes link rows without writing any;
  * ``read_board_cards`` reads a board STRICTLY read-only.

Nothing here writes kanban data. A board is read through SQLite's ``.backup``
into a private temp file, which is then opened ``mode=ro`` with
``PRAGMA query_only = 1``: the live file is never opened for reading directly and
a write attempt on the connection the module hands out raises. The ``sync`` verb
that applies these proposals is a later card.

Kanban lives at ``$HERMES_HOME/kanban/boards/<slug>/kanban.db``; columns are
``triage, todo, ready, running, blocked, review, done, archived, scheduled``.
"""

from __future__ import annotations

import contextlib
import os
import re
import shutil
import sqlite3
import tempfile
from pathlib import Path
from typing import Callable, Iterator, Mapping, NamedTuple, Optional, Sequence, Union


class KanbanReadError(Exception):
    """Board missing, an unsafe board slug, or a tasks table this reader refuses."""


class Card(NamedTuple):
    """The parts of a kanban card the store cares about."""

    board: str
    id: str
    title: str
    status: str
    body: Optional[str]
    assignee: Optional[str]


class ParsedLine(NamedTuple):
    """One card's ``product:`` lines: refs in first-seen order, then the bad tokens."""

    refs: list[str]
    invalid: list[str]


class Proposal(NamedTuple):
    """A link the ``sync`` verb would create: board + card + entry ref + status."""

    board: str
    card_id: str
    ref: str
    execution_status: Optional[str]


# ---------------------------------------------------------------- the product line
# Anchored at line start (after optional whitespace), key case-insensitive. Prose
# that merely mentions "product:" mid-sentence is never a mapping.
_PRODUCT_LINE_RE = re.compile(r"^\s*product\s*:\s*(.*)$", re.IGNORECASE)

# A ref token: uppercase prefix + digits, no leading zero (ids start at 1), plus
# the ``F:<slug>`` form. The slug shape mirrors model.FEATURE_SLUG_RE.
_REF_TOKEN_RE = re.compile(r"^(R|A|F|S|DOC)([1-9]\d*)$")
_SLUG_TOKEN_RE = re.compile(r"^F:[a-z0-9][a-z0-9-]{0,39}$")

# A fenced code block: lines inside it are examples, never mappings. Toggled by the
# matching marker so ``` and ~~~ blocks both work.
_FENCE_RE = re.compile(r"^\s*(```|~~~)")


def _is_ref(token: str) -> bool:
    return bool(_REF_TOKEN_RE.match(token) or _SLUG_TOKEN_RE.match(token))


def parse_product_line(body: Optional[str]) -> ParsedLine:
    """The refs named by ``product:`` lines of a card body.

    Any number of lines; tokens are separated by commas and/or whitespace. Valid
    tokens are ``R<n>``, ``A<n>``, ``F<n>``, ``S<n>``, ``DOC<n>`` (digits, no
    leading zero) and ``F:<slug>``; valid ones are kept in first-appearance order
    without duplicates, every other token is returned in ``invalid``. Lines inside
    fenced code blocks are ignored.
    """
    refs: list[str] = []
    invalid: list[str] = []
    if not body:
        return ParsedLine(refs, invalid)
    seen: dict[str, None] = {}
    fence: Optional[str] = None
    for line in str(body).splitlines():
        fence_match = _FENCE_RE.match(line)
        if fence_match:
            marker = fence_match.group(1)
            if fence is None:
                fence = marker
            elif fence == marker:
                fence = None
            continue
        if fence is not None:
            continue
        match = _PRODUCT_LINE_RE.match(line)
        if match is None:
            continue
        for token in re.split(r"[,\s]+", match.group(1).strip()):
            if not token:
                continue
            if not _is_ref(token):
                invalid.append(token)
                continue
            if token not in seen:
                seen[token] = None
                refs.append(token)
    return ParsedLine(refs, invalid)


# ------------------------------------------------------------------ column mapping
#: S5 default column -> execution status. ``archived`` maps to nothing at all.
DEFAULT_STATUS_MAPPING: Mapping[str, Optional[str]] = {
    "triage": "approved",
    "todo": "approved",
    "ready": "approved",
    "scheduled": "approved",
    "blocked": "approved",
    "running": "in_progress",
    "review": "implemented",
    "done": "implemented",
    "archived": None,
}

#: How far along the execution chain a mapped status is, for the A20 warning.
#: ``verified`` is not in this table on purpose: no card reaches it.
STAGE_ORDER: Mapping[str, int] = {"approved": 0, "in_progress": 1, "implemented": 2}


def map_card_status(status: str,
                    mapping: Optional[Mapping[str, Optional[str]]] = None
                    ) -> Optional[str]:
    """The execution status a board column drives, or None for no change.

    The default mapping is S5's; *mapping* replaces only the entries it names.
    Nothing here returns ``verified`` — that status comes only from criteria with
    evidence (A19).
    """
    table: dict[str, Optional[str]] = dict(DEFAULT_STATUS_MAPPING)
    if mapping:
        table.update(mapping)
    return table.get(status)


# -------------------------------------------------------------------- board access
def _hermes_home() -> Path:
    env = os.environ.get("HERMES_HOME")
    return Path(env).expanduser() if env else Path.home() / ".hermes"


def _board_path(board: str,
                hermes_home: Optional[Union[str, Path]] = None) -> tuple[str, Path]:
    """``(clean slug, db path)``; a missing board raises naming the path it looked at."""
    clean = str(board or "").strip()
    if (not clean or clean == "." or ".." in clean or "/" in clean
            or "\\" in clean or os.sep in clean):
        raise KanbanReadError(f"board {str(board)!r} is not a plain board name")
    root = Path(hermes_home).expanduser() if hermes_home is not None else _hermes_home()
    path = root / "kanban" / "boards" / clean / "kanban.db"
    if not path.is_file():
        raise KanbanReadError(f"board {clean!r} not found: {path}")
    return clean, path


@contextlib.contextmanager
def board_copy(board: str, *,
               hermes_home: Optional[Union[str, Path]] = None,
               ) -> Iterator[sqlite3.Connection]:
    """Yield a read-only connection to a private temp COPY of one board.

    The live db is opened only to run SQLite's ``.backup`` into the copy; the
    connection yielded here is ``mode=ro`` + ``PRAGMA query_only = 1``, so a write
    attempt raises. The temp copy is removed when the block exits.
    """
    _clean, live = _board_path(board, hermes_home)
    directory = Path(tempfile.mkdtemp(prefix="product-kanban-"))
    target = directory / "kanban.db"
    try:
        # Read-only source. Opening a WAL-mode db ``mode=ro`` makes SQLite create
        # its -shm/-wal bookkeeping files in the board directory; when the board has
        # no WAL at all, read it ``immutable=1`` so this reader creates no file.
        has_wal = Path(f"{live}-wal").exists() or Path(f"{live}-shm").exists()
        uris = [f"file:{live}?mode=ro"] if has_wal else [
            f"file:{live}?mode=ro&immutable=1", f"file:{live}?mode=ro",
        ]
        source: Optional[sqlite3.Connection] = None
        failure: Optional[sqlite3.Error] = None
        for uri in uris:
            try:
                source = sqlite3.connect(uri, uri=True)
                break
            except sqlite3.Error as exc:
                failure = exc
        if source is None:
            raise KanbanReadError(f"board {board!r} is unreadable: {failure}")
        try:
            destination = sqlite3.connect(str(target))
            try:
                source.backup(destination)
            finally:
                destination.close()
        except sqlite3.Error as exc:
            raise KanbanReadError(f"board {board!r} is unreadable: {exc}") from exc
        finally:
            source.close()
        try:
            con = sqlite3.connect(f"file:{target}?mode=ro", uri=True)
        except sqlite3.Error as exc:
            raise KanbanReadError(f"board {board!r} copy is unreadable: {exc}") from exc
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA query_only = 1")
        try:
            yield con
        finally:
            con.close()
    finally:
        shutil.rmtree(directory, ignore_errors=True)


#: columns the reader needs; body and assignee are read when the board has them
_REQUIRED_COLUMNS = ("id", "title", "status")


def read_board_cards(board: str, *,
                     hermes_home: Optional[Union[str, Path]] = None) -> list[Card]:
    """Every card of one board, in id order, read from a read-only ``.backup`` copy.

    Raises ``KanbanReadError`` when the board (or its ``tasks`` table) is missing.
    """
    with board_copy(board, hermes_home=hermes_home) as con:
        try:
            columns = {row["name"] for row in con.execute("PRAGMA table_info(tasks)")}
            if not columns:
                raise KanbanReadError(f"board {board!r}: schema not understood (no tasks table)")
            missing = [name for name in _REQUIRED_COLUMNS if name not in columns]
            if missing:
                raise KanbanReadError(
                    f"board {board!r}: schema not understood (tasks is missing"
                    f" {', '.join(missing)})"
                )
            select = ["id", "title", "status"]
            select += [name for name in ("body", "assignee") if name in columns]
            rows = con.execute(f"SELECT {', '.join(select)} FROM tasks ORDER BY id").fetchall()
        except sqlite3.Error as exc:
            raise KanbanReadError(f"board {board!r} is unreadable: {exc}") from exc
    return [
        Card(
            board=board,
            id=row["id"],
            title=row["title"] or "",
            status=row["status"] or "",
            body=row["body"] if "body" in columns else None,
            assignee=row["assignee"] if "assignee" in columns else None,
        )
        for row in rows
    ]


# ---------------------------------------------------------------------- proposals
def propose_links(cards: Sequence[Card],
                  exists: Callable[[str], bool],
                  ) -> tuple[list[Proposal], list[tuple[str, str, str]]]:
    """Propose link rows from the ``product:`` lines; write nothing.

    *exists(ref)* decides whether a ref resolves in the store. Returns
    ``(proposals, unresolved)``: proposals carry the card's mapped execution
    status, unresolved entries are ``(board, card_id, ref)``. Order is
    deterministic: board, then card id, then the ref order of the line.
    """
    proposals: list[Proposal] = []
    unresolved: list[tuple[str, str, str]] = []
    for card in sorted(cards, key=lambda one: (str(one.board), str(one.id))):
        status = map_card_status(card.status)
        for ref in parse_product_line(card.body).refs:
            if exists(ref):
                proposals.append(Proposal(card.board, card.id, ref, status))
            else:
                unresolved.append((card.board, card.id, ref))
    return proposals, unresolved


# ------------------------------------------------------------------------- A20
def status_warning(entry_status: str, previous_card_status: Optional[str],
                   new_card_status: str) -> Optional[str]:
    """A warning when a *verified* entry's card moves to a lower stage, else None.

    S5/A20: the card is evidence, not proof — the entry keeps its status and the
    user is told instead.
    """
    if entry_status != "verified" or previous_card_status is None:
        return None
    previous = map_card_status(previous_card_status)
    new = map_card_status(new_card_status)
    if previous is None or new is None:
        return None
    if STAGE_ORDER[new] >= STAGE_ORDER[previous]:
        return None
    return (
        f"warning: the entry is verified and its card moved backward"
        f" ({previous_card_status} -> {new_card_status}: {previous} -> {new});"
        f" the entry is unchanged"
    )
