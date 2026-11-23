"""Schema versioning + migration tests for the product store (tmp HERMES_HOME only).

Method mirrors the feature-graph store tests: fresh install, refusal of a
newer db byte-identical, monkeypatched migrations with backup, rollback on
failure, ordering, idempotent reconnect. Differences: this is a NEW store, so
there is no legacy-unversioned adoption — an empty db (user_version 0, no
tables) is a fresh install; user_version 0 WITH tables is refused untouched.
"""
from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path

import pytest

import store


@pytest.fixture()
def db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Every test gets its own HERMES_HOME, so the real store is never touched."""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes-home"))
    return store.db_path()


# ------------------------------------------------------------------ raw db helpers

def _raw(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(str(path))


def _user_version(path: Path) -> int:
    con = _raw(path)
    try:
        return con.execute("PRAGMA user_version").fetchone()[0]
    finally:
        con.close()


def _columns(path: Path, table: str) -> set:
    con = _raw(path)
    try:
        return {row[1] for row in con.execute(f"PRAGMA table_info({table})")}
    finally:
        con.close()


def _project_slugs(path: Path) -> list:
    con = _raw(path)
    try:
        return [row[0] for row in con.execute("SELECT slug FROM project ORDER BY slug")]
    finally:
        con.close()


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _make_v1(path: Path) -> None:
    """A healthy v1 db: the baseline schema stamped 1, with one project row."""
    path.parent.mkdir(parents=True, exist_ok=True)
    con = _raw(path)
    con.executescript(store.SCHEMA_V1)
    con.execute("INSERT INTO project(slug, name, created_at, updated_at)"
                " VALUES ('demo', 'Demo', 't', 't')")
    con.commit()
    con.close()
    con = _raw(path)
    con.execute("PRAGMA user_version = 1")
    con.commit()
    con.close()


# ---------------------------------------------------------------------- fresh install

def test_empty_db_fresh_install_is_stamped_latest_without_backup(db: Path) -> None:
    store.connect().close()
    assert _user_version(db) == store.LATEST_VERSION
    tables = {row[0] for row in
              _raw(db).execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert {"project", "requirement", "history"} <= tables
    assert not list(db.parent.glob("*.bak*"))


def test_unversioned_db_with_tables_is_refused_byte_identical(db: Path) -> None:
    db.parent.mkdir(parents=True, exist_ok=True)
    con = _raw(db)
    con.execute("CREATE TABLE junk(x)")
    con.commit()
    con.close()
    before = _sha256(db)
    with pytest.raises(store.StoreError) as excinfo:
        store.connect()
    message = str(excinfo.value)
    assert "refusing" in message and "junk" in message
    assert _sha256(db) == before                          # never wrote
    assert _user_version(db) == 0


# --------------------------------------------------------------------- newer db refused

def test_newer_db_is_refused_byte_identical(db: Path) -> None:
    _make_v1(db)
    con = _raw(db)
    con.execute("PRAGMA user_version = 99")
    con.commit()
    con.close()
    before = _sha256(db)
    with pytest.raises(store.StoreError) as excinfo:
        store.connect()
    message = str(excinfo.value)
    assert "schema v99" in message
    assert f"up to v{store.LATEST_VERSION}" in message
    assert "upgrade the plugin" in message
    assert _sha256(db) == before                          # version gate before WAL
    assert _user_version(db) == 99
    assert _project_slugs(db) == ["demo"]


# ------------------------------------------------------------------------- migrations

def _add_doc_lang(con: sqlite3.Connection) -> None:
    con.execute("ALTER TABLE doc ADD COLUMN lang TEXT")


def _add_doc_toc(con: sqlite3.Connection) -> None:
    con.execute("ALTER TABLE doc ADD COLUMN toc TEXT")


def test_migration_applies_with_backup(db: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _make_v1(db)
    monkeypatch.setattr(store, "MIGRATIONS", [(2, "add doc.lang", _add_doc_lang)])
    monkeypatch.setattr(store, "LATEST_VERSION", store.latest_version())
    store.connect().close()
    assert _user_version(db) == 2
    assert "lang" in _columns(db, "doc")
    assert _project_slugs(db) == ["demo"]                 # data intact
    backup = db.parent / f"{db.name}.bak-v1"
    assert backup.is_file()
    bcon = _raw(backup)                                   # valid sqlite db at v1
    try:
        assert bcon.execute("PRAGMA user_version").fetchone()[0] == 1
        assert bcon.execute("SELECT COUNT(*) FROM project").fetchone()[0] == 1
    finally:
        bcon.close()
    assert "lang" not in _columns(backup, "doc")


def test_failing_migration_rolls_back_and_keeps_backup(db: Path,
                                                       monkeypatch: pytest.MonkeyPatch) -> None:
    def _broken(con: sqlite3.Connection) -> None:
        con.execute("ALTER TABLE doc ADD COLUMN lang TEXT")
        raise RuntimeError("boom halfway")

    _make_v1(db)
    monkeypatch.setattr(store, "MIGRATIONS", [(2, "broken migration", _broken)])
    monkeypatch.setattr(store, "LATEST_VERSION", store.latest_version())
    with pytest.raises(store.StoreError) as excinfo:
        store.connect()
    message = str(excinfo.value)
    assert "migration to v2 failed" in message
    assert "boom halfway" in message and "db restored to v1" in message
    assert "backup at" in message
    assert _user_version(db) == 1
    assert "lang" not in _columns(db, "doc")               # the ALTER rolled back
    assert _project_slugs(db) == ["demo"]
    assert (db.parent / f"{db.name}.bak-v1").is_file()


def test_two_migrations_apply_in_order(db: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    applied: list = []

    def _m2(con: sqlite3.Connection) -> None:
        applied.append(2)
        _add_doc_lang(con)

    def _m3(con: sqlite3.Connection) -> None:
        applied.append(3)
        _add_doc_toc(con)

    _make_v1(db)
    monkeypatch.setattr(store, "MIGRATIONS", [
        (2, "add doc.lang", _m2),
        (3, "add doc.toc", _m3),
    ])
    monkeypatch.setattr(store, "LATEST_VERSION", store.latest_version())
    store.connect().close()
    assert applied == [2, 3]
    assert _user_version(db) == 3
    assert {"lang", "toc"} <= _columns(db, "doc")
    assert (db.parent / f"{db.name}.bak-v1").is_file()


def test_second_connect_makes_no_second_backup(db: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _make_v1(db)
    monkeypatch.setattr(store, "MIGRATIONS", [(2, "add doc.lang", _add_doc_lang)])
    monkeypatch.setattr(store, "LATEST_VERSION", store.latest_version())
    store.connect().close()
    store.connect().close()
    assert _user_version(db) == 2
    assert [p.name for p in db.parent.glob("*.bak*")] == [f"{db.name}.bak-v1"]


# ------------------------------------------------------------------------ schema_info

def test_schema_info_reports_version_and_latest(db: Path) -> None:
    assert store.schema_info() == {"version": 0, "latest": store.LATEST_VERSION,
                                   "path": str(db)}                       # no db yet
    store.connect().close()
    info = store.schema_info()
    assert info["version"] == store.LATEST_VERSION == info["latest"]
    assert info["path"] == str(db)
