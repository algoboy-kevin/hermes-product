"""HP-27: ``sync`` ends with a read-only ``reconcile`` block.

Covered here, against FAKE boards built under a throwaway ``$HERMES_HOME``:

* a feature whose linked cards all map to ``implemented`` while its status is
  below ``shipped`` -> one ``reconcile F<n>`` line (report only, no move);
* an open card (``running``), an already ``shipped`` feature, and a card whose
  column maps to nothing (``archived``) print nothing — the last one is ignored
  rather than counted;
* a requirement whose tracking cards are all done while a criterion is neither
  ``passed`` nor ``waived`` -> one ``reconcile R<n>`` line naming
  ``criteria <passed>/<total> passed``; all passed (or waived) prints nothing;
* the lines come after the existing ones, follow the same REF order, and a
  project with no qualifying entry prints byte-identical output to before;
* the live board file is never written and no status moves because of the block.

The real ``~/.hermes`` and the real boards are never touched.
"""

from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path

import store
from _helpers import (  # noqa: F401  (fixtures imported for discovery)
    add_criterion, add_feature, add_requirement, cli, con, home, raw_update,
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


def make_board(home: Path, slug: str, cards: list[dict]) -> Path:
    """Build a fake board db with the real ``tasks`` columns and the given cards."""
    path = home / "hermes-home" / "kanban" / "boards" / slug / "kanban.db"
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


def new_project(slug: str = "shop") -> str:
    store.create_project(slug, slug.title())
    return slug


def card(card_id: str, status: str, refs: str, title: str = "Card") -> dict:
    return {"id": card_id, "title": title, "status": status, "body": f"product: {refs}"}


def run_sync(capsys, *argv: str) -> tuple[int, str, str]:
    return cli(capsys, "sync", "--project", "shop", *argv)


def reconcile_lines(out: str) -> list[str]:
    return [line for line in out.splitlines() if line.startswith("reconcile")]


def status_of(kind: str, entity_id: int, project: str = "shop") -> str:
    connection = store.connect()
    try:
        row = connection.execute(
            f"SELECT status FROM {store.ENTITY_TABLE[kind]} WHERE project = ? AND id = ?",
            (project, entity_id)).fetchone()
    finally:
        connection.close()
    return row["status"]


# ------------------------------------------------------------ feature: all done
def test_feature_with_every_card_done_and_status_below_shipped_reconciles(
        home: Path, con, capsys) -> None:
    new_project()
    add_feature("shop", title="Bulk import")
    raw_update(con, "shop", "feature", 1, status="in_progress")
    make_board(home, "b", [card("t_1", "done", "F1"), card("t_2", "review", "F1")])
    rc, out, err = run_sync(capsys, "--board", "b")
    assert rc == 0, err
    assert reconcile_lines(out) == [
        "reconcile F1  in_progress, 2/2 cards done: ready to ship? (owner decides)"]
    # after the existing lines, before the dry-run trailer
    assert out.splitlines()[-2] == \
        "reconcile F1  in_progress, 2/2 cards done: ready to ship? (owner decides)"
    assert out.splitlines()[-1] == "proposal only: re-run with --apply to write"


# ------------------------------------------------------------------ open card
def test_one_open_card_prints_nothing(home: Path, con, capsys) -> None:
    new_project()
    add_feature("shop", title="Bulk import")
    raw_update(con, "shop", "feature", 1, status="in_progress")
    make_board(home, "b", [card("t_1", "done", "F1"), card("t_2", "running", "F1")])
    rc, out, err = run_sync(capsys, "--board", "b")
    assert rc == 0, err
    assert reconcile_lines(out) == []


# --------------------------------------------------------------- already shipped
def test_a_shipped_feature_prints_nothing(home: Path, con, capsys) -> None:
    new_project()
    add_feature("shop", title="Bulk import")
    raw_update(con, "shop", "feature", 1, status="shipped")
    make_board(home, "b", [card("t_1", "done", "F1"), card("t_2", "done", "F1")])
    rc, out, err = run_sync(capsys, "--board", "b")
    assert rc == 0, err
    assert reconcile_lines(out) == []


# ------------------------------------------------------------ requirement: criteria
def test_requirement_with_a_done_card_and_one_unpassed_criterion_reconciles(
        home: Path, con, capsys) -> None:
    new_project()
    add_requirement("shop", title="Bulk import")
    add_criterion("shop", requirement=1)
    add_criterion("shop", requirement=1, text="Given a file, then rows land")
    raw_update(con, "shop", "criterion", 1, status="passed", evidence="test_bulk")
    make_board(home, "b", [card("t_1", "done", "R1")])
    rc, out, err = run_sync(capsys, "--board", "b")
    assert rc == 0, err
    assert reconcile_lines(out) == [
        "reconcile R1  tracked by 1 card, all done; criteria 1/2 passed:"
        " run the criteria"]
    assert status_of("criterion", 2) == "not_run"


def test_all_criteria_passed_or_waived_prints_nothing(home: Path, con, capsys) -> None:
    new_project()
    add_requirement("shop", title="All passed")
    add_criterion("shop", requirement=1)
    add_criterion("shop", requirement=1, text="Given a file, then rows land")
    for criterion_id in (1, 2):
        raw_update(con, "shop", "criterion", criterion_id, status="passed",
                   evidence="test_bulk")
    add_requirement("shop", title="One waived")
    add_criterion("shop", requirement=2)
    add_criterion("shop", requirement=2, text="Given a file, then rows land")
    raw_update(con, "shop", "criterion", 3, status="passed", evidence="test_bulk")
    raw_update(con, "shop", "criterion", 4, status="waived", evidence="not shipped")
    make_board(home, "b", [card("t_1", "done", "R1, R2")])
    rc, out, err = run_sync(capsys, "--board", "b")
    assert rc == 0, err
    assert reconcile_lines(out) == []


# ---------------------------------------------------------------- archived cards
def test_an_archived_card_is_ignored_not_counted(home: Path, con, capsys) -> None:
    new_project()
    add_feature("shop", title="Bulk import")
    add_feature("shop", title="Only archived")
    raw_update(con, "shop", "feature", 1, status="in_progress")
    raw_update(con, "shop", "feature", 2, status="in_progress")
    make_board(home, "b", [
        card("t_1", "done", "F1"),
        card("t_2", "archived", "F1"),
        card("t_3", "archived", "F2"),
    ])
    rc, out, err = run_sync(capsys, "--board", "b")
    assert rc == 0, err
    assert reconcile_lines(out) == [
        "reconcile F1  in_progress, 1/1 cards done: ready to ship? (owner decides)"]


# ------------------------------------------------------------------ no block at all
def test_no_qualifying_entry_leaves_the_output_byte_identical(home: Path, con,
                                                              capsys) -> None:
    new_project()
    add_feature("shop", title="Bulk import")
    make_board(home, "b", [card("t_1", "running", "F1")])
    rc, out, err = run_sync(capsys, "--board", "b")
    assert rc == 0, err
    assert out == "\n".join([
        "sync (shop): 1 boards, 1 cards read, 1 with product lines",
        "link   F1  kanban:b/t_1  implements  new",
        "status F1  planned -> in_progress  proposed",
        "proposal only: re-run with --apply to write",
    ]) + "\n"


# ----------------------------------------------------------------------- ordering
def test_the_block_follows_the_sync_ref_order(home: Path, con, capsys) -> None:
    new_project()
    add_feature("shop", title="First")
    add_feature("shop", title="Second")
    add_requirement("shop", title="Tracked")
    add_criterion("shop", requirement=1)
    raw_update(con, "shop", "feature", 1, status="in_progress")
    raw_update(con, "shop", "feature", 2, status="in_progress")
    make_board(home, "b", [card("t_1", "done", "F2, R1, F1")])
    rc, out, err = run_sync(capsys, "--board", "b")
    assert rc == 0, err
    assert [line.split()[1] for line in reconcile_lines(out)] == ["R1", "F1", "F2"]


# ------------------------------------------------------------- report only, no writes
def test_the_block_writes_nothing_and_the_live_board_is_untouched(
        home: Path, con, capsys) -> None:
    new_project()
    add_feature("shop", title="Bulk import")
    raw_update(con, "shop", "feature", 1, status="in_progress")
    live = make_board(home, "b", [card("t_1", "done", "F1")])
    before = (hashlib.sha256(live.read_bytes()).hexdigest(), live.stat().st_size,
              live.stat().st_mtime_ns)
    rc, out, err = run_sync(capsys, "--board", "b", "--apply")
    assert rc == 0, err
    after = (hashlib.sha256(live.read_bytes()).hexdigest(), live.stat().st_size,
             live.stat().st_mtime_ns)
    assert before == after
    assert reconcile_lines(out) == [
        "reconcile F1  in_progress, 1/1 cards done: ready to ship? (owner decides)"]
    assert status_of("feature", 1) == "in_progress"
