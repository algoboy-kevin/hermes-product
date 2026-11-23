"""The entity body (schema v2): R#, F# and S# carry the text that DEFINES them.

Pinned here: the v1 -> v2 migration (column added, data kept, backup taken); `add`
and `set` with --body / --body-file / body=; the REFUSALS that replace the old
silent drop (`add S --body-file` said "created" and kept nothing; A# has no body;
--body-ref is DOC#-only); retrieval cost (list, trace, next and the plain `show`
never print the text, only a size line; `show --body` reads it bounded and by
section); the history row; the agent tool; and the S3 export/import round trip.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

import model
import store
from _helpers import (  # `home`/`con` are fixtures imported for discovery
    add_criterion, add_feature, add_requirement, add_spec, cli, con, history_rows, home,
    make_project,
)
from agent_tools import handle_product_show

BODY = (
    "# Contract\n"
    "\n"
    "The tracker emits one row per second.\n"
    "\n"
    "## Parity\n"
    "\n"
    "Live rows equal research rows.\n"
    "\n"
    "## Limits\n"
    "\n"
    "No allocations on the hot path.\n"
)


@pytest.fixture()
def shop(home: Path) -> str:
    make_project()
    return "shop"


def _body_file(tmp_path: Path, text: str = BODY, name: str = "body.md") -> Path:
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


# ----------------------------------------------------------------------- migration
def test_fresh_install_has_a_body_column_on_r_f_s_only(home: Path) -> None:
    store.connect().close()
    raw = sqlite3.connect(str(store.db_path()))
    try:
        for table, expected in (("requirement", True), ("feature", True), ("spec", True),
                                ("criterion", False), ("doc", False)):
            columns = {row[1] for row in raw.execute(f"PRAGMA table_info({table})")}
            assert ("body" in columns) is expected, table
    finally:
        raw.close()


def test_v1_db_migrates_keeping_rows_and_backing_up(home: Path) -> None:
    path = store.db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = sqlite3.connect(str(path))
    raw.executescript(store.SCHEMA_V1)
    raw.execute("INSERT INTO project(slug, name, created_at, updated_at)"
                " VALUES ('old', 'Old', 't', 't')")
    raw.execute("INSERT INTO spec(project, id, title, summary, status)"
                " VALUES ('old', 1, 'Tracker', 'One row per second.', 'draft')")
    raw.execute("PRAGMA user_version = 1")
    raw.commit()
    raw.close()

    store.connect().close()

    migrated = sqlite3.connect(str(path))
    try:
        assert migrated.execute("PRAGMA user_version").fetchone()[0] == 2
        row = migrated.execute("SELECT title, summary, body FROM spec").fetchone()
        assert row == ("Tracker", "One row per second.", None)
    finally:
        migrated.close()
    backup = path.parent / f"{path.name}.bak-v1"
    assert backup.is_file()
    old = sqlite3.connect(str(backup))
    try:
        assert "body" not in {r[1] for r in old.execute("PRAGMA table_info(spec)")}
    finally:
        old.close()


# --------------------------------------------------------------------------- add
@pytest.mark.parametrize("kind,args,ref", [
    ("R", ["--title", "Per-second rows", "--statement", "The tracker emits a row per second.",
           "--priority", "must"], "R1"),
    ("F", ["--title", "Market state", "--layer", "core"], "F1"),
    ("S", ["--title", "Tracker contract", "--summary", "What the tracker decides."], "S1"),
])
def test_add_with_body_text_and_with_body_file(shop, capsys, tmp_path, kind, args, ref) -> None:
    rc, out, err = cli(capsys, "add", kind, *args, "--body", BODY)
    assert rc == 0, err
    assert out == f"{ref} created\n"
    assert store.get_entity(shop, ref, with_body=True)["row"]["body"] == BODY

    other = f"{ref[0]}2"
    rc, out, err = cli(capsys, "add", kind, *args, "--body-file", str(_body_file(tmp_path)))
    assert rc == 0, err
    assert out == f"{other} created\n"
    assert store.get_entity(shop, other, with_body=True)["row"]["body"] == BODY


def test_add_s_body_file_is_kept_not_silently_dropped(shop, capsys, tmp_path) -> None:
    """The adoption defect: `add S --body-file F` printed 'S1 created' and stored nothing."""
    rc, out, err = cli(capsys, "add", "S", "--title", "Probe spec", "--summary",
                       "One real sentence.", "--body-file", str(_body_file(tmp_path)))
    assert rc == 0, err
    assert store.get_entity(shop, "S1", with_body=True)["row"]["body"] == BODY


def test_add_without_body_stores_null(shop) -> None:
    add_spec(shop)
    entry = store.get_entity(shop, "S1")
    assert entry["row"]["body_chars"] == 0
    assert "body" not in entry["row"]
    assert store.get_entity(shop, "S1", with_body=True)["row"]["body"] is None


def test_blank_body_is_no_body(shop, capsys) -> None:
    rc, _out, err = cli(capsys, "add", "S", "--title", "Blank body", "--summary", "s.",
                        "--body", "  \n ")
    assert rc == 0, err
    assert store.get_entity(shop, "S1", with_body=True)["row"]["body"] is None


def test_add_criterion_with_a_body_is_refused_and_takes_no_id(shop, capsys, tmp_path) -> None:
    add_requirement(shop)
    for flag, value in (("--body", "context"), ("--body-file", str(_body_file(tmp_path)))):
        rc, out, err = cli(capsys, "add", "A", "--req", "R1", "--text", "Given x then y",
                           flag, value)
        assert rc != 0 and out == ""
        assert "A# has no body" in err
    assert store.list_entities(shop, "criterion") == []
    add_criterion(shop)                       # the first id is still A1: nothing was consumed
    assert store.get_entity(shop, "A1")["id"] == 1


def test_add_body_ref_is_doc_only(shop, capsys) -> None:
    rc, out, err = cli(capsys, "add", "S", "--title", "Ref body", "--summary", "s.",
                       "--body-ref", "docs/x.md")
    assert rc != 0 and out == ""
    assert "--body-ref is only for DOC#" in err
    assert store.list_entities(shop, "spec") == []


def test_add_two_body_options_are_refused(shop, capsys, tmp_path) -> None:
    rc, out, err = cli(capsys, "add", "S", "--title", "Two bodies", "--summary", "s.",
                       "--body", "a", "--body-file", str(_body_file(tmp_path)))
    assert rc != 0
    assert "pass only one of" in err
    assert store.list_entities(shop, "spec") == []


def test_add_missing_body_file_is_named_and_creates_nothing(shop, capsys, tmp_path) -> None:
    rc, _out, err = cli(capsys, "add", "S", "--title", "Missing file", "--summary", "s.",
                        "--body-file", str(tmp_path / "nope.md"))
    assert rc != 0
    assert "nope.md" in err
    assert store.list_entities(shop, "spec") == []


# --------------------------------------------------------------------------- set
def test_set_body_by_key_and_by_file_logs_history_once_per_change(shop, capsys, tmp_path) -> None:
    add_spec(shop)
    rc, out, err = cli(capsys, "set", "S1", f"body={BODY}")
    assert rc == 0, err
    assert out == "S1 updated: body\n"
    assert [h["change"] for h in history_rows(shop, "spec", 1)] == ["body changed"]

    rc, out, err = cli(capsys, "set", "S1", "--body-file", str(_body_file(tmp_path)))
    assert rc == 0, err
    assert out == "S1 updated: body\n"
    # the same text again is not a change: no second history row
    assert [h["change"] for h in history_rows(shop, "spec", 1)] == ["body changed"]

    rc, _out, err = cli(capsys, "set", "S1", "--body-file",
                        str(_body_file(tmp_path, BODY + "\nMore.\n", "v2.md")),
                        "--reason", "added the limits note")
    assert rc == 0, err
    rows = history_rows(shop, "spec", 1)
    assert [h["change"] for h in rows] == ["body changed", "body changed"]
    assert rows[1]["reason"] == "added the limits note"
    assert store.get_entity(shop, "S1", with_body=True)["row"]["body"].endswith("More.\n")


def test_set_empty_body_clears_it(shop, capsys) -> None:
    add_spec(shop, body=BODY)
    rc, _out, err = cli(capsys, "set", "S1", "body=")
    assert rc == 0, err
    assert store.get_entity(shop, "S1", with_body=True)["row"]["body"] is None
    assert [h["change"] for h in history_rows(shop, "spec", 1)] == ["body changed"]


@pytest.mark.parametrize("make,ref", [(add_requirement, "R1"), (add_feature, "F1"),
                                      (add_spec, "S1")])
def test_set_body_works_on_r_f_s(shop, capsys, make, ref) -> None:
    make(shop)
    rc, _out, err = cli(capsys, "set", ref, f"body={BODY}")
    assert rc == 0, err
    assert store.get_entity(shop, ref, with_body=True)["row"]["body"] == BODY


def test_set_body_on_a_criterion_is_refused(shop, capsys, tmp_path) -> None:
    add_requirement(shop)
    add_criterion(shop)
    rc, _out, err = cli(capsys, "set", "A1", "body=context")
    assert rc != 0 and "unknown field 'body'" in err
    rc, _out, err = cli(capsys, "set", "A1", "--body-file", str(_body_file(tmp_path)))
    assert rc != 0 and "A# has no body" in err


def test_set_body_key_and_body_file_together_are_refused(shop, capsys, tmp_path) -> None:
    add_spec(shop)
    rc, _out, err = cli(capsys, "set", "S1", "body=x", "--body-file", str(_body_file(tmp_path)))
    assert rc != 0 and "either body=... or --body-file" in err
    assert store.get_entity(shop, "S1")["row"]["body_chars"] == 0


def test_set_body_does_not_touch_the_status_machine(shop, capsys) -> None:
    add_spec(shop)
    cli(capsys, "set", "S1", f"body={BODY}")
    assert store.get_entity(shop, "S1")["row"]["status"] == "draft"


# ---------------------------------------------------------------- retrieval cost (R5)
def test_show_prints_a_size_line_never_the_text(shop, capsys) -> None:
    add_spec(shop, body=BODY)
    rc, out, err = cli(capsys, "show", "S1")
    assert rc == 0, err
    assert f"body: {len(BODY)} chars (show S1 --body)" in out
    assert "tracker emits one row" not in out
    assert "--- body ---" not in out


def test_show_without_a_body_has_no_body_line(shop, capsys) -> None:
    add_spec(shop)
    rc, out, _err = cli(capsys, "show", "S1")
    assert rc == 0 and "body" not in out


def test_show_body_prints_the_text_after_history(shop, capsys) -> None:
    add_spec(shop, body=BODY)
    rc, out, err = cli(capsys, "show", "S1", "--body")
    assert rc == 0, err
    head, _sep, text = out.partition("--- body ---\n")
    assert text == BODY
    assert head.index("history:") < len(head)
    assert "[truncated" not in out


def test_show_body_is_bounded_and_reports_truncation(shop, capsys) -> None:
    add_spec(shop, body="x" * 500)
    rc, out, err = cli(capsys, "show", "S1", "--body", "--max-chars", "100")
    assert rc == 0, err
    assert out.count("x") == 100
    assert "[truncated: showed 100 of 500 chars]" in out


def test_show_body_section_and_missing_section(shop, capsys) -> None:
    add_spec(shop, body=BODY)
    rc, out, err = cli(capsys, "show", "S1", "--body", "--section", "Parity")
    assert rc == 0, err
    text = out.partition("--- body ---\n")[2]
    assert text.startswith("## Parity") and "Live rows equal research rows." in text
    assert "No allocations" not in text

    rc, _out, err = cli(capsys, "show", "S1", "--body", "--section", "Nope")
    assert rc != 0
    assert "no section 'Nope'" in err and "Contract" in err and "Limits" in err


def test_show_body_of_an_entry_without_one_says_so(shop, capsys) -> None:
    add_feature(shop)
    rc, out, err = cli(capsys, "show", "F1", "--body")
    assert rc == 0, err
    assert out.endswith("--- body: none ---\n")


def test_show_body_on_a_criterion_is_refused(shop, capsys) -> None:
    add_requirement(shop)
    add_criterion(shop)
    rc, _out, err = cli(capsys, "show", "A1", "--body")
    assert rc != 0 and "has no body" in err


def test_show_section_or_max_chars_need_body(shop, capsys) -> None:
    add_spec(shop, body=BODY)
    rc, _out, err = cli(capsys, "show", "S1", "--section", "Parity")
    assert rc != 0 and "only apply with --body" in err


def test_show_max_chars_range(shop, capsys) -> None:
    add_spec(shop, body=BODY)
    rc, _out, err = cli(capsys, "show", "S1", "--body", "--max-chars", "0")
    assert rc != 0 and "1..100000" in err


def test_show_body_works_for_a_doc_too(shop, capsys) -> None:
    from _helpers import add_doc
    add_doc(shop, body=BODY)
    rc, out, err = cli(capsys, "show", "DOC1", "--body")
    assert rc == 0, err
    assert out.partition("--- body ---\n")[2] == BODY


def test_list_trace_and_next_never_print_a_body(shop, capsys, con) -> None:
    add_requirement(shop, body=BODY)
    add_feature(shop, body=BODY)
    add_spec(shop, body=BODY)
    for argv in (["list"], ["list", "R"], ["list", "F"], ["list", "S"], ["trace", "R1"],
                 ["trace", "F1"], ["trace", "S1"], ["next"]):
        rc, out, err = cli(capsys, *argv)
        assert rc == 0, (argv, err)
        assert "tracker emits one row" not in out, argv
        assert "Live rows equal" not in out, argv


def test_store_rows_carry_size_not_text_by_default(shop) -> None:
    add_requirement(shop, body=BODY)
    add_feature(shop, body=BODY)
    add_spec(shop, body=BODY)
    for item in store.list_entities(shop):
        if item["kind"] in model.BODY_KINDS:
            assert "body" not in item, item["ref"]
            assert item["body_chars"] == len(BODY), item["ref"]
    changed = store.update_entity(shop, "S1", {"body": BODY + "x"})
    assert "body" not in changed["row"] and changed["row"]["body_chars"] == len(BODY) + 1


# ------------------------------------------------------------------------ agent tool
def test_agent_show_reads_the_body(shop) -> None:
    add_spec(shop, body=BODY)
    import json
    plain = json.loads(handle_product_show({"ref": "S1"}))
    assert plain["ok"] and "--- body ---" not in plain["output"]
    full = json.loads(handle_product_show({"ref": "S1", "body": True, "section": "Limits"}))
    assert full["ok"], full
    assert "No allocations on the hot path." in full["output"]
    assert "Live rows equal" not in full["output"]
    short = json.loads(handle_product_show({"ref": "S1", "body": True, "max_chars": 10}))
    assert "[truncated: showed 10 of" in short["output"]


# --------------------------------------------------------------------- export / import
TRICKY = (
    "# Title\n\n"
    "Status: this line is body text, not state.\n"
    "- bullet: with colon\n"
    "  indented  \n"                     # trailing spaces survive
    "---\n"
    "unicode: ünïcödé ✓ 日本語\n"
    "```sh\n# not a heading\n```\n"
    "no trailing newline"
)


def _export(capsys, out: Path, project: str = "shop") -> Path:
    rc, _text, err = cli(capsys, "export", "--out", str(out), "--project", project)
    assert rc == 0, err
    return out


def _files(root: Path) -> dict:
    return {str(p.relative_to(root)): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}


def test_export_import_roundtrip_is_lossless_and_byte_stable(shop, capsys, tmp_path,
                                                             monkeypatch) -> None:
    add_requirement(shop, body=BODY)
    add_feature(shop, body=TRICKY)
    add_spec(shop, body=TRICKY + "\n\n")
    add_spec(shop, title="Second spec")          # no body: key omitted, imports as NULL

    first = _export(capsys, tmp_path / "first")
    again = _export(capsys, tmp_path / "again")
    assert _files(first) == _files(again)                         # deterministic
    assert "body:" in (first / "specs.yaml").read_text(encoding="utf-8")

    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "other-home"))
    rc, _out, err = cli(capsys, "import", str(first))
    assert rc == 0, err
    for ref, expected in (("R1", BODY), ("F1", TRICKY), ("S1", TRICKY + "\n\n"), ("S2", None)):
        assert store.get_entity("shop", ref, with_body=True)["row"]["body"] == expected, ref
    second = _export(capsys, tmp_path / "second")
    assert _files(second) == _files(first)                        # A22: re-export identical


def test_import_of_an_export_written_before_bodies_still_works(shop, capsys, tmp_path,
                                                                monkeypatch) -> None:
    add_spec(shop)                                # no body: the export has no `body` key at all
    out = _export(capsys, tmp_path / "old")
    assert "body" not in (out / "specs.yaml").read_text(encoding="utf-8")
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "fresh-home"))
    rc, _out, err = cli(capsys, "import", str(out))
    assert rc == 0, err
    assert store.get_entity("shop", "S1")["row"]["body_chars"] == 0


def test_import_merge_reports_a_body_difference_as_a_conflict(shop, capsys, tmp_path,
                                                               monkeypatch) -> None:
    add_spec(shop, body=BODY)
    out = _export(capsys, tmp_path / "exp")
    store.update_entity(shop, "S1", {"body": BODY + "edited locally\n"})
    rc, _out, err = cli(capsys, "import", str(out), "--as", "shop")
    assert rc == 3                                # S3: a differing row is never overwritten
    assert "body" in (_out + err)
    assert store.get_entity(shop, "S1", with_body=True)["row"]["body"].endswith("locally\n")


def test_import_rejects_a_non_text_body(shop, capsys, tmp_path, monkeypatch) -> None:
    from ruamel.yaml import YAML
    add_spec(shop, body=BODY)
    out = _export(capsys, tmp_path / "exp")
    path = out / "specs.yaml"
    yaml = YAML()
    data = yaml.load(path.read_text(encoding="utf-8"))
    data[0]["body"] = [1, 2]
    with path.open("w", encoding="utf-8") as handle:
        yaml.dump(data, handle)
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "bad-home"))
    rc, _out, err = cli(capsys, "import", str(out))
    assert rc != 0
    assert "body must be text" in err and "specs.yaml" in err
