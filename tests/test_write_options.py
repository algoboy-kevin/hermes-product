"""`--if-missing`, `--dry-run` and `--json` on the writing verbs (DOC5 sections 2.4, 2.5).

Pinned here: re-running an `add` with `--if-missing` creates nothing the second time and
names the existing ref (per-kind natural key); `--dry-run` runs the REAL verb on a throwaway
copy (same messages and refusals, same ids as the real run would take) and leaves the live
database and body files byte-identical; `--json` prints one object for success and for a
refusal, with the exit code unchanged.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest

import store
from _helpers import add_requirement, add_spec, cli, home, make_project  # noqa: F401  (fixture)


def _fingerprint(project: str = "shop") -> str:
    """Hash of every row of every table plus every body file: any live write changes it."""
    con = store.connect()
    try:
        digest = hashlib.sha256()
        for (name,) in con.execute("SELECT name FROM sqlite_master WHERE type='table'"
                                   " AND name NOT LIKE 'sqlite_%' ORDER BY name").fetchall():
            for text in sorted(repr(tuple(row)) for row in con.execute(f"SELECT * FROM {name}")):
                digest.update(text.encode())
    finally:
        con.close()
    root = store.db_path().parent
    for path in sorted(p for p in root.rglob("*") if p.is_file() and not p.name.startswith("product.db")):
        digest.update(path.name.encode() + path.read_bytes())
    return digest.hexdigest()


@pytest.fixture()
def shop(home: Path) -> str:
    make_project()
    return "shop"


# ------------------------------------------------------------------------ --if-missing
@pytest.mark.parametrize("args", [
    ["S", "--title", "Tracker contract", "--summary", "What the tracker decides in full."],
    ["R", "--title", "Per-second rows", "--statement", "The tracker emits a row per second.",
     "--priority", "must"],
    ["F", "--title", "Market state", "--layer", "core", "--slug", "market-state"],
    ["DOC", "--title", "Run report", "--kind", "report", "--summary", "What the run measured."],
])
def test_if_missing_is_idempotent(shop, capsys, args) -> None:
    rc, out, err = cli(capsys, "add", *args, "--if-missing")
    assert rc == 0, err
    assert out.endswith("created\n")
    ref = out.split()[0]
    before = _fingerprint()
    for _ in range(2):
        rc, out, err = cli(capsys, "add", *args, "--if-missing")
        assert rc == 0, err
        assert out == f"{ref} exists\n"
    assert _fingerprint() == before


def test_plain_add_still_duplicates(shop, capsys) -> None:
    args = ["S", "--title", "Tracker contract", "--summary", "What the tracker decides in full."]
    cli(capsys, "add", *args)
    assert cli(capsys, "add", *args)[1] == "S2 created\n"


def test_if_missing_keys(shop, capsys) -> None:
    cli(capsys, "add", "S", "--title", "Tracker  Contract", "--summary", "What it decides in full.")
    # title match ignores case and whitespace
    assert cli(capsys, "add", "S", "--title", "tracker contract", "--summary", "Other words entirely here.",
               "--if-missing")[1] == "S1 exists\n"
    cli(capsys, "add", "F", "--title", "First name", "--layer", "core", "--slug", "feat")
    # a feature is keyed by slug when given, even if the title differs
    assert cli(capsys, "add", "F", "--title", "Second name", "--layer", "core", "--slug", "feat",
               "--if-missing")[1] == "F1 exists\n"
    add_requirement(shop)
    cli(capsys, "add", "A", "--req", "R1", "--text", "Given x when y then z")
    assert cli(capsys, "add", "A", "--req", "R1", "--text", "given x when  y then z",
               "--if-missing")[1] == "A1 exists\n"
    assert cli(capsys, "add", "A", "--req", "R1", "--text", "A different check",
               "--if-missing")[1] == "A2 created\n"


def test_if_missing_does_not_hide_a_bad_payload(shop, capsys) -> None:
    rc, _out, err = cli(capsys, "add", "S", "--title", "No summary here", "--if-missing")
    assert rc != 0 and "summary is required" in err


# --------------------------------------------------------------------------- --dry-run
def test_dry_run_add_reports_the_real_ref_and_writes_nothing(shop, capsys, tmp_path) -> None:
    add_spec(shop)
    body = tmp_path / "b.md"
    body.write_text("# Body\n", encoding="utf-8")
    before = _fingerprint()
    rc, out, err = cli(capsys, "add", "S", "--title", "Second spec", "--summary",
                       "What the second spec decides.", "--body-file", str(body), "--dry-run")
    assert rc == 0, err
    assert out == "(dry run) S2 created\n"
    assert _fingerprint() == before
    assert [e["ref"] for e in store.list_entities(shop, "spec")] == ["S1"]
    # the real run then takes exactly the id the dry run promised
    assert cli(capsys, "add", "S", "--title", "Second spec", "--summary",
               "What the second spec decides.")[1] == "S2 created\n"


def test_dry_run_doc_add_does_not_write_a_body_file(shop, capsys) -> None:
    before = _fingerprint()
    rc, out, err = cli(capsys, "add", "DOC", "--title", "A report", "--kind", "report",
                       "--summary", "What it measured.", "--body", "text", "--dry-run")
    assert rc == 0, err
    assert out == "(dry run) DOC1 created\n"
    assert _fingerprint() == before
    assert not store.docs_dir(shop).exists() or not list(store.docs_dir(shop).glob("*"))


def test_dry_run_refusals_are_the_real_refusals(shop, capsys) -> None:
    add_requirement(shop)
    before = _fingerprint()
    for argv, message in ((["add", "A", "--req", "R1", "--text", "x y z", "--body", "no"],
                           "A# has no body"),
                          (["set", "R1", "status=done"], "cannot move"),
                          (["link", "F9", "R1"], "F9")):
        rc, out, err = cli(capsys, *argv, "--dry-run")
        assert rc != 0 and message in err, (argv, err)
    assert _fingerprint() == before


def test_dry_run_set_and_link_and_unlink(shop, capsys) -> None:
    add_requirement(shop)
    cli(capsys, "add", "F", "--title", "Feature one", "--layer", "core")
    before = _fingerprint()
    rc, out, err = cli(capsys, "set", "R1", "title=Renamed requirement", "--dry-run")
    assert rc == 0 and out == "(dry run) R1 updated: title\n", err
    rc, out, err = cli(capsys, "link", "F1", "R1", "--dry-run")
    assert rc == 0 and out.startswith("(dry run) "), err
    assert _fingerprint() == before
    assert store.get_entity(shop, "R1")["row"]["title"] != "Renamed requirement"
    assert store.get_entity(shop, "R1")["links"]["features"] == []


def test_dry_run_restores_the_home_and_removes_the_copy(shop, capsys, monkeypatch) -> None:
    import tempfile
    seen: list[str] = []
    real = tempfile.mkdtemp

    def spy(*args, **kwargs):
        path = real(*args, **kwargs)
        seen.append(path)
        return path

    monkeypatch.setattr(tempfile, "mkdtemp", spy)
    before_env = os.environ["HERMES_HOME"]
    cli(capsys, "add", "S", "--title", "Temp check", "--summary", "What this temp spec decides.",
        "--dry-run")
    assert os.environ["HERMES_HOME"] == before_env
    assert len(seen) == 1 and not Path(seen[0]).exists()


def test_dry_run_import_writes_nothing(shop, capsys, tmp_path, monkeypatch) -> None:
    add_spec(shop, body="# Body\n")
    out_dir = tmp_path / "exp"
    assert cli(capsys, "export", "--out", str(out_dir), "--project", "shop")[0] == 0
    other = tmp_path / "other"
    monkeypatch.setenv("HERMES_HOME", str(other))
    rc, out, err = cli(capsys, "import", str(out_dir), "--dry-run")
    assert rc == 0, err
    assert out.startswith("(dry run) imported shop:")
    assert not (other / "product" / "product.db").exists()      # the real home was never created
    monkeypatch.setenv("HERMES_HOME", str(other))
    assert store.list_projects() == []


# ----------------------------------------------------------------------------- --json
def test_json_success_shape(shop, capsys) -> None:
    rc, out, err = cli(capsys, "add", "S", "--title", "Tracker contract", "--summary",
                       "What the tracker decides in full.", "--json")
    assert rc == 0, err
    payload = json.loads(out)
    assert payload == {"ok": True, "exit": 0, "dry_run": False, "ref": "S1",
                       "action": "created", "lines": ["S1 created"]}
    rc, out, _err = cli(capsys, "add", "S", "--title", "Tracker contract", "--summary", "x",
                        "--if-missing", "--json")
    assert json.loads(out)["action"] == "exists" and rc == 0


def test_json_with_dry_run_and_set_and_status(shop, capsys) -> None:
    add_spec(shop)
    rc, out, _e = cli(capsys, "set", "S1", "summary=A new and better summary line.", "--json",
                      "--dry-run")
    payload = json.loads(out)
    assert rc == 0 and payload["dry_run"] is True and payload["action"] == "updated"
    rc, out, _e = cli(capsys, "set", "S1", "status=approved", "--json")
    payload = json.loads(out)
    assert rc == 1 and payload["ok"] is False and "cannot move" in payload["error"]
    assert payload["exit"] == 1


def test_json_refusal_keeps_the_exit_code(shop, capsys) -> None:
    rc, out, err = cli(capsys, "add", "S", "--title", "No summary here", "--json")
    payload = json.loads(out)
    assert rc == 1 and err == ""
    assert payload["ok"] is False and "summary is required" in payload["error"]


def test_json_on_link_and_fold_docs(shop, capsys) -> None:
    add_requirement(shop)
    cli(capsys, "add", "F", "--title", "Feature one", "--layer", "core")
    out = cli(capsys, "link", "F1", "R1", "--json")[1]
    assert json.loads(out)["action"] == "linked"
    out = cli(capsys, "fold-docs", "--json")[1]
    payload = json.loads(out)
    assert payload["ok"] is True and payload["lines"][-1].startswith("fold-docs shop: nothing to fold")
