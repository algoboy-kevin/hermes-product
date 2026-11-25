"""Export tests (S3 / A21) for card HP-5.

Pins: the export layout and file list, per-file shape (schema column order, None
omitted, flags as true/false, paths as YAML lists, link rows as sorted lists),
byte-exact DOC bodies with the `DOC<id>-<slug>.md` naming rule, literal-block
multi-line strings with exact whitespace round-trip, determinism (A21: two runs
byte-identical), the empty project, the DIR refusal rules, stale body cleanup and
counter preservation (never-reused ids). Nothing here reads docs/.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from ruamel.yaml import YAML

import export as export_mod
import store
from _helpers import (  # `home`/`con` are fixtures imported for discovery
    add_requirement, build_export_fixture, cli, con, home, make_project,
)

EXPECTED_FILES = {
    "project.yaml", "requirements.yaml", "criteria.yaml", "features.yaml",
    "specs.yaml", "docs.yaml", "links.yaml", "history.yaml",
}
REQ_KEYS = ["id", "title", "statement", "rationale", "priority", "phase", "status",
            "status_reason", "superseded_by", "blocked", "blocked_reason",
            "hypothesis_ref", "legacy", "prd_ref", "notes", "origin", "updated_at"]
A_KEYS = ["id", "requirement_id", "text", "status", "evidence", "evidence_level",
          "notes", "updated_at"]
F_KEYS = ["id", "slug", "title", "layer", "status", "status_reason", "phase",
          "description", "infra", "paths", "blocked", "blocked_reason", "notes",
          "origin", "updated_at"]
S_KEYS = ["id", "title", "summary", "status", "status_reason", "superseded_by",
          "paths", "blocked", "blocked_reason", "notes", "origin", "updated_at"]


def load_yaml(path: Path):
    return YAML(typ="safe").load(path.read_text(encoding="utf-8"))


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


# ------------------------------------------------------------------ A21: stability
def test_export_file_list_exact(exported: Path) -> None:
    names = {str(p.relative_to(exported)) for p in exported.rglob("*") if p.is_file()}
    assert names == EXPECTED_FILES | {"docs/DOC1-ünïcödé-guide.md",
                                      "docs/DOC2-webhook-flow.md"}


def test_a21_two_exports_are_byte_identical(home: Path, tmp_path: Path, con, capsys) -> None:
    build_export_fixture(con, tmp_path)
    con.close()
    first = export_ok(capsys, tmp_path / "one")
    second = export_ok(capsys, tmp_path / "two")
    assert files_of(first) == files_of(second)


def test_export_twice_to_same_dir_matches(home: Path, tmp_path: Path, con, capsys) -> None:
    build_export_fixture(con, tmp_path)
    con.close()
    out = export_ok(capsys, tmp_path / "out")
    before = files_of(out)
    out = export_ok(capsys, out)
    assert files_of(out) == before


def test_every_file_ends_with_exactly_one_newline(exported: Path) -> None:
    for name, data in files_of(exported).items():
        if name.startswith("docs/"):
            continue                       # bodies are byte-exact, not newline-normalized
        assert data.endswith(b"\n"), name
        assert not data.endswith(b"\n\n"), name


def test_export_output_line(home: Path, tmp_path: Path, con, capsys) -> None:
    build_export_fixture(con, tmp_path)
    con.close()
    rc, out_text, err = cli(capsys, "export", "--out", str(tmp_path / "o"),
                            "--project", "shop")
    assert rc == 0
    assert out_text == (f"exported shop to {tmp_path / 'o'}:"
                        " 2 requirements, 3 criteria, 2 features, 2 specs, 3 docs\n")


# ------------------------------------------------------------- per-file structure
def test_project_yaml_shape(exported: Path, tmp_path: Path) -> None:
    data = load_yaml(exported / "project.yaml")
    assert data["slug"] == "shop"
    assert data["name"] == "Shop"
    assert data["root"] == str(tmp_path)
    assert data["counters"] == {"R": 3, "A": 4, "F": 3, "S": 3, "DOC": 4}


def test_requirements_yaml_shape(exported: Path) -> None:
    rows = load_yaml(exported / "requirements.yaml")
    assert [row["id"] for row in rows] == [1, 2]
    first = rows[0]
    assert [key for key in REQ_KEYS if key in first] == list(first.keys())
    assert first["priority"] == "must"
    assert first["status"] == "in_progress"
    assert first["blocked"] is False and first["legacy"] is False
    assert first["superseded_by"] == 2
    assert first["prd_ref"] == "PRD.md#goals"
    assert first["statement"] == "A buyer can pay by card at checkout"


def test_criteria_yaml_shape(exported: Path) -> None:
    rows = load_yaml(exported / "criteria.yaml")
    assert [row["id"] for row in rows] == [1, 2, 3]
    first = rows[0]
    assert [key for key in A_KEYS if key in first] == list(first.keys())
    assert first["requirement_id"] == 1
    assert first["status"] == "passed"
    assert first["evidence"] == "test_pay_by_card"
    assert first["evidence_level"] == "unit"
    assert rows[1]["status"] == "not_run"
    assert "evidence" not in rows[1]           # None values omitted


def test_features_yaml_shape(exported: Path) -> None:
    rows = load_yaml(exported / "features.yaml")
    assert [row["id"] for row in rows] == [1, 2]
    first = rows[0]
    assert [key for key in F_KEYS if key in first] == list(first.keys())
    assert first["paths"] == ["src/pay/stripe.py", "src/pay"]   # real YAML list
    assert first["infra"] is False
    second = rows[1]
    assert second["infra"] is True
    assert second["blocked"] is True
    assert second["blocked_reason"] == "vendor API token"
    assert "paths" not in second


def test_specs_yaml_shape(exported: Path) -> None:
    rows = load_yaml(exported / "specs.yaml")
    assert [row["id"] for row in rows] == [1, 2]
    first = rows[0]
    assert [key for key in S_KEYS if key in first] == list(first.keys())
    assert first["status"] == "approved"
    assert first["paths"] == ["docs/payments.md"]
    assert first["blocked"] is False


def test_docs_yaml_shape(exported: Path) -> None:
    rows = load_yaml(exported / "docs.yaml")
    assert [row["id"] for row in rows] == [1, 2, 3]
    body_doc = rows[0]
    assert body_doc["body"] == "DOC1-ünïcödé-guide.md"     # names its body file
    assert "body_file" not in body_doc                      # ... and omits body_file
    assert body_doc["kind"] == "prd"
    assert "body_ref" not in body_doc and "body_hash" not in body_doc
    ref_doc = rows[2]
    assert ref_doc["body_ref"].endswith("external-note.md")  # ref+hash only
    assert ref_doc["body_hash"] and len(ref_doc["body_hash"]) == 64
    assert "body" not in ref_doc
    assert ref_doc["status"] == "superseded"
    assert ref_doc["superseded_by"] == 1


def test_links_yaml_shape(exported: Path) -> None:
    links = load_yaml(exported / "links.yaml")
    assert list(links.keys()) == ["feature_requirement", "spec_requirement",
                                  "feature_spec", "feature_dependency",
                                  "requirement_dependency", "spec_dependency",
                                  "doc_link", "external_link"]
    assert links["feature_requirement"] == [[1, 1], [2, 2]]
    assert links["spec_requirement"] == [[1, 1], [2, 2]]
    assert links["feature_spec"] == [[1, 1]]
    assert links["feature_dependency"] == [[1, 2, False]]      # optional as bool
    assert links["requirement_dependency"] == [[2, 1]]
    assert links["spec_dependency"] == [[2, 1]]
    assert links["doc_link"] == [[2, "requirement", 1]]
    assert links["external_link"] == [{"entity_type": "requirement", "entity_id": 1,
                                       "system": "kanban", "external_id": "SHOP-3",
                                       "url": "https://kanban.example/SHOP-3",
                                       "role": "tracks"}]


def test_history_yaml_shape(exported: Path) -> None:
    rows = load_yaml(exported / "history.yaml")
    assert rows, "the fixture writes history rows"
    for row in rows:
        assert set(row) <= {"entity_type", "entity_id", "change", "reason", "at"}
    kinds = [row["entity_type"] for row in rows]
    assert "feature" in kinds and "requirement" in kinds
    # oldest first by row id: the first event is the F1-R1 link
    assert rows[0]["change"] == "linked R1"
    # reason is omitted when absent, present when the API recorded one
    passed = [row for row in rows if row["change"] == "status not_run -> passed"]
    assert passed and "reason" not in passed[0]
    assert any("reason" in row for row in rows)


# --------------------------------------------------- whitespace and unicode fidelity
def test_multiline_statement_round_trips_exactly(exported: Path) -> None:
    rows = load_yaml(exported / "requirements.yaml")
    statement = rows[1]["statement"]
    assert statement == "First line \nsecond line\n"     # trailing space AND newline
    assert rows[1]["notes"] == "line one\nline two\n"


def test_multiline_clean_value_uses_literal_block(exported: Path) -> None:
    text = (exported / "requirements.yaml").read_text(encoding="utf-8")
    assert "notes: |" in text


def test_unicode_title_names_the_body_file(exported: Path) -> None:
    assert (exported / "docs" / "DOC1-ünïcödé-guide.md").is_file()
    body = (exported / "docs" / "DOC1-ünïcödé-guide.md").read_bytes()
    assert body == "# Goals\n\nPay by card.\n".encode("utf-8")


def test_doc_body_written_byte_exact(home: Path, tmp_path: Path, con, capsys) -> None:
    build_export_fixture(con, tmp_path)
    body_text = "crlf line\r\nsecond\r\n"
    path = store.doc_body_path("shop", {"body_file": "docs/DOC2.md"})
    path.write_bytes(body_text.encode("utf-8"))
    con.close()
    out = export_ok(capsys, tmp_path / "out")
    data = (out / "docs" / "DOC2-webhook-flow.md").read_bytes()
    assert data == body_text.encode("utf-8")


def test_doc_slugify_rules() -> None:
    assert export_mod.doc_slugify("Webhook flow") == "webhook-flow"
    assert export_mod.doc_slugify("(Draft) Notes!") == "draft--notes"
    assert export_mod.doc_slugify("Ünïcödé Guide") == "ünïcödé-guide"
    long = export_mod.doc_slugify("A" * 20 + " " + "B" * 40)
    assert len(long) == 40 and long == ("a" * 20 + "-" + "b" * 19)
    assert export_mod.doc_slugify("!!!") == ""


# ------------------------------------------------------------------------- empty
def test_empty_project_files(home: Path, tmp_path: Path, capsys) -> None:
    make_project()
    out = export_ok(capsys, tmp_path / "out")
    assert load_yaml(out / "requirements.yaml") == []
    assert load_yaml(out / "criteria.yaml") == []
    assert load_yaml(out / "features.yaml") == []
    assert load_yaml(out / "specs.yaml") == []
    assert load_yaml(out / "docs.yaml") == []
    assert load_yaml(out / "history.yaml") == []
    links = load_yaml(out / "links.yaml")
    assert set(links) == {"feature_requirement", "spec_requirement", "feature_spec",
                          "feature_dependency", "requirement_dependency",
                          "spec_dependency", "doc_link", "external_link"}
    assert all(rows == [] for rows in links.values())
    assert load_yaml(out / "project.yaml")["counters"] == {"R": 1, "A": 1, "F": 1,
                                                          "S": 1, "DOC": 1}
    assert list((out / "docs").iterdir()) == []


# ------------------------------------------------------------------- DIR handling
def test_refuse_nonempty_dir_without_project_yaml(home: Path, tmp_path: Path, con, capsys) -> None:
    build_export_fixture(con, tmp_path)
    con.close()
    out = tmp_path / "occupied"
    out.mkdir()
    (out / "keep.txt").write_text("mine", encoding="utf-8")
    rc, out_text, err = cli(capsys, "export", "--out", str(out), "--project", "shop")
    assert rc == 1
    assert "project.yaml" in err
    assert files_of(out) == {"keep.txt": b"mine"}        # nothing written


def test_export_out_dir_is_required(home: Path) -> None:
    from _helpers import run
    with pytest.raises(SystemExit):
        run(["export", "--project", "shop"])


def test_overwrites_earlier_export_and_removes_stale_bodies(
        home: Path, tmp_path: Path, con, capsys) -> None:
    build_export_fixture(con, tmp_path)
    out = export_ok(capsys, tmp_path / "out")
    assert (out / "docs" / "DOC2-webhook-flow.md").is_file()
    con.execute("DELETE FROM doc_link WHERE project = 'shop' AND doc_id = 2")
    con.execute("DELETE FROM doc WHERE project = 'shop' AND id = 2")
    con.commit()
    con.close()
    out = export_ok(capsys, out)
    assert not (out / "docs" / "DOC2-webhook-flow.md").exists()   # stale body gone
    assert (out / "docs" / "DOC1-ünïcödé-guide.md").is_file()
    assert [row["id"] for row in load_yaml(out / "docs.yaml")] == [1, 3]


# --------------------------------------------------------------------- counters
def test_counters_keep_never_reused_ids(home: Path, tmp_path: Path, con, capsys) -> None:
    make_project()
    for _ in range(3):
        add_requirement()
    con.execute("DELETE FROM requirement WHERE project = 'shop' AND id = 2")
    con.commit()
    con.close()
    out = export_ok(capsys, tmp_path / "out")
    assert load_yaml(out / "project.yaml")["counters"]["R"] == 4
    rows = load_yaml(out / "requirements.yaml")
    assert [row["id"] for row in rows] == [1, 3]
