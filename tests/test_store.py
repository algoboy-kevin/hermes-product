"""Store tests for the `product` plugin (card HP-1): S1 schema v1, ids, constraints.

Every test runs against a tmp HERMES_HOME (monkeypatch.setenv): the real store is
never touched. No test reads docs/: the expected table set and enum values are
transcribed into constants below.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

import store

# Every table of SCHEMA_V1 (S1 sections 2-9), frozen names.
SCHEMA_V1_TABLES = {
    "project", "meta", "id_counter",
    "requirement", "criterion", "feature", "spec", "doc",
    "feature_requirement", "spec_requirement", "feature_spec",
    "feature_dependency", "requirement_dependency", "spec_dependency",
    "doc_link", "external_link", "history",
}


@pytest.fixture()
def con(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> sqlite3.Connection:
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes-home"))
    connection = store.connect()
    yield connection
    connection.close()


# ---------------------------------------------------------------- fresh install
def test_fresh_db_is_stamped_at_latest_with_every_table(con: sqlite3.Connection) -> None:
    version = con.execute("PRAGMA user_version").fetchone()[0]
    tables = {row["name"] for row in
              con.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert version == store.LATEST_VERSION
    assert SCHEMA_V1_TABLES <= tables


def test_reconnect_is_idempotent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes-home"))
    store.connect().close()
    path = store.db_path()
    store.connect().close()
    con = sqlite3.connect(str(path))
    try:
        assert con.execute("PRAGMA user_version").fetchone()[0] == store.LATEST_VERSION
    finally:
        con.close()
    assert not list(path.parent.glob("*.bak*"))


def test_foreign_keys_are_on(con: sqlite3.Connection) -> None:
    assert con.execute("PRAGMA foreign_keys").fetchone()[0] == 1


# ------------------------------------------------------------------------ ids
def test_ids_are_allocated_per_project(con: sqlite3.Connection) -> None:
    store.create_project("a", "A", con=con)
    store.create_project("b", "B", con=con)
    assert store.next_id(con, "a", "R") == 1
    assert store.next_id(con, "b", "R") == 1
    assert store.next_id(con, "a", "R") == 2
    assert store.next_id(con, "b", "R") == 2
    store.create_project("c", "C", con=con)
    assert store.next_id(con, "c", "R") == 1          # counters are per project


def test_ids_are_never_reused(con: sqlite3.Connection) -> None:
    store.create_project("a", "A", con=con)
    assert store.next_id(con, "a", "R") == 1
    con.execute("INSERT INTO requirement(project, id) VALUES ('a', 1)")
    con.execute("DELETE FROM requirement WHERE project = 'a' AND id = 1")
    con.commit()
    assert store.next_id(con, "a", "R") == 2          # the deleted row's id stays retired


def test_kinds_are_independent(con: sqlite3.Connection) -> None:
    store.create_project("a", "A", con=con)
    assert store.next_id(con, "a", "R") == 1
    assert store.next_id(con, "a", "F") == 1
    assert store.next_id(con, "a", "A") == 1
    assert store.next_id(con, "a", "R") == 2


# ------------------------------------------------------------- referential glue
def test_link_rows_cannot_cross_projects(con: sqlite3.Connection) -> None:
    store.create_project("a", "A", con=con)
    store.create_project("b", "B", con=con)
    con.execute("INSERT INTO feature(project, id) VALUES ('a', 1)")
    con.execute("INSERT INTO requirement(project, id) VALUES ('b', 1)")
    con.commit()
    with pytest.raises(sqlite3.IntegrityError):
        con.execute(
            "INSERT INTO feature_requirement(project, feature_id, requirement_id)"
            " VALUES ('a', 1, 1)"
        )


# ------------------------------------------------------------------- CHECKs
def test_enum_check_rejects_bad_status(con: sqlite3.Connection) -> None:
    store.create_project("a", "A", con=con)
    with pytest.raises(sqlite3.IntegrityError):
        con.execute("INSERT INTO requirement(project, id, status) VALUES ('a', 1, 'bogus')")


def test_slug_check_rejects_bad_slug(con: sqlite3.Connection) -> None:
    for bad in ("UPPER", "-lead", "has_underscore", "dot.slug", "x" * 41, ""):
        with pytest.raises(sqlite3.IntegrityError):
            con.execute(
                "INSERT INTO project(slug, name, created_at, updated_at)"
                " VALUES (?, 'X', 't', 't')",
                (bad,),
            )


# ------------------------------------------------------- project verbs (store side)
def test_create_project_rejects_duplicate_slug(con: sqlite3.Connection) -> None:
    store.create_project("shop", "Shop", con=con)
    with pytest.raises(store.StoreError) as excinfo:
        store.create_project("shop", "Second", con=con)
    assert "already exists" in str(excinfo.value)
    assert store.list_projects(con) == [{"slug": "shop", "name": "Shop", "root": None}]


def test_create_project_rejects_invalid_slug_naming_the_rule(con: sqlite3.Connection) -> None:
    with pytest.raises(store.StoreError) as excinfo:
        store.create_project("Bad_Slug", "X", con=con)
    assert "^[a-z0-9][a-z0-9-]{0,39}$" in str(excinfo.value)
    assert store.list_projects(con) == []


def test_create_project_stores_root_absolute(tmp_path: Path,
                                             monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes-home"))
    root = tmp_path / "code" / "shop"
    created = store.create_project("shop", "Shop", root=str(root))
    assert created == {"slug": "shop", "name": "Shop", "root": str(root.resolve())}
    assert store.list_projects() == [{"slug": "shop", "name": "Shop", "root": str(root.resolve())}]
    assert store.project_exists("shop") is True
    assert store.project_exists("ghost") is False
    assert store.get_project("ghost") is None
    assert store.get_project("shop")["name"] == "Shop"


def test_set_active_project_requires_an_existing_project(con: sqlite3.Connection) -> None:
    store.create_project("shop", "Shop", con=con)
    with pytest.raises(store.StoreError) as excinfo:
        store.set_active_project("ghost", con=con)
    assert "project 'ghost' does not exist (known: shop)" in str(excinfo.value)
    store.set_active_project("shop", con=con)
    assert store.get_active_project(con) == "shop"
