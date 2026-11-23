"""HP-12: ``sync`` proposes external links from kanban cards and applies them on request.

Covered here, against FAKE boards built under a throwaway ``$HERMES_HOME``:

* A19: a card in ``done`` moves a spec to ``implemented`` and never to ``verified``;
  a move the store refuses is one ``refused`` line and exit 3, and the links and
  the other moves still land;
* A20: a ``verified`` spec whose card is not finished warns and stays unchanged
  (and a ``done`` card does not warn);
* dry run is the default: no rows, no moves, and the trailer line;
* idempotency: a second ``--apply`` reports every link ``exists`` and every move
  ``unchanged``;
* the furthest-stage rule across two cards on one entry, the feature mapping
  (``approved`` -> no-op, ``in_progress`` forward, ``done`` never ships), link-only
  R and A with their roles, a DOC ref and an unknown ref unresolved, a card with
  no product line ignored, ``--map`` overrides and ``--map x=verified`` refused,
  backward/equal moves ``unchanged``, the line order, two boards, a missing board
  writing nothing, the board file never written, ``--project`` and an unknown
  project.

The real ``~/.hermes`` and the real boards are never touched.
"""

from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path

import pytest

import store
from _helpers import (  # noqa: F401  (fixtures imported for discovery)
    add_criterion, add_doc, add_feature, add_requirement, add_spec, cli, con, home,
    link_feature_requirement, link_spec_requirement, parser, raw_update,
)

TASKS_DDL = """
CREATE TABLE IF NOT EXISTS tasks (
  id             TEXT PRIMARY KEY,
  title          TEXT NOT NULL,
  body           TEXT,
  assignee       TEXT,
  status         TEXT NOT NULL,
  priority       INTEGER NOT NULL DEFAULT 0,
  created_by     TEXT,
  created_at     INTEGER NOT NULL,
  started_at     INTEGER,
  completed_at   INTEGER,
  workspace_kind TEXT NOT NULL DEFAULT 'scratch'
);
"""


def board_path(home: Path, slug: str) -> Path:
    """The fake board's db, exactly where ``kanban_reader`` looks for it."""
    return home / "hermes-home" / "kanban" / "boards" / slug / "kanban.db"


def make_board(home: Path, slug: str, cards: list[dict]) -> Path:
    """Build a board db with the real ``tasks`` columns and the given cards."""
    path = board_path(home, slug)
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(str(path))
    try:
        connection.executescript(TASKS_DDL)
        for index, card in enumerate(cards):
            connection.execute(
                "INSERT INTO tasks(id, title, body, assignee, status, priority, created_at)"
                " VALUES (?,?,?,?,?,?,?)",
                (card["id"], card["title"], card.get("body"), card.get("assignee"),
                 card["status"], card.get("priority", 0), card.get("created_at", 1000 + index)),
            )
        connection.commit()
    finally:
        connection.close()
    return path


@pytest.fixture()
def shop(home: Path, con) -> str:  # noqa: F811  (the `home` fixture sets HERMES_HOME)
    """A project with six specs and four features in the states sync reasons about."""
    store.create_project("shop", "Shop")
    add_requirement("shop", title="Pay order", statement="A buyer can pay", priority="must")
    add_criterion("shop", requirement=1)
    for title in ("Stripe", "Extras", "Third", "Fourth"):
        add_feature("shop", title=title, layer="core")
    for title in ("Payments", "Refunds", "Audit", "Ledger", "Webhooks", "Reports"):
        add_spec("shop", title=title, summary=f"{title} summary")
    add_doc("shop", title="Notes", kind="design")
    link_feature_requirement(con, "shop", 1, 1)
    for spec_id in (1, 2, 3, 4, 5, 6):
        link_spec_requirement(con, "shop", spec_id, 1)
    raw_update(con, "shop", "spec", 1, status="approved")        # S1: -> implemented
    raw_update(con, "shop", "spec", 3, status="verified")        # S3: A20 warning
    raw_update(con, "shop", "spec", 4, status="verified")        # S4: verified + done
    raw_update(con, "shop", "spec", 5, status="approved")        # S5: two cards
    raw_update(con, "shop", "spec", 6, status="implemented")     # S6: equal/backward
    raw_update(con, "shop", "feature", 3, status="in_progress")  # F3: was in progress
    raw_update(con, "shop", "feature", 4, status="in_progress")  # F4: done never ships
    return "shop"


def run_sync(capsys, *argv: str) -> tuple[int, str, str]:
    return cli(capsys, "sync", "--project", "shop", *argv)


def status_of(kind: str, entity_id: int, project: str = "shop") -> str:
    connection = store.connect()
    try:
        row = connection.execute(
            f"SELECT status FROM {store.ENTITY_TABLE[kind]} WHERE project = ? AND id = ?",
            (project, entity_id)).fetchone()
    finally:
        connection.close()
    return row["status"]


def external_rows(project: str = "shop") -> list[tuple]:
    connection = store.connect()
    try:
        return [tuple(row) for row in connection.execute(
            "SELECT entity_type, entity_id, system, external_id, url, role"
            " FROM external_link WHERE project = ? ORDER BY entity_type, entity_id, external_id",
            (project,))]
    finally:
        connection.close()


# ------------------------------------------------------------------ A19 (Done card)
def test_a19_done_card_marks_a_spec_implemented_not_verified(home: Path, shop: str,
                                                             capsys) -> None:
    make_board(home, "b", [{"id": "t_1", "title": "Pay", "status": "done",
                            "body": "product: S1"}])
    rc, out, err = run_sync(capsys, "--board", "b", "--apply")
    assert rc == 0, err
    assert "link   S1  kanban:b/t_1  implements  new" in out.splitlines()
    assert "status S1  approved -> implemented  applied" in out.splitlines()
    assert status_of("spec", 1) == "implemented"


def test_a19_a_refused_move_is_one_line_and_exit_three(home: Path, shop: str,
                                                       capsys) -> None:
    make_board(home, "b", [{"id": "t_1", "title": "Refunds", "status": "done",
                            "body": "product: S2"}])
    rc, out, err = run_sync(capsys, "--board", "b", "--apply")
    assert rc == 3
    assert ("status S2  draft -> implemented  refused: cannot move S2 draft ->"
            " implemented: go through approved first") in out.splitlines()
    assert status_of("spec", 2) == "draft"
    # the link landed even though the move was refused
    assert ("spec", 2, "kanban", "b/t_1", None, "implements") in external_rows()


# ----------------------------------------------------------------- A20 (verified)
def test_a20_verified_with_a_card_in_ready_warns_and_changes_nothing(
        home: Path, shop: str, capsys) -> None:
    make_board(home, "b", [{"id": "t_1", "title": "Audit", "status": "ready",
                            "body": "product: S3"}])
    rc, out, err = run_sync(capsys, "--board", "b", "--apply")
    assert rc == 0, err
    assert ("warning: S3 is verified but card b/t_1 is in ready (maps to approved);"
            " entry unchanged") in out.splitlines()
    assert not any(line.startswith("status S3") for line in out.splitlines())
    assert status_of("spec", 3) == "verified"


def test_a20_verified_with_a_card_in_running_also_warns(home: Path, shop: str,
                                                        capsys) -> None:
    make_board(home, "b", [{"id": "t_1", "title": "Audit", "status": "running",
                            "body": "product: S3"}])
    rc, out, err = run_sync(capsys, "--board", "b", "--apply")
    assert rc == 0, err
    assert ("warning: S3 is verified but card b/t_1 is in running (maps to"
            " in_progress); entry unchanged") in out.splitlines()
    assert status_of("spec", 3) == "verified"


def test_a20_no_warning_when_the_verified_card_is_done(home: Path, shop: str,
                                                       capsys) -> None:
    make_board(home, "b", [{"id": "t_1", "title": "Ledger", "status": "done",
                            "body": "product: S4"}])
    rc, out, err = run_sync(capsys, "--board", "b", "--apply")
    assert rc == 0, err
    assert not any(line.startswith("warning:") for line in out.splitlines())
    assert not any(line.startswith("status S4") for line in out.splitlines())
    assert status_of("spec", 4) == "verified"


# ------------------------------------------------------------------- dry run (default)
def test_dry_run_is_the_default_and_writes_nothing(home: Path, shop: str,
                                                   capsys) -> None:
    make_board(home, "b", [{"id": "t_1", "title": "Pay", "status": "done",
                            "body": "product: S1"}])
    rc, out, err = run_sync(capsys, "--board", "b")
    assert rc == 0, err
    assert "link   S1  kanban:b/t_1  implements  new" in out.splitlines()
    assert "status S1  approved -> implemented  proposed" in out.splitlines()
    assert out.splitlines()[-1] == "proposal only: re-run with --apply to write"
    assert external_rows() == []
    assert status_of("spec", 1) == "approved"


# --------------------------------------------------------------------- idempotency
def test_links_are_idempotent_and_second_apply_changes_nothing(home: Path, shop: str,
                                                              capsys) -> None:
    make_board(home, "b", [{"id": "t_1", "title": "Pay", "status": "done",
                            "body": "product: S1, R1"}])
    assert run_sync(capsys, "--board", "b", "--apply")[0] == 0
    rows = external_rows()
    rc, out, err = run_sync(capsys, "--board", "b", "--apply")
    assert rc == 0, err
    assert "link   S1  kanban:b/t_1  implements  exists" in out.splitlines()
    assert "link   R1  kanban:b/t_1  tracks  exists" in out.splitlines()
    assert "status S1  implemented -> implemented  unchanged" in out.splitlines()
    assert external_rows() == rows
    assert status_of("spec", 1) == "implemented"


# ------------------------------------------------------------------ furthest stage
def test_the_furthest_stage_wins_between_two_cards(home: Path, shop: str,
                                                   capsys) -> None:
    make_board(home, "b", [
        {"id": "t_1", "title": "Ready", "status": "ready", "body": "product: S5"},
        {"id": "t_2", "title": "Done", "status": "done", "body": "product: S5"},
    ])
    rc, out, err = run_sync(capsys, "--board", "b", "--apply")
    assert rc == 0, err
    statuses = [line for line in out.splitlines() if line.startswith("status S5")]
    assert statuses == ["status S5  approved -> implemented  applied"]
    assert status_of("spec", 5) == "implemented"


# ----------------------------------------------------------------- feature mapping
def test_feature_mapping_approved_is_a_no_op(home: Path, shop: str, capsys) -> None:
    make_board(home, "b", [{"id": "t_1", "title": "Stripe", "status": "ready",
                            "body": "product: F1"}])
    rc, out, err = run_sync(capsys, "--board", "b", "--apply")
    assert rc == 0, err
    assert "status F1  planned -> planned  unchanged" in out.splitlines()
    assert status_of("feature", 1) == "planned"


def test_feature_mapping_in_progress_is_forward(home: Path, shop: str, capsys) -> None:
    make_board(home, "b", [{"id": "t_1", "title": "Extras", "status": "running",
                            "body": "product: F2"}])
    rc, out, err = run_sync(capsys, "--board", "b", "--apply")
    assert rc == 0, err
    assert "status F2  planned -> in_progress  applied" in out.splitlines()
    assert status_of("feature", 2) == "in_progress"


def test_feature_done_never_ships(home: Path, shop: str, capsys) -> None:
    make_board(home, "b", [{"id": "t_1", "title": "Fourth", "status": "done",
                            "body": "product: F4"}])
    rc, out, err = run_sync(capsys, "--board", "b", "--apply")
    assert rc == 0, err
    assert not any(line.startswith("status F4") for line in out.splitlines())
    assert status_of("feature", 4) == "in_progress"


# ------------------------------------------------------------- link-only R and A
def test_requirement_and_criterion_are_link_only(home: Path, shop: str,
                                                 capsys) -> None:
    make_board(home, "b", [{"id": "t_1", "title": "Pay", "status": "running",
                            "body": "product: R1, A1"}])
    rc, out, err = run_sync(capsys, "--board", "b", "--apply")
    assert rc == 0, err
    assert "link   R1  kanban:b/t_1  tracks  new" in out.splitlines()
    assert "link   A1  kanban:b/t_1  tracks  new" in out.splitlines()
    assert not any(line.startswith("status R1") for line in out.splitlines())
    assert not any(line.startswith("status A1") for line in out.splitlines())
    assert status_of("requirement", 1) == "draft"
    assert status_of("criterion", 1) == "not_run"
    assert ("requirement", 1, "kanban", "b/t_1", None, "tracks") in external_rows()


def test_doc_ref_is_unresolved_because_a_doc_carries_no_card(home: Path, shop: str,
                                                             capsys) -> None:
    make_board(home, "b", [{"id": "t_1", "title": "Notes", "status": "todo",
                            "body": "product: DOC1"}])
    rc, out, err = run_sync(capsys, "--board", "b", "--apply")
    assert rc == 0, err
    assert "unresolved  b/t_1  DOC1  DOC cannot carry a card link" in out.splitlines()
    assert external_rows() == []


def test_unknown_ref_is_unresolved_not_an_error(home: Path, shop: str, capsys) -> None:
    make_board(home, "b", [{"id": "t_1", "title": "Ghost", "status": "todo",
                            "body": "product: F99"}])
    rc, out, err = run_sync(capsys, "--board", "b", "--apply")
    assert rc == 0, err
    unresolved = [line for line in out.splitlines() if line.startswith("unresolved")]
    assert len(unresolved) == 1
    assert unresolved[0].startswith("unresolved  b/t_1  F99  ")
    assert "does not exist" in unresolved[0]
    assert external_rows() == []


def test_card_without_a_product_line_is_ignored_and_counted(home: Path, shop: str,
                                                            capsys) -> None:
    make_board(home, "b", [
        {"id": "t_1", "title": "Pay", "status": "running", "body": "product: S1"},
        {"id": "t_2", "title": "No mapping", "status": "running", "body": "fix the bug"},
    ])
    rc, out, err = run_sync(capsys, "--board", "b")
    assert rc == 0, err
    assert out.splitlines()[0] == "sync (shop): 1 boards, 2 cards read, 1 with product lines"
    assert not any("b/t_2" in line for line in out.splitlines())


# ------------------------------------------------------------------------ --map
def test_map_override_moves_the_column(home: Path, shop: str, capsys) -> None:
    make_board(home, "b", [{"id": "t_1", "title": "Pay", "status": "ready",
                            "body": "product: S1"}])
    rc, out, err = run_sync(capsys, "--board", "b", "--map", "ready=implemented",
                            "--apply")
    assert rc == 0, err
    assert "status S1  approved -> implemented  applied" in out.splitlines()
    assert status_of("spec", 1) == "implemented"


def test_map_verified_is_refused(home: Path, shop: str, capsys) -> None:
    make_board(home, "b", [{"id": "t_1", "title": "Pay", "status": "done",
                            "body": "product: S1"}])
    rc, out, err = run_sync(capsys, "--board", "b", "--map", "done=verified", "--apply")
    assert rc == 1
    assert err.strip() == ("nothing maps to verified: that status comes only from"
                           " criteria with evidence")
    assert external_rows() == []
    assert status_of("spec", 1) == "approved"


def test_map_unknown_column_is_accepted(home: Path, shop: str, capsys) -> None:
    make_board(home, "b", [{"id": "t_1", "title": "Extras", "status": "launched",
                            "body": "product: F2"}])
    rc, out, err = run_sync(capsys, "--board", "b", "--map", "launched=in_progress",
                            "--apply")
    assert rc == 0, err
    assert "status F2  planned -> in_progress  applied" in out.splitlines()


def test_map_none_means_no_change(home: Path, shop: str, capsys) -> None:
    make_board(home, "b", [{"id": "t_1", "title": "Extras", "status": "running",
                            "body": "product: F2"}])
    rc, out, err = run_sync(capsys, "--board", "b", "--map", "running=none", "--apply")
    assert rc == 0, err
    assert not any(line.startswith("status F2") for line in out.splitlines())
    assert status_of("feature", 2) == "planned"


def test_map_with_an_unknown_status_names_the_choices(home: Path, shop: str,
                                                      capsys) -> None:
    make_board(home, "b", [{"id": "t_1", "title": "Pay", "status": "done",
                            "body": "product: S1"}])
    rc, out, err = run_sync(capsys, "--board", "b", "--map", "done=shipped")
    assert rc == 1
    assert "approved" in err and "in_progress" in err and "implemented" in err


# ------------------------------------------------------------------ equal/backward
def test_backward_move_is_unchanged(home: Path, shop: str, capsys) -> None:
    make_board(home, "b", [{"id": "t_1", "title": "Reports", "status": "ready",
                            "body": "product: S6"}])
    rc, out, err = run_sync(capsys, "--board", "b", "--apply")
    assert rc == 0, err
    assert "status S6  implemented -> approved  unchanged" in out.splitlines()
    assert status_of("spec", 6) == "implemented"


def test_equal_move_is_unchanged(home: Path, shop: str, capsys) -> None:
    make_board(home, "b", [{"id": "t_1", "title": "Reports", "status": "review",
                            "body": "product: S6"}])
    rc, out, err = run_sync(capsys, "--board", "b", "--apply")
    assert rc == 0, err
    assert "status S6  implemented -> implemented  unchanged" in out.splitlines()


# ---------------------------------------------------------------------- ordering
def test_lines_are_ordered_links_then_statuses_then_warnings_then_unresolved(
        home: Path, shop: str, capsys) -> None:
    make_board(home, "b", [{"id": "t_1", "title": "Mixed", "status": "running",
                            "body": "product: S2, F1, A1, R1, DOC1"}])
    rc, out, err = run_sync(capsys, "--board", "b")
    assert rc == 0, err
    lines = out.splitlines()
    prefixes = [line.split()[0] for line in lines[1:-1]]
    assert prefixes == ["link", "link", "link", "link", "status", "status",
                        "unresolved"]
    links = [line for line in lines if line.startswith("link")]
    assert [line.split()[1] for line in links] == ["R1", "A1", "F1", "S2"]


# ----------------------------------------------------------------------- boards
def test_two_boards_are_read_together(home: Path, shop: str, capsys) -> None:
    make_board(home, "a", [{"id": "t_1", "title": "One", "status": "running",
                            "body": "product: F1"}])
    make_board(home, "b", [{"id": "t_1", "title": "Two", "status": "running",
                            "body": "product: F1"}])
    rc, out, err = run_sync(capsys, "--board", "a", "--board", "b", "--apply")
    assert rc == 0, err
    assert out.splitlines()[0] == "sync (shop): 2 boards, 2 cards read, 2 with product lines"
    assert "link   F1  kanban:a/t_1  implements  new" in out.splitlines()
    assert "link   F1  kanban:b/t_1  implements  new" in out.splitlines()
    assert len(external_rows()) == 2


def test_missing_board_exits_one_and_writes_nothing_for_any_board(
        home: Path, shop: str, capsys) -> None:
    make_board(home, "b", [{"id": "t_1", "title": "Pay", "status": "done",
                            "body": "product: S1"}])
    rc, out, err = run_sync(capsys, "--board", "b", "--board", "ghost", "--apply")
    assert rc == 1
    assert "ghost" in err and "kanban.db" in err
    assert external_rows() == []
    assert status_of("spec", 1) == "approved"


def test_the_live_board_file_is_never_written(home: Path, shop: str, capsys) -> None:
    live = make_board(home, "b", [{"id": "t_1", "title": "Pay", "status": "done",
                                   "body": "product: S1"}])
    before = (hashlib.sha256(live.read_bytes()).hexdigest(), live.stat().st_size,
              live.stat().st_mtime_ns)
    assert run_sync(capsys, "--board", "b", "--apply")[0] == 0
    after = (hashlib.sha256(live.read_bytes()).hexdigest(), live.stat().st_size,
             live.stat().st_mtime_ns)
    assert before == after


# ---------------------------------------------------------------------- the row
def test_the_external_link_row_shape(home: Path, shop: str, capsys) -> None:
    make_board(home, "b", [{"id": "t_7", "title": "Pay", "status": "running",
                            "body": "product: F1, S1"}])
    assert run_sync(capsys, "--board", "b", "--apply")[0] == 0
    assert external_rows() == [
        ("feature", 1, "kanban", "b/t_7", None, "implements"),
        ("spec", 1, "kanban", "b/t_7", None, "implements"),
    ]


# ---------------------------------------------------------------------- project
def test_project_flag_selects_the_project(home: Path, shop: str, con, capsys) -> None:
    store.create_project("other", "Other")
    make_board(home, "b", [{"id": "t_1", "title": "Pay", "status": "done",
                            "body": "product: S1"}])
    rc, out, err = cli(capsys, "sync", "--board", "b", "--project", "shop", "--apply")
    assert rc == 0, err
    assert len(external_rows("shop")) == 1
    assert external_rows("other") == []


def test_unknown_project_exits_one(home: Path, capsys) -> None:
    make_board(home, "b", [{"id": "t_1", "title": "Pay", "status": "done"}])
    rc, out, err = cli(capsys, "sync", "--board", "b", "--project", "nope")
    assert rc == 1
    assert "does not exist" in err


def test_board_flag_is_required() -> None:
    with pytest.raises(SystemExit) as excinfo:
        parser().parse_args(["sync"])
    assert excinfo.value.code == 2
