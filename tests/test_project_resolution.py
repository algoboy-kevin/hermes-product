"""resolve_project tests (card HP-1): every rule of project resolution.

Order: explicit slug > cwd-in-root (segment containment, longest root wins)
> `project use` > the only project > the several-projects error. Every test
runs against a tmp HERMES_HOME (monkeypatch.setenv).
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

import store


@pytest.fixture()
def con(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> sqlite3.Connection:
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes-home"))
    connection = store.connect()
    yield connection
    connection.close()


# ------------------------------------------------------------ (1) explicit slug
def test_explicit_slug_wins_over_cwd(con: sqlite3.Connection, tmp_path: Path) -> None:
    store.create_project("a", "A", root=str(tmp_path / "a"), con=con)
    store.create_project("b", "B", root=str(tmp_path / "b"), con=con)
    assert store.resolve_project("a", con, cwd=tmp_path / "b") == "a"


def test_unknown_slug_lists_known_projects(con: sqlite3.Connection) -> None:
    store.create_project("a", "A", con=con)
    store.create_project("b", "B", con=con)
    with pytest.raises(store.StoreError) as excinfo:
        store.resolve_project("x", con)
    assert str(excinfo.value) == "project 'x' does not exist (known: a, b)"


def test_unknown_slug_with_no_projects(con: sqlite3.Connection) -> None:
    with pytest.raises(store.StoreError) as excinfo:
        store.resolve_project("x", con)
    assert str(excinfo.value) == "project 'x' does not exist (known: none)"


# ------------------------------------------------------- (2) cwd inside a root
def test_cwd_under_root_picks_that_project(con: sqlite3.Connection, tmp_path: Path) -> None:
    store.create_project("a", "A", root=str(tmp_path / "x" / "proj"), con=con)
    store.create_project("b", "B", root=str(tmp_path / "other"), con=con)
    assert store.resolve_project(None, con, cwd=tmp_path / "x" / "proj" / "src") == "a"


def test_cwd_equal_to_root_picks_that_project(con: sqlite3.Connection, tmp_path: Path) -> None:
    store.create_project("a", "A", root=str(tmp_path / "x"), con=con)
    store.create_project("b", "B", root=str(tmp_path / "y"), con=con)
    assert store.resolve_project(None, con, cwd=tmp_path / "x") == "a"


def test_root_segment_boundary_is_respected(con: sqlite3.Connection, tmp_path: Path) -> None:
    """/x/proj does NOT contain /x/proj-other: with no other rule resolving, it errors."""
    store.create_project("a", "A", root=str(tmp_path / "x" / "proj"), con=con)
    store.create_project("b", "B", con=con)                # no root
    with pytest.raises(store.StoreError):
        store.resolve_project(None, con, cwd=tmp_path / "x" / "proj-other")


def test_longest_root_wins_for_nested_roots(con: sqlite3.Connection, tmp_path: Path) -> None:
    store.create_project("outer", "Outer", root=str(tmp_path / "x"), con=con)
    store.create_project("inner", "Inner", root=str(tmp_path / "x" / "proj"), con=con)
    assert store.resolve_project(None, con, cwd=tmp_path / "x" / "proj" / "src") == "inner"


def test_equal_root_tie_falls_through_to_error(con: sqlite3.Connection, tmp_path: Path) -> None:
    root = tmp_path / "x"
    store.create_project("a", "A", root=str(root), con=con)
    store.create_project("b", "B", root=str(root), con=con)
    with pytest.raises(store.StoreError) as excinfo:
        store.resolve_project(None, con, cwd=root / "src")
    message = str(excinfo.value)
    assert "several projects (" in message
    assert "pass --project <slug> or run: hermes product project use <slug>" in message


# ------------------------------------------------------------ (3) `project use`
def test_use_fallback_picks_the_active_project(con: sqlite3.Connection, tmp_path: Path) -> None:
    store.create_project("a", "A", root=str(tmp_path / "a"), con=con)
    store.create_project("b", "B", root=str(tmp_path / "b"), con=con)
    store.set_active_project("b", con=con)
    assert store.resolve_project(None, con, cwd=tmp_path / "nowhere") == "b"


# --------------------------------------------------------- (4) the only project
def test_only_project_fallback(con: sqlite3.Connection, tmp_path: Path) -> None:
    store.create_project("solo", "Solo", con=con)
    assert store.resolve_project(None, con, cwd=tmp_path / "nowhere") == "solo"


# ---------------------------------------------------------------- (5) the errors
def test_no_projects_tells_the_add_command(con: sqlite3.Connection, tmp_path: Path) -> None:
    with pytest.raises(store.StoreError) as excinfo:
        store.resolve_project(None, con, cwd=tmp_path)
    assert str(excinfo.value) == "no projects yet - hermes product project add <slug> <name>"


def test_ambiguous_projects_are_listed_with_roots(con: sqlite3.Connection,
                                                  tmp_path: Path) -> None:
    store.create_project("a", "A", root=str(tmp_path / "x"), con=con)
    store.create_project("b", "B", con=con)
    with pytest.raises(store.StoreError) as excinfo:
        store.resolve_project(None, con, cwd=tmp_path / "nowhere")
    expected = (
        f"several projects (a [{(tmp_path / 'x').resolve()}], b [no root]) -"
        " pass --project <slug> or run: hermes product project use <slug>"
    )
    assert str(excinfo.value) == expected
