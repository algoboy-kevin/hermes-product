"""Schema v3: `doc.kind` is validated by the model, so `report` (and later kinds) need no rebuild.

Pinned here: the `report` kind through `add`; a v1 database with documents, links and a
supersede chain migrates v1 -> v3 intact (ids, links, foreign keys, backup) with the
rebuilt table accepting the new kind; and an unknown kind is still refused by the model.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

import model
import store
from _helpers import add_doc, add_spec, cli, home, make_project  # noqa: F401  (fixtures)


def test_report_is_a_kind(home: Path, capsys) -> None:
    make_project()
    rc, out, err = cli(capsys, "add", "DOC", "--title", "Run report", "--kind", "report",
                       "--summary", "What the 2026-11-23 run measured.", "--body", "# Run\n")
    assert rc == 0, err
    assert store.get_entity("shop", "DOC1")["row"]["kind"] == "report"
    assert "report" in model.DOC_KINDS


def test_an_unknown_kind_is_still_refused(home: Path, capsys) -> None:
    make_project()
    rc, _out, err = cli(capsys, "add", "DOC", "--title", "Odd one", "--kind", "memo",
                        "--summary", "s.")
    assert rc != 0 and "kind must be one of" in err and "report" in err
    assert store.list_entities("shop", "doc") == []


def test_v1_documents_survive_the_rebuild(home: Path) -> None:
    path = store.db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = sqlite3.connect(str(path))
    raw.executescript(store.SCHEMA_V1)
    raw.execute("INSERT INTO project(slug, name, created_at, updated_at) VALUES ('old','Old','t','t')")
    raw.execute("INSERT INTO spec(project,id,title,summary,status) VALUES ('old',1,'Spec','s.','draft')")
    raw.executemany(
        "INSERT INTO doc(project,id,title,kind,summary,body_file,status,superseded_by,updated_at)"
        " VALUES ('old',?,?,?,?,?,?,?,'t')",
        [(1, "First", "design", "One.", "docs/DOC1.md", "superseded", 2),
         (2, "Second", "prd", "Two.", None, "current", None)])
    raw.execute("INSERT INTO doc_link VALUES ('old', 2, 'spec', 1)")
    raw.execute("INSERT INTO id_counter VALUES ('old','DOC',3)")
    raw.execute("PRAGMA user_version = 1")
    raw.commit()
    raw.close()

    store.connect().close()

    migrated = sqlite3.connect(str(path))
    try:
        assert migrated.execute("PRAGMA user_version").fetchone()[0] == store.LATEST_VERSION
        rows = migrated.execute(
            "SELECT id, title, kind, body_file, status, superseded_by FROM doc ORDER BY id").fetchall()
        assert rows == [(1, "First", "design", "docs/DOC1.md", "superseded", 2),
                        (2, "Second", "prd", None, "current", None)]
        assert migrated.execute("SELECT * FROM doc_link").fetchall() == [("old", 2, "spec", 1)]
        assert migrated.execute("PRAGMA foreign_key_check").fetchall() == []
        assert migrated.execute("SELECT next_id FROM id_counter WHERE kind='DOC'").fetchone()[0] == 3
        # the rebuilt table has no kind CHECK any more ...
        migrated.execute("INSERT INTO doc(project,id,title,kind,summary,status)"
                         " VALUES ('old',9,'Free kind','report','s.','current')")
        # ... but the status CHECK and the foreign keys are still enforced
        with pytest.raises(sqlite3.IntegrityError):
            migrated.execute("INSERT INTO doc(project,id,title,kind,summary,status)"
                             " VALUES ('old',10,'Bad status','report','s.','weird')")
        migrated.commit()                       # the pragma is a no-op inside a transaction
        migrated.execute("PRAGMA foreign_keys = ON")
        with pytest.raises(sqlite3.IntegrityError):
            migrated.execute("INSERT INTO doc_link VALUES ('old', 777, 'spec', 1)")
    finally:
        migrated.close()
    assert (path.parent / f"{path.name}.bak-v1").is_file()


def test_foreign_keys_are_on_again_after_a_migration(home: Path) -> None:
    path = store.db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = sqlite3.connect(str(path))
    raw.executescript(store.SCHEMA_V1)
    raw.execute("PRAGMA user_version = 1")
    raw.commit()
    raw.close()
    con = store.connect()
    try:
        assert con.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    finally:
        con.close()
