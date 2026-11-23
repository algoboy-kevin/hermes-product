"""Import tests (S3 / A22) for card HP-5.

Pins: import into an empty store with original ids and verbatim history (A22:
a second export is byte-identical), `--as`, the merge rules (skip / insert /
conflict with exit 3 and a named field, links inserted if absent, history
dedupe, counters max), ONE transaction with temp-then-move body files
(atomicity), and validation errors naming file and entry. Nothing here reads
docs/.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from ruamel.yaml import YAML

import export as export_mod
import store
from _helpers import (  # `home`/`con` are fixtures imported for discovery
    add_requirement, build_export_fixture, cli, con, home, make_project,
    raw_update, table_rows,
)


def load_yaml(path: Path):
    return YAML(typ="safe").load(path.read_text(encoding="utf-8"))


def dump_yaml(path: Path, data) -> None:
    text = YAML()
    buf = __import__("io").StringIO()
    text.dump(data, buf)
    path.write_text(buf.getvalue(), encoding="utf-8")


def files_of(root: Path) -> dict:
    return {str(p.relative_to(root)): p.read_bytes()
            for p in sorted(root.rglob("*")) if p.is_file()}


def export_ok(capsys, out: Path, project: str = "shop") -> Path:
    rc, out_text, err = cli(capsys, "export", "--out", str(out), "--project", project)
    assert rc == 0, err
    return out


@pytest.fixture()
def exported(home: Path, tmp_path: Path, con, capsys) -> Path:
    build_export_fixture(con, tmp_path)
    return export_ok(capsys, tmp_path / "out")


def switch_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, name: str) -> None:
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / name))


# ------------------------------------------------------------------- A22 and basics
def test_a22_import_into_empty_store_reexport_byte_identical(
        exported: Path, tmp_path: Path, monkeypatch, capsys) -> None:
    switch_home(monkeypatch, tmp_path, "empty-home")
    rc, out_text, err = cli(capsys, "import", str(exported))
    assert rc == 0, err
    second = export_ok(capsys, tmp_path / "second")
    assert files_of(second) == files_of(exported)


def test_import_bodies_land_byte_exact(exported: Path, tmp_path: Path,
                                       monkeypatch, capsys) -> None:
    switch_home(monkeypatch, tmp_path, "empty-home")
    rc, out_text, err = cli(capsys, "import", str(exported))
    assert rc == 0, err
    bodies = store.docs_dir("shop")
    assert (bodies / "DOC2.md").read_bytes() == \
        (exported / "docs" / "DOC2-webhook-flow.md").read_bytes()
    assert (bodies / "DOC1.md").read_bytes() == \
        (exported / "docs" / "DOC1-ünïcödé-guide.md").read_bytes()
    assert not (bodies / "DOC3.md").exists()      # body_ref doc: no external file


def test_import_restores_original_ids(exported: Path, tmp_path: Path,
                                      monkeypatch, capsys) -> None:
    switch_home(monkeypatch, tmp_path, "empty-home")
    rc, out_text, err = cli(capsys, "import", str(exported))
    assert rc == 0, err
    con2 = store.connect()
    try:
        ids = [row["id"] for row in con2.execute(
            "SELECT id FROM requirement WHERE project = 'shop' ORDER BY id")]
        assert ids == [1, 2]
        row = con2.execute("SELECT body_file FROM doc WHERE project = 'shop' AND id = 2"
                           ).fetchone()
        assert row["body_file"] == "docs/DOC2.md"
    finally:
        con2.close()


def test_import_history_verbatim(exported: Path, tmp_path: Path,
                                 monkeypatch, capsys) -> None:
    switch_home(monkeypatch, tmp_path, "empty-home")
    rc, out_text, err = cli(capsys, "import", str(exported))
    assert rc == 0, err
    original = load_yaml(exported / "history.yaml")
    con2 = store.connect()
    try:
        rows = [dict(r) for r in con2.execute(
            "SELECT entity_type, entity_id, change, reason, at FROM history"
            " WHERE project = 'shop' ORDER BY id")]
    finally:
        con2.close()
    expected = [{key: row.get(key) for key in ("entity_type", "entity_id", "change",
                                               "reason", "at")} for row in original]
    assert rows == expected


def test_import_restores_counters(exported: Path, tmp_path: Path,
                                  monkeypatch, capsys) -> None:
    switch_home(monkeypatch, tmp_path, "empty-home")
    rc, out_text, err = cli(capsys, "import", str(exported))
    assert rc == 0, err
    from _helpers import add_requirement
    assert add_requirement(project="shop") == 3       # ids 1,2 used; 3 is next


def test_import_output_line(exported: Path, tmp_path: Path,
                            monkeypatch, capsys) -> None:
    switch_home(monkeypatch, tmp_path, "empty-home")
    rc, out_text, err = cli(capsys, "import", str(exported))
    assert rc == 0
    assert out_text == "imported shop: 12 inserted, 0 skipped, 0 conflicts\n"


def test_default_slug_from_project_yaml(exported: Path, tmp_path: Path,
                                        monkeypatch, capsys) -> None:
    switch_home(monkeypatch, tmp_path, "empty-home")
    rc, out_text, err = cli(capsys, "import", str(exported))
    assert rc == 0
    assert out_text.startswith("imported shop: ")


def test_as_slug_creates_under_new_slug(exported: Path, tmp_path: Path,
                                        monkeypatch, capsys) -> None:
    switch_home(monkeypatch, tmp_path, "empty-home")
    rc, out_text, err = cli(capsys, "import", str(exported), "--as", "other")
    assert rc == 0, err
    assert out_text.startswith("imported other: ")
    other = store.get_project("other")
    assert other is not None and other["name"] == "Shop"   # name/root from the file
    assert store.get_project("shop") is None
    con2 = store.connect()
    try:
        count = con2.execute("SELECT COUNT(*) AS n FROM requirement"
                             " WHERE project = 'other'").fetchone()["n"]
        assert count == 2
    finally:
        con2.close()


def test_import_missing_project_yaml(home: Path, tmp_path: Path, capsys) -> None:
    make_project()
    empty = tmp_path / "not-an-export"
    empty.mkdir()
    rc, out_text, err = cli(capsys, "import", str(empty))
    assert rc == 1
    assert "project.yaml" in err


# ----------------------------------------------------------------------- merge rules
def add_requirement_row(exported: Path, entity_id: int = 3) -> None:
    """Give the export a row the store does not have (merge: absent -> insert)."""
    rows = load_yaml(exported / "requirements.yaml")
    rows.append({"id": entity_id, "title": "Fraud checks",
                 "statement": "A buyer cannot pay with a stolen card",
                 "priority": "must", "status": "draft",
                 "blocked": False, "legacy": False})
    dump_yaml(exported / "requirements.yaml", rows)


def test_merge_reimport_is_all_skipped(exported: Path, capsys) -> None:
    rc, out_text, err = cli(capsys, "import", str(exported))
    assert rc == 0, err
    assert out_text == "imported shop: 0 inserted, 12 skipped, 0 conflicts\n"


def test_merge_inserts_missing_entity(exported: Path, con, capsys) -> None:
    add_requirement_row(exported)
    rc, out_text, err = cli(capsys, "import", str(exported))
    assert rc == 0, err
    assert out_text == "imported shop: 1 inserted, 12 skipped, 0 conflicts\n"
    row = con.execute("SELECT title, status FROM requirement"
                      " WHERE project = 'shop' AND id = 3").fetchone()
    assert row is not None and row["title"] == "Fraud checks"
    assert row["status"] == "draft"


def test_merge_conflict_names_field_and_exits_three(exported: Path, con, capsys) -> None:
    raw_update(con, "shop", "requirement", 1,
               statement="A buyer can pay by card at checkout, always")
    con.close()
    rc, out_text, err = cli(capsys, "import", str(exported))
    assert rc == 3
    assert "imported shop: 0 inserted, 11 skipped, 1 conflicts" in out_text
    assert "conflict R1: statement differs" in out_text


def test_merge_conflict_never_overwrites(exported: Path, con, capsys) -> None:
    raw_update(con, "shop", "requirement", 1, statement="the local wording wins")
    con.close()
    rc, out_text, err = cli(capsys, "import", str(exported))
    assert rc == 3
    con = store.connect()
    try:
        row = con.execute("SELECT statement FROM requirement"
                          " WHERE project = 'shop' AND id = 1").fetchone()
        assert row["statement"] == "the local wording wins"
    finally:
        con.close()


def test_merge_conflict_still_commits_the_rest(exported: Path, con, capsys) -> None:
    add_requirement_row(exported)                    # absent -> insert
    raw_update(con, "shop", "requirement", 1, statement="local change")   # -> conflict
    con.close()
    rc, out_text, err = cli(capsys, "import", str(exported))
    assert rc == 3
    assert "imported shop: 1 inserted, 11 skipped, 1 conflicts" in out_text
    assert "conflict R1: statement differs" in out_text
    con = store.connect()
    try:
        ids = [row["id"] for row in con.execute(
            "SELECT id FROM requirement WHERE project = 'shop' ORDER BY id")]
        assert ids == [1, 2, 3]                 # the non-conflicting rows landed
    finally:
        con.close()


def test_merge_links_inserted_if_absent(exported: Path, con, capsys) -> None:
    con.execute("DELETE FROM feature_requirement WHERE project = 'shop'"
                " AND feature_id = 1 AND requirement_id = 1")
    con.commit()
    con.close()
    rc, out_text, err = cli(capsys, "import", str(exported))
    assert rc == 0, err
    assert (1, 1) in table_rows("shop", "feature_requirement")


def test_merge_history_dedupe(exported: Path, capsys) -> None:
    rc, out_text, err = cli(capsys, "import", str(exported))
    assert rc == 0, err
    con = store.connect()
    try:
        count = con.execute("SELECT COUNT(*) AS n FROM history"
                            " WHERE project = 'shop'").fetchone()["n"]
    finally:
        con.close()
    original = load_yaml(exported / "history.yaml")
    assert count == len(original)               # every row was already there


def test_merge_history_inserted_when_new(exported: Path, capsys) -> None:
    history = load_yaml(exported / "history.yaml")
    history.append({"entity_type": "requirement", "entity_id": 1,
                    "change": "note added", "at": "2026-01-01T00:00:00+00:00"})
    dump_yaml(exported / "history.yaml", history)
    rc, out_text, err = cli(capsys, "import", str(exported))
    assert rc == 0, err
    con = store.connect()
    try:
        rows = [row["change"] for row in con.execute(
            "SELECT change FROM history WHERE project = 'shop' ORDER BY id")]
    finally:
        con.close()
    assert rows[-1] == "note added"
    # importing again dedupes the new row too
    rc, out_text, err = cli(capsys, "import", str(exported))
    assert rc == 0, err
    con = store.connect()
    try:
        count = con.execute("SELECT COUNT(*) AS n FROM history"
                            " WHERE project = 'shop'").fetchone()["n"]
    finally:
        con.close()
    assert count == len(history)


def test_merge_counters_take_max(exported: Path, capsys) -> None:
    project = load_yaml(exported / "project.yaml")
    project["counters"]["R"] = 99
    dump_yaml(exported / "project.yaml", project)
    rc, out_text, err = cli(capsys, "import", str(exported))
    assert rc == 0, err
    from _helpers import add_requirement
    assert add_requirement(project="shop") == 99


# ------------------------------------------------------------------------ atomicity
def test_atomic_failure_leaves_fresh_store_and_disk_untouched(
        exported: Path, tmp_path: Path, monkeypatch, capsys) -> None:
    switch_home(monkeypatch, tmp_path, "empty-home")

    def boom() -> None:
        raise RuntimeError("forced mid-import failure")

    monkeypatch.setattr(export_mod, "_after_entity_rows", boom)
    with pytest.raises(RuntimeError):
        export_mod.import_export(exported)
    assert store.get_project("shop") is None                # db untouched
    bodies = store.docs_dir("shop")
    assert not bodies.exists() or list(bodies.iterdir()) == []   # no body files


def test_atomic_failure_leaves_merge_untouched(
        exported: Path, monkeypatch, capsys) -> None:
    add_requirement_row(exported)                    # would be inserted
    con = store.connect()
    con.execute("DELETE FROM feature_requirement WHERE project = 'shop'"
                " AND feature_id = 1 AND requirement_id = 1")
    con.commit()
    con.close()

    def boom() -> None:
        raise RuntimeError("forced mid-import failure")

    monkeypatch.setattr(export_mod, "_after_entity_rows", boom)
    with pytest.raises(RuntimeError):
        export_mod.import_export(exported)
    con = store.connect()
    try:
        count = con.execute("SELECT COUNT(*) AS n FROM feature_requirement"
                            " WHERE project = 'shop'").fetchone()["n"]
        row = con.execute("SELECT 1 FROM requirement WHERE project = 'shop'"
                          " AND id = 3").fetchone()
    finally:
        con.close()
    assert count == 1                       # the deleted link did NOT come back
    assert row is None                      # and neither did the new row land
    bodies = store.docs_dir("shop")
    assert sorted(p.name for p in bodies.iterdir()) == ["DOC1.md", "DOC2.md"]


# ------------------------------------------------------------------ validation errors
def _edit(exported: Path, name: str, old: str, new: str) -> None:
    path = exported / name
    text = path.read_text(encoding="utf-8")
    assert old in text
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def test_validation_bad_enum_names_file_and_entry(exported: Path, capsys) -> None:
    _edit(exported, "requirements.yaml", "status: in_progress", "status: bogus")
    rc, out_text, err = cli(capsys, "import", str(exported))
    assert rc == 1
    assert "requirements.yaml" in err and "R1" in err and "bogus" in err


def test_validation_dangling_link_names_file_and_entry(exported: Path, capsys) -> None:
    _edit(exported, "links.yaml", "- [1, 1]", "- [9, 1]")
    rc, out_text, err = cli(capsys, "import", str(exported))
    assert rc == 1
    assert "links.yaml" in err and "F9" in err


def test_validation_two_prd_docs_names_file_and_entry(exported: Path, capsys) -> None:
    docs = load_yaml(exported / "docs.yaml")
    docs[1]["kind"] = "prd"
    dump_yaml(exported / "docs.yaml", docs)
    rc, out_text, err = cli(capsys, "import", str(exported))
    assert rc == 1
    assert "docs.yaml" in err and "DOC2" in err and "prd" in err


def test_validation_malformed_yaml_names_file(exported: Path, capsys) -> None:
    (exported / "features.yaml").write_text("- id: 1\n  title: [unclosed\n",
                                            encoding="utf-8")
    rc, out_text, err = cli(capsys, "import", str(exported))
    assert rc == 1
    assert "features.yaml" in err


def test_validation_missing_body_file_names_file_and_entry(exported: Path, capsys) -> None:
    (exported / "docs" / "DOC2-webhook-flow.md").unlink()
    rc, out_text, err = cli(capsys, "import", str(exported))
    assert rc == 1
    assert "docs.yaml" in err and "DOC2" in err
    assert "DOC2-webhook-flow.md" in err
