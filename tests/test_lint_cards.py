"""Lint tests (card HP-35): the workflow checks ``card_without_ref`` and
``completed_without_review``.

Product board = context, kanban = work; these two checks make the workflow gates
(HP-30, card-convention section 3) visible: a ``ready``/``running`` card that
cites no R#/F#/S# is flagged, and a card claimed and then completed after the
rule date (2026-11-25T09:00:00Z) with no ``review_requested`` event before the
completion is flagged. Exempt: triage/blocked (and any other column), a card
completed before the rule date, and a card never claimed (closed by the parent
without a run).

Every test that needs a board builds a synthetic ``kanban.db`` under a throwaway
HERMES_HOME with the real ``tasks``/``task_events`` columns (the shape
test_kanban_reader.py uses); the real store and board tree are never read.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

import kanban_reader
import lint
from _helpers import (  # `con` is a fixture imported for discovery
    add_external_link,
    add_requirement,
    cli,
    con,
    make_project,
)

RULE = lint.REVIEW_RULE_FROM

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

EVENTS_DDL = """
CREATE TABLE IF NOT EXISTS task_events (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  task_id    TEXT NOT NULL,
  run_id     INTEGER,
  kind       TEXT NOT NULL,
  payload    TEXT,
  created_at INTEGER NOT NULL
);
"""


@pytest.fixture()
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A throwaway HERMES_HOME; the real store and board tree are never read."""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes-home"))
    return tmp_path / "hermes-home"


# ------------------------------------------------------------------ board fixture
def make_board(home: Path, slug: str, cards: list[dict],
               events: list[tuple] | tuple = ()) -> Path:
    """Build a board db at ``<home>/kanban/boards/<slug>/kanban.db`` with cards
    ``[{id, title, body?, status}]`` and events ``[(task_id, kind, created_at)]``."""
    path = home / "kanban" / "boards" / slug / "kanban.db"
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(path))
    try:
        con.executescript(TASKS_DDL)
        con.executescript(EVENTS_DDL)
        for index, card in enumerate(cards):
            con.execute(
                "INSERT INTO tasks(id, title, body, assignee, status, priority,"
                " created_at) VALUES (?,?,?,?,?,?,?)",
                (card["id"], card["title"], card.get("body"), card.get("assignee"),
                 card["status"], card.get("priority", 0), card.get("created_at",
                                                                  1000 + index)),
            )
        for task_id, kind, created_at in events:
            con.execute(
                "INSERT INTO task_events(task_id, kind, created_at) VALUES (?,?,?)",
                (task_id, kind, created_at),
            )
        con.commit()
    finally:
        con.close()
    return path


# ------------------------------------------------------------- pure data builders
def card(id: str = "t_1", title: str = "HP-1 Widget", status: str = "ready",
         body: str | None = None) -> kanban_reader.Card:
    return kanban_reader.Card(board="dev", id=id, title=title, status=status,
                              body=body, assignee=None)


def event(task_id: str, kind: str, created_at: int, id: int = 1
          ) -> kanban_reader.CardEvent:
    return kanban_reader.CardEvent(task_id=task_id, id=id, kind=kind,
                                   created_at=created_at)


def by_flag(findings, flag: str) -> list:
    return [finding for finding in findings if finding.flag == flag]


def ref_message(title: str) -> str:
    return (f"{title} is ready or running and cites no R#/F#/S#;"
            " record it first (workflow step 3)")


def review_message(title: str) -> str:
    return f"{title} was completed without a review step (card-convention section 3)"


# ------------------------------------------------------------------ card_without_ref
def test_ready_card_without_a_ref_is_flagged() -> None:
    found = lint.card_without_ref([card(body="Just a note with no reference.")])
    assert found == [lint.Finding("card_without_ref", "warning", "t_1",
                                  ref_message("HP-1 Widget"))]


def test_ready_card_with_a_product_line_is_clean() -> None:
    assert lint.card_without_ref([card(body="KEY: HP-1\nproduct: R3, S2")]) == []
    assert lint.card_without_ref([card(body="product: F7")]) == []
    assert lint.card_without_ref([card(body="product: F:stripe")]) == []


def test_running_card_without_a_ref_is_flagged() -> None:
    found = lint.card_without_ref([card(status="running", body="no refs here")])
    assert [one.flag for one in found] == ["card_without_ref"]


def test_triage_and_blocked_cards_are_exempt() -> None:
    cards = [card(id="t_1", status="triage", body="nothing"),
             card(id="t_2", status="blocked", body="nothing"),
             card(id="t_3", status="review", body="nothing"),
             card(id="t_4", status="done", body="nothing")]
    assert lint.card_without_ref(cards) == []


def test_regex_fallback_finds_a_ref_in_the_first_ten_lines() -> None:
    body = "\n".join(["prose"] * 9 + ["and R3 was recorded there"])
    assert lint.card_without_ref([card(body=body)]) == []
    body_beyond = "\n".join(["prose"] * 11 + ["R3 appears only here, line 12"])
    found = lint.card_without_ref([card(body=body_beyond)])
    assert [one.flag for one in found] == ["card_without_ref"]


# ------------------------------------------------------- completed_without_review
def test_completed_after_the_rule_date_with_claim_and_no_review_is_flagged() -> None:
    events = [event("t_1", "claimed", RULE - 5), event("t_1", "completed", RULE)]
    found = lint.completed_without_review([card()], events)
    assert found == [lint.Finding("completed_without_review", "warning", "t_1",
                                  review_message("HP-1 Widget"))]


def test_review_requested_before_completed_is_clean() -> None:
    events = [event("t_1", "claimed", RULE - 20, id=1),
              event("t_1", "review_requested", RULE - 10, id=2),
              event("t_1", "completed", RULE, id=3)]
    assert lint.completed_without_review([card()], events) == []


def test_completed_before_the_rule_date_is_exempt() -> None:
    events = [event("t_1", "claimed", RULE - 30), event("t_1", "completed", RULE - 1)]
    assert lint.completed_without_review([card()], events) == []


def test_never_claimed_is_exempt() -> None:
    """Closed by the parent without a run: no claimed event, nothing to review."""
    events = [event("t_1", "completed", RULE + 10)]
    assert lint.completed_without_review([card()], events) == []


# --------------------------------------------------------------------- read wiring
def test_project_without_a_board_is_silent_for_both_checks(home, con, capsys) -> None:
    make_project()
    add_requirement()
    con.close()
    findings = lint.run("shop")
    assert by_flag(findings, "card_without_ref") == []
    assert by_flag(findings, "completed_without_review") == []


def test_a_link_without_a_board_slash_names_no_board(home, con) -> None:
    make_project()
    add_requirement()
    add_external_link(con, "shop", "requirement", 1, system="kanban",
                      external_id="SHOP-3", role="tracks")
    con.close()
    findings = lint.run("shop")
    assert by_flag(findings, "card_without_ref") == []
    assert by_flag(findings, "completed_without_review") == []


def test_lint_counts_the_warning_in_the_summary_line(home, con, capsys) -> None:
    make_project()
    add_requirement()
    make_board(home, "dev", [{"id": "t_1", "title": "HP-1 Widget",
                              "body": "no ref anywhere", "status": "ready"}])
    add_external_link(con, "shop", "requirement", 1, system="kanban",
                      external_id="dev/t_1", role="tracks")
    con.close()
    rc, out, err = cli(capsys, "lint")
    assert rc == 0, err                       # warnings never fail the exit code
    lines = out.splitlines()
    assert lines[0] == "lint (shop): 0 errors, 1 warnings"
    assert f"warning card_without_ref t_1  {ref_message('HP-1 Widget')}" in lines


def test_lint_flags_a_completed_card_from_the_board_events(home, con, capsys) -> None:
    make_project()
    add_requirement()
    make_board(home, "dev", [{"id": "t_1", "title": "HP-1 Widget",
                              "body": "product: R1", "status": "done"}],
               events=[("t_1", "claimed", RULE - 5), ("t_1", "completed", RULE + 5)])
    add_external_link(con, "shop", "requirement", 1, system="kanban",
                      external_id="dev/t_1", role="tracks")
    con.close()
    rc, out, err = cli(capsys, "lint")
    assert rc == 0, err
    assert f"warning completed_without_review t_1  {review_message('HP-1 Widget')}" \
        in out.splitlines()


def test_read_board_events_returns_the_three_kinds(home) -> None:
    make_board(home, "dev", [{"id": "t_1", "title": "HP-1 Widget", "status": "done"}],
               events=[("t_1", "created", RULE - 9),
                       ("t_1", "claimed", RULE - 5),
                       ("t_1", "review_requested", RULE - 2),
                       ("t_1", "completed", RULE + 1)])
    found = kanban_reader.read_board_events("dev")
    assert [(one.task_id, one.kind, one.created_at) for one in found] == [
        ("t_1", "claimed", RULE - 5),
        ("t_1", "review_requested", RULE - 2),
        ("t_1", "completed", RULE + 1),
    ]
