"""Origin-field tests (HP-33): `origin` = owner | derived | import on R#/F#/S#.

Pinned here: the v6 migration backfill (notes 'origin: owner%' -> owner, else
import) with its `.bak-v5` backup, `derived` as the add default, `--origin` on
add, the refusals (bad value; A#/DOC# before any id is allocated), `set
origin=`, `list --origin` (alone and with `--status`, JSON rows carry it), the
`show` `origin:` line after `notes`, export/import round trip (lossless, two
exports byte-identical, an OLD export without the column imports as `import`)
and the `apply` op path. `store.LATEST_VERSION` is pinned, never a literal.
Nothing here reads docs/.
"""
from __future__ import annotations

import io
import json
import sqlite3
from pathlib import Path

from ruamel.yaml import YAML

import store
from _helpers import (  # `home`/`con` are fixtures imported for discovery
    cli,
    con,
    home,
    make_project,
    row,
    run,
)

PROJECT = "shop"


def _err(capsys) -> str:
    return capsys.readouterr().err


# ------------------------------------------------------------------ v6 migration
def _insert_req(con: sqlite3.Connection, entity_id: int, notes) -> None:
    con.execute(
        "INSERT INTO requirement(project, id, title, statement, priority, status, notes)"
        " VALUES ('demo', ?, ?, 'A buyer can pay by card', 'must', 'draft', ?)",
        (entity_id, f"Pay by card {entity_id}", notes),
    )


def _make_v5(db: Path) -> None:
    """A real v5 db (schema v1 + the v2..v5 migrations) with rows to backfill."""
    db.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(db))
    con.executescript(store.SCHEMA_V1)
    store._migrate_v2_entity_body(con)
    store._migrate_v3_open_doc_kinds(con)
    store._migrate_v4_doc_flags(con)
    con.execute("INSERT INTO project(slug, name, created_at, updated_at)"
                " VALUES ('demo', 'Demo', 't', 't')")
    _insert_req(con, 1, "origin: owner (2026-11-25) ruled on this one")
    _insert_req(con, 2, "key demo")
    _insert_req(con, 3, None)
    con.execute("INSERT INTO feature(project, id, title, layer, status)"
                " VALUES ('demo', 1, 'Stripe integration', 'core', 'planned')")
    con.execute("INSERT INTO spec(project, id, title, summary, status, notes)"
                " VALUES ('demo', 1, 'Payments', 'Use PaymentIntents with 3DS',"
                " 'draft', 'origin: owner')")
    store._migrate_v5_flag_the_prds(con)
    con.commit()
    con.close()
    con = sqlite3.connect(str(db))
    con.execute("PRAGMA user_version = 5")
    con.commit()
    con.close()


def _origin_of(db: Path, table: str, entity_id: int):
    con = sqlite3.connect(str(db))
    try:
        return con.execute(
            f"SELECT origin FROM {table} WHERE project = 'demo' AND id = ?",
            (entity_id,),
        ).fetchone()[0]
    finally:
        con.close()


def test_migration_backfills_origin_and_backs_up_v5(home: Path) -> None:
    db = store.db_path()
    _make_v5(db)
    store.connect().close()
    con = sqlite3.connect(str(db))
    try:
        assert con.execute("PRAGMA user_version").fetchone()[0] == store.LATEST_VERSION
    finally:
        con.close()
    assert _origin_of(db, "requirement", 1) == "owner"
    assert _origin_of(db, "requirement", 2) == "import"
    assert _origin_of(db, "requirement", 3) == "import"
    assert _origin_of(db, "feature", 1) == "import"
    assert _origin_of(db, "spec", 1) == "owner"
    backup = db.parent / f"{db.name}.bak-v5"
    assert backup.is_file()
    bcon = sqlite3.connect(str(backup))
    try:
        assert bcon.execute("PRAGMA user_version").fetchone()[0] == 5
        columns = {r[1] for r in bcon.execute("PRAGMA table_info(requirement)")}
        assert "origin" not in columns                  # the backup predates v6
    finally:
        bcon.close()


# --------------------------------------------------------------- add: flag/default
def test_add_defaults_to_derived(home: Path, capsys) -> None:
    make_project()
    assert run(["add", "R", "--title", "Pay by card",
                "--statement", "A buyer can pay by card", "--priority", "must"]) == 0
    assert run(["add", "F", "--title", "Stripe integration", "--layer", "core"]) == 0
    assert run(["add", "S", "--title", "Payments",
                "--summary", "Use PaymentIntents with 3DS"]) == 0
    assert row(PROJECT, "requirement", 1)["origin"] == "derived"
    assert row(PROJECT, "feature", 1)["origin"] == "derived"
    assert row(PROJECT, "spec", 1)["origin"] == "derived"


def test_add_origin_flag_stores_it(home: Path, capsys) -> None:
    make_project()
    assert run(["add", "R", "--title", "Pay by card",
                "--statement", "A buyer can pay by card", "--priority", "must",
                "--origin", "owner"]) == 0
    assert run(["add", "F", "--title", "Stripe integration", "--layer", "core",
                "--origin", "import"]) == 0
    assert row(PROJECT, "requirement", 1)["origin"] == "owner"
    assert row(PROJECT, "feature", 1)["origin"] == "import"


def test_bad_origin_value_is_refused_naming_the_allowed_values(
    home: Path, capsys
) -> None:
    make_project()
    rc = run(["add", "R", "--title", "Pay by card",
              "--statement", "A buyer can pay by card", "--priority", "must",
              "--origin", "bogus"])
    assert rc == 1
    err = _err(capsys)
    assert "origin" in err
    assert "owner|derived|import" in err
    assert store.list_entities(PROJECT, "requirement") == []      # nothing created


def test_origin_on_a_or_doc_is_refused_before_any_id(home: Path, capsys) -> None:
    make_project()
    assert run(["add", "R", "--title", "Pay by card",
                "--statement", "A buyer can pay by card", "--priority", "must"]) == 0
    rc = run(["add", "A", "--req", "R1", "--text", "Given a card, then it works",
              "--origin", "owner"])
    assert rc == 1
    assert "not a field of add A" in _err(capsys)
    rc = run(["add", "DOC", "--title", "Webhook flow", "--kind", "design",
              "--summary", "How webhooks are processed.", "--origin", "owner"])
    assert rc == 1
    assert "not a field of add DOC" in _err(capsys)
    # nothing was created and no id was consumed: the next rows are still 1
    assert run(["add", "A", "--req", "R1", "--text", "Given a card, then it works"]) == 0
    assert run(["add", "DOC", "--title", "Webhook flow", "--kind", "design",
                "--summary", "How webhooks are processed."]) == 0
    assert row(PROJECT, "criterion", 1)["id"] == 1
    assert row(PROJECT, "doc", 1)["id"] == 1
    assert store.list_entities(PROJECT, "criterion")[1:] == []
    assert store.list_entities(PROJECT, "doc")[1:] == []


# --------------------------------------------------------------------------- set
def test_set_origin_updates_and_validates(home: Path, capsys) -> None:
    make_project()
    assert run(["add", "R", "--title", "Pay by card",
                "--statement", "A buyer can pay by card", "--priority", "must"]) == 0
    capsys.readouterr()                                     # drain the add output
    rc, out, err = cli(capsys, "set", "R1", "origin=owner")
    assert rc == 0, err
    assert out == "R1 updated: origin\n"
    assert row(PROJECT, "requirement", 1)["origin"] == "owner"
    rc, out, err = cli(capsys, "set", "R1", "origin=bogus")
    assert rc == 1
    assert "owner|derived|import" in err


# -------------------------------------------------------------------------- list
def test_list_origin_filter_and_json_rows(home: Path, con, capsys) -> None:
    make_project()
    for title, origin in (("Pay by card", "owner"), ("Refund", "derived"),
                          ("Fraud checks", "import")):
        assert run(["add", "R", "--title", title,
                    "--statement", "A buyer can pay by card", "--priority", "must",
                    "--origin", origin]) == 0
    assert run(["add", "F", "--title", "Stripe integration", "--layer", "core",
                "--origin", "owner"]) == 0
    assert run(["add", "S", "--title", "Payments",
                "--summary", "Use PaymentIntents with 3DS"]) == 0
    assert run(["add", "A", "--req", "R1", "--text", "Given a card, then it works"]) == 0
    capsys.readouterr()                                     # drain the add output

    assert run(["list", "R", "--origin", "owner"]) == 0
    assert [line.split("  ")[0] for line in capsys.readouterr().out.splitlines()] == ["R1"]

    assert run(["list", "R", "--origin", "import"]) == 0
    assert [line.split("  ")[0] for line in capsys.readouterr().out.splitlines()] == ["R3"]

    # combined with --status: both filters must hold
    assert run(["list", "R", "--origin", "owner", "--status", "draft"]) == 0
    assert [line.split("  ")[0] for line in capsys.readouterr().out.splitlines()] == ["R1"]
    assert run(["list", "R", "--origin", "owner", "--status", "approved"]) == 0
    assert capsys.readouterr().out == ""

    # no kind: only R#/F#/S# rows carry origin, so only they can match
    assert run(["list", "--origin", "owner"]) == 0
    assert [line.split("  ")[0] for line in capsys.readouterr().out.splitlines()] == \
        ["R1", "F1"]

    assert run(["list", "R", "--origin", "bogus"]) == 1
    assert "owner|derived|import" in _err(capsys)

    # JSON rows carry the field
    assert run(["list", "R", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    origins = {item["ref"]: item["origin"] for item in payload["items"]}
    assert origins == {"R1": "owner", "R2": "derived", "R3": "import"}


# --------------------------------------------------------------------------- show
def test_show_prints_the_origin_line_after_notes(home: Path, capsys) -> None:
    make_project()
    assert run(["add", "R", "--title", "Pay by card",
                "--statement", "A buyer can pay by card", "--priority", "must",
                "--notes", "keep the provider small", "--origin", "owner"]) == 0
    assert run(["show", "R1"]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert "origin: owner" in lines
    assert lines.index("notes: keep the provider small") < lines.index("origin: owner")
    assert lines.index("origin: owner") < lines.index(
        next(line for line in lines if line.startswith("updated_at:")))
    assert run(["add", "S", "--title", "Payments",
                "--summary", "Use PaymentIntents with 3DS", "--origin", "import"]) == 0
    assert run(["show", "S1"]) == 0
    assert "origin: import" in capsys.readouterr().out.splitlines()


# ----------------------------------------------------------------- export / import
def _dump_yaml(path: Path, data) -> None:
    buffer = io.StringIO()
    yaml = YAML()
    yaml.dump(data, buffer)
    path.write_text(buffer.getvalue(), encoding="utf-8")


def _load_yaml(path: Path):
    return YAML(typ="safe").load(path.read_text(encoding="utf-8"))


def _strip_origin(directory: Path) -> None:
    """Rewrite an export as an OLD one: the R/F/S rows predate the origin column."""
    for name in ("requirements.yaml", "features.yaml", "specs.yaml"):
        path = directory / name
        rows = _load_yaml(path)
        for row_dict in rows:
            row_dict.pop("origin", None)
        _dump_yaml(path, rows)


def _seed_mixed(home: Path, capsys) -> None:
    make_project()
    assert run(["add", "R", "--title", "Pay by card",
                "--statement", "A buyer can pay by card", "--priority", "must",
                "--origin", "owner"]) == 0
    assert run(["add", "R", "--title", "Refunds",
                "--statement", "A buyer can get a refund", "--priority", "should"]) == 0
    assert run(["add", "F", "--title", "Stripe integration", "--layer", "core",
                "--origin", "import"]) == 0
    assert run(["add", "S", "--title", "Payments",
                "--summary", "Use PaymentIntents with 3DS"]) == 0


def test_export_import_round_trip_preserves_origin(home: Path, tmp_path: Path,
                                                   capsys) -> None:
    _seed_mixed(home, capsys)
    out = tmp_path / "out"
    assert run(["export", "--out", str(out)]) == 0
    rows = {r["id"]: r for r in _load_yaml(out / "requirements.yaml")}
    assert rows[1]["origin"] == "owner"
    assert rows[2]["origin"] == "derived"

    assert run(["import", str(out), "--as", "copy"]) == 0
    assert row("copy", "requirement", 1)["origin"] == "owner"
    assert row("copy", "requirement", 2)["origin"] == "derived"
    assert row("copy", "feature", 1)["origin"] == "import"
    assert row("copy", "spec", 1)["origin"] == "derived"

    # two exports of the same data are byte-identical
    out2 = tmp_path / "out2"
    assert run(["export", "--out", str(out2), "--project", "copy"]) == 0
    for name in ("requirements.yaml", "features.yaml", "specs.yaml"):
        assert (out / name).read_bytes() == (out2 / name).read_bytes()


def test_old_export_without_the_column_imports_as_import(home: Path, tmp_path: Path,
                                                         capsys) -> None:
    _seed_mixed(home, capsys)
    out = tmp_path / "out"
    assert run(["export", "--out", str(out)]) == 0
    _strip_origin(out)
    assert run(["import", str(out), "--as", "old"]) == 0
    for kind, entity_id in (("requirement", 1), ("requirement", 2),
                            ("feature", 1), ("spec", 1)):
        assert row("old", kind, entity_id)["origin"] == "import", (kind, entity_id)


# --------------------------------------------------------------------------- apply
def test_apply_ops_carry_origin(home: Path, tmp_path: Path, capsys) -> None:
    make_project()
    plan = tmp_path / "plan.json"
    plan.write_text(json.dumps({
        "project": PROJECT,
        "ops": [
            {"add": {"type": "R", "title": "Pay by card",
                     "statement": "A buyer can pay by card", "priority": "must",
                     "origin": "owner", "as": "rk"}},
            {"add": {"type": "S", "title": "Payments",
                     "summary": "Use PaymentIntents with 3DS"}},
            {"set": {"ref": "$rk", "origin": "import"}},
        ],
    }), encoding="utf-8")
    rc, out, err = cli(capsys, "apply", str(plan))
    assert rc == 0, err
    assert row(PROJECT, "requirement", 1)["origin"] == "import"     # set op landed
    assert row(PROJECT, "spec", 1)["origin"] == "derived"           # plan row without it

    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({
        "project": PROJECT,
        "ops": [{"add": {"type": "A", "req": "R1",
                         "text": "Given a card, then it works", "origin": "owner"}}],
    }), encoding="utf-8")
    rc, out, err = cli(capsys, "apply", str(bad))
    assert rc == 1
    assert "not a field of add A" in err
