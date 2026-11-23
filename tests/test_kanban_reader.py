"""HP-12a: the pure external-link core (``kanban_reader.py``).

Covers S5's card -> entry rules: the ``product:`` line parser, the column ->
status mapping (nothing maps to ``verified``), the read-only board reader (a
``.backup`` copy, never the live db), link proposals and the A20 warning.

Every test that needs a board builds a synthetic ``kanban.db`` under ``tmp_path``
with the real ``tasks`` columns; the real ``~/.hermes`` is never touched.
"""

from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path

import pytest

import kanban_reader

# The real `tasks` columns the reader may use (same names/types as the board).
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


# --------------------------------------------------------------------- fixtures
def make_board(home: Path, slug: str, cards: list[dict], *,
               ddl: str = TASKS_DDL) -> Path:
    """Build a board db at ``<home>/kanban/boards/<slug>/kanban.db`` and return its path."""
    path = home / "kanban" / "boards" / slug / "kanban.db"
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(path))
    try:
        con.executescript(ddl)
        columns = {row[1] for row in con.execute("PRAGMA table_info(tasks)")}
        fields = [col for col in ("id", "title", "body", "assignee", "status",
                                  "priority", "created_at") if col in columns]
        for index, card in enumerate(cards):
            values = {
                "id": card["id"], "title": card["title"], "body": card.get("body"),
                "assignee": card.get("assignee"), "status": card["status"],
                "priority": card.get("priority", 0),
                "created_at": card.get("created_at", 1000 + index),
            }
            con.execute(
                f"INSERT INTO tasks({', '.join(fields)})"
                f" VALUES ({', '.join('?' * len(fields))})",
                tuple(values[name] for name in fields),
            )
        con.commit()
    finally:
        con.close()
    return path


@pytest.fixture()
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A throwaway HERMES_HOME; the real board tree is never read."""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes-home"))
    return tmp_path / "hermes-home"


# --------------------------------------------------------------- parse_product_line
def test_parse_several_refs_in_order() -> None:
    parsed = kanban_reader.parse_product_line("product: R3, S2, F7")
    assert parsed.refs == ["R3", "S2", "F7"]
    assert parsed.invalid == []


def test_parse_commas_and_whitespace_without_space_after_key() -> None:
    parsed = kanban_reader.parse_product_line("product:F7 S12, A21")
    assert parsed.refs == ["F7", "S12", "A21"]
    assert parsed.invalid == []


def test_parse_prefix_is_case_insensitive_and_may_be_indented() -> None:
    assert kanban_reader.parse_product_line("Product: F7").refs == ["F7"]
    assert kanban_reader.parse_product_line("PRODUCT: A21").refs == ["A21"]
    assert kanban_reader.parse_product_line("   product: S2").refs == ["S2"]
    assert kanban_reader.parse_product_line("\tproduct: DOC1").refs == ["DOC1"]


def test_parse_duplicates_keep_first_appearance() -> None:
    parsed = kanban_reader.parse_product_line("product: F7, F7 R3, R3")
    assert parsed.refs == ["F7", "R3"]
    assert parsed.invalid == []


def test_parse_feature_slug_form() -> None:
    parsed = kanban_reader.parse_product_line("product: F:stripe, F7, F:payments-v2")
    assert parsed.refs == ["F:stripe", "F7", "F:payments-v2"]
    assert parsed.invalid == []


def test_parse_invalid_tokens_are_collected_in_order() -> None:
    parsed = kanban_reader.parse_product_line(
        "product: F7, banana, r3, R01, S0, F:Bad_Slug"
    )
    assert parsed.refs == ["F7"]
    assert parsed.invalid == ["banana", "r3", "R01", "S0", "F:Bad_Slug"]


def test_parse_leading_zero_is_invalid() -> None:
    parsed = kanban_reader.parse_product_line("product: R0, DOC01, R10")
    assert parsed.refs == ["R10"]
    assert parsed.invalid == ["R0", "DOC01"]


def test_parse_fenced_code_block_is_ignored() -> None:
    body = "intro\n```\nproduct: F7\n```\nproduct: S2\n"
    assert kanban_reader.parse_product_line(body).refs == ["S2"]


def test_parse_unclosed_fence_swallows_the_rest() -> None:
    body = "```text\nproduct: F7\nproduct: S2\n"
    parsed = kanban_reader.parse_product_line(body)
    assert parsed.refs == [] and parsed.invalid == []


def test_parse_without_a_product_line_is_empty() -> None:
    parsed = kanban_reader.parse_product_line("fix the checkout bug\nno mapping here")
    assert parsed.refs == [] and parsed.invalid == []


def test_parse_empty_and_none_body() -> None:
    assert kanban_reader.parse_product_line("").refs == []
    assert kanban_reader.parse_product_line(None).refs == []
    assert kanban_reader.parse_product_line("product:").refs == []
    assert kanban_reader.parse_product_line("product:  ,, ").refs == []


def test_parse_only_anchored_at_line_start() -> None:
    assert kanban_reader.parse_product_line("see product: F7").refs == []
    assert kanban_reader.parse_product_line("productivity: F7").refs == []
    assert kanban_reader.parse_product_line("xproduct: F7").refs == []


def test_parse_several_lines_union_in_first_seen_order() -> None:
    body = "product: F7, S2\nsome prose\nproduct: S2, R3\n"
    parsed = kanban_reader.parse_product_line(body)
    assert parsed.refs == ["F7", "S2", "R3"]


# ------------------------------------------------------------------ map_card_status
@pytest.mark.parametrize("status,expected", [
    ("triage", "approved"),
    ("todo", "approved"),
    ("ready", "approved"),
    ("scheduled", "approved"),
    ("blocked", "approved"),
    ("running", "in_progress"),
    ("review", "implemented"),
    ("done", "implemented"),
    ("archived", None),
    ("launched", None),
])
def test_default_mapping(status: str, expected) -> None:
    assert kanban_reader.map_card_status(status) == expected


def test_nothing_maps_to_verified() -> None:
    """A19/S5: a finished card is evidence, not proof."""
    assert set(kanban_reader.DEFAULT_STATUS_MAPPING.values()) - {None} == {
        "approved", "in_progress", "implemented",
    }
    for status in kanban_reader.DEFAULT_STATUS_MAPPING:
        assert kanban_reader.map_card_status(status) != "verified"


def test_done_maps_to_implemented_not_verified() -> None:
    """A19: S2 becomes implemented, never verified."""
    assert kanban_reader.map_card_status("done") == "implemented"


def test_custom_mapping_replaces_only_the_entries_it_names() -> None:
    custom = {"todo": None, "done": "shipped"}
    assert kanban_reader.map_card_status("todo", custom) is None
    assert kanban_reader.map_card_status("done", custom) == "shipped"
    assert kanban_reader.map_card_status("running", custom) == "in_progress"


def test_custom_mapping_unknown_status_is_none() -> None:
    assert kanban_reader.map_card_status("weird", {"todo": "approved"}) is None


# ---------------------------------------------------------------- read_board_cards
def test_read_board_cards_reads_every_field_ordered_by_id(home: Path) -> None:
    make_board(home, "b", [
        {"id": "t_3", "title": "Three", "status": "done", "body": "product: S2"},
        {"id": "t_1", "title": "One", "status": "todo", "assignee": "bob"},
        {"id": "t_2", "title": "Two", "status": "archived", "body": None},
    ])
    cards = kanban_reader.read_board_cards("b")
    assert [card.id for card in cards] == ["t_1", "t_2", "t_3"]
    first = cards[0]
    assert first.board == "b" and first.title == "One" and first.status == "todo"
    assert first.assignee == "bob" and first.body is None
    assert cards[2].body == "product: S2"
    # archived cards are still read; their mapped status is None
    assert kanban_reader.map_card_status(cards[1].status) is None


def test_read_board_cards_honours_the_hermes_home_argument(tmp_path: Path) -> None:
    elsewhere = tmp_path / "other-home"
    make_board(elsewhere, "b", [{"id": "t_1", "title": "One", "status": "ready"}])
    cards = kanban_reader.read_board_cards("b", hermes_home=elsewhere)
    assert [card.id for card in cards] == ["t_1"]
    assert cards[0].board == "b"


def test_read_board_cards_missing_board_names_the_path(home: Path) -> None:
    with pytest.raises(kanban_reader.KanbanReadError) as excinfo:
        kanban_reader.read_board_cards("ghost")
    message = str(excinfo.value)
    assert "ghost" in message
    assert str(home / "kanban" / "boards" / "ghost" / "kanban.db") in message


def test_read_board_cards_refuses_a_board_slug_that_is_a_path(home: Path) -> None:
    with pytest.raises(kanban_reader.KanbanReadError):
        kanban_reader.read_board_cards("../escape")


def test_read_board_cards_missing_tasks_table_is_an_error(home: Path) -> None:
    path = home / "kanban" / "boards" / "b" / "kanban.db"
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(path))
    con.execute("CREATE TABLE notes (id TEXT)")
    con.commit()
    con.close()
    with pytest.raises(kanban_reader.KanbanReadError, match="schema"):
        kanban_reader.read_board_cards("b")


def test_read_board_cards_tolerates_missing_body_and_assignee(home: Path) -> None:
    ddl = ("CREATE TABLE tasks (id TEXT PRIMARY KEY, title TEXT NOT NULL,"
           " status TEXT NOT NULL, created_at INTEGER NOT NULL);")
    make_board(home, "b", [{"id": "t_1", "title": "One", "status": "todo"}], ddl=ddl)
    cards = kanban_reader.read_board_cards("b")
    assert cards[0].body is None and cards[0].assignee is None


def test_module_connection_is_a_copy_and_write_attempt_raises(home: Path) -> None:
    live = make_board(home, "b", [{"id": "t_1", "title": "One", "status": "todo"}])
    with kanban_reader.board_copy("b") as con:
        # reads work
        assert con.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == 1
        opened = con.execute("PRAGMA database_list").fetchone()[2]
        assert Path(opened) != live                      # the copy, not the live db
        assert Path(opened).is_file()
        with pytest.raises(sqlite3.OperationalError):
            con.execute("UPDATE tasks SET status = 'done'")
        with pytest.raises(sqlite3.OperationalError):
            con.execute("INSERT INTO tasks(id, title, status, created_at)"
                        " VALUES ('t_2', 'x', 'todo', 1)")
        with pytest.raises(sqlite3.OperationalError):
            con.execute("DELETE FROM tasks")
        copy_path = Path(opened)
    assert not copy_path.exists()                         # the temp copy is removed


def test_read_board_cards_leaves_the_live_db_untouched(home: Path) -> None:
    live = make_board(home, "b", [
        {"id": "t_1", "title": "One", "status": "todo", "body": "product: F7"},
    ])
    before = (hashlib.sha256(live.read_bytes()).hexdigest(),
              live.stat().st_size, live.stat().st_mtime_ns)
    cards = kanban_reader.read_board_cards("b")
    after = (hashlib.sha256(live.read_bytes()).hexdigest(),
             live.stat().st_size, live.stat().st_mtime_ns)
    assert cards[0].body == "product: F7"
    assert before == after


def test_read_board_cards_does_not_create_the_board(home: Path) -> None:
    with pytest.raises(kanban_reader.KanbanReadError):
        kanban_reader.read_board_cards("b")
    assert not (home / "kanban").exists()


def test_read_board_cards_creates_no_file_beside_the_live_db(home: Path) -> None:
    live = make_board(home, "b", [{"id": "t_1", "title": "One", "status": "todo"}])
    before = sorted(path.name for path in live.parent.iterdir())
    assert kanban_reader.read_board_cards("b")[0].title == "One"
    assert sorted(path.name for path in live.parent.iterdir()) == before


# -------------------------------------------------------------------- propose_links
def _exists_in(known: set[str]):
    def exists(ref: str) -> bool:
        return ref in known
    return exists


def test_propose_links_resolved_and_unresolved_in_deterministic_order() -> None:
    cards = [
        kanban_reader.Card("b", "t_2", "Two", "done", "product: S2, F99", None),
        kanban_reader.Card("a", "t_9", "Nine", "running", "product: F7", None),
        kanban_reader.Card("a", "t_1", "One", "todo", "product: F7, S2, R99", None),
    ]
    proposals, unresolved = kanban_reader.propose_links(cards, _exists_in({"F7", "S2"}))
    assert [(p.board, p.card_id, p.ref) for p in proposals] == [
        ("a", "t_1", "F7"),
        ("a", "t_1", "S2"),
        ("a", "t_9", "F7"),
        ("b", "t_2", "S2"),
    ]
    assert unresolved == [("a", "t_1", "R99"), ("b", "t_2", "F99")]


def test_propose_links_carries_the_mapped_execution_status() -> None:
    cards = [
        kanban_reader.Card("b", "t_1", "One", "done", "product: F7", None),
        kanban_reader.Card("b", "t_2", "Two", "running", "product: F7", None),
        kanban_reader.Card("b", "t_3", "Three", "archived", "product: F7", None),
    ]
    proposals, unresolved = kanban_reader.propose_links(cards, _exists_in({"F7"}))
    assert unresolved == []
    assert [(p.card_id, p.execution_status) for p in proposals] == [
        ("t_1", "implemented"),
        ("t_2", "in_progress"),
        ("t_3", None),
    ]


def test_propose_links_ignores_invalid_tokens_and_cards_without_a_line() -> None:
    cards = [
        kanban_reader.Card("b", "t_1", "One", "todo", "product: F7, banana", None),
        kanban_reader.Card("b", "t_2", "Two", "todo", "no mapping", None),
    ]
    proposals, unresolved = kanban_reader.propose_links(cards, _exists_in({"F7", "banana"}))
    assert [(p.card_id, p.ref) for p in proposals] == [("t_1", "F7")]
    assert unresolved == []


# ------------------------------------------------------------------ status_warning
def test_status_warning_when_verified_and_the_card_moves_backward() -> None:
    """A20: S2 verified, its card goes done -> running: a warning, S2 unchanged."""
    warning = kanban_reader.status_warning("verified", "done", "running")
    assert warning is not None
    assert "verified" in warning
    assert "implemented" in warning and "in_progress" in warning
    assert "done" in warning and "running" in warning


def test_status_warning_is_silent_when_the_stage_does_not_drop() -> None:
    assert kanban_reader.status_warning("verified", "done", "review") is None
    assert kanban_reader.status_warning("verified", "running", "done") is None


def test_status_warning_only_applies_to_a_verified_entry() -> None:
    assert kanban_reader.status_warning("implemented", "done", "running") is None
    assert kanban_reader.status_warning("approved", "done", "todo") is None


def test_status_warning_without_a_previous_status() -> None:
    assert kanban_reader.status_warning("verified", None, "running") is None


def test_status_warning_when_the_new_status_has_no_stage() -> None:
    assert kanban_reader.status_warning("verified", "done", "archived") is None
