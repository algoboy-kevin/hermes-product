"""`apply PLAN`: a whole batch of add / set / link / unlink in ONE process (card HP-14).

Pinned here:

* the example plan of S4 section 2 runs as one process: F, R, S with a plan-relative
  ``body_file``, both links and a status ``set`` (test a);
* a rerun is idempotent: no created rows, no new history, links report ``already linked``
  and an unchanged status is a no-op rather than a failed batch (test b);
* a bad op refuses the WHOLE batch: exit 1 naming the op, and the store is byte-identical
  with no ``product.db.bak-apply-*`` copy (test c);
* static errors (unknown alias, duplicate alias, unknown field, two op keys, ``as`` on a
  link) are refused before anything touches the store (test d);
* ``--dry-run`` and ``--json`` behave like every other writing verb (tests e, f);
* a JSON plan is a plan, and ``--project`` disagreeing with the plan is refused (test g);
* 150 spec adds and 300 dependency links run in one process, well under the loop's cost
  (test h);
* an alias on a row that already exists binds to that row's ref (test i).
"""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

import pytest

import store
from _helpers import cli, history_rows, home, make_project  # noqa: F401  (fixture)

KINDS = ("R", "A", "F", "S", "DOC")

EXAMPLE = """\
project: demo
ops:
  - add: {type: F, as: pay, title: "Card payments", slug: pay, layer: core,
          description: "Charge a card at checkout"}
  - add: {type: R, as: r1, title: "Pay by card", statement: "A buyer can pay by card",
          priority: must}
  - link: {a: $r1, b: $pay}
  - add: {type: S, as: s1, title: "Charge flow", summary: "How a card is charged.",
          body_file: specs/charge.md}
  - link: {a: $s1, b: $r1}
  - set: {ref: $r1, status: approved, rationale: "owner requirement"}
"""

BODY = "# Charge flow\n\nA card is charged once per order.\n"

REFUSED = """\
project: demo
ops:
  - add: {type: F, as: pay, title: "Card payments", slug: pay, layer: core}
  - add: {type: R, as: r1, title: "Pay by card", statement: "A buyer can pay by card",
          priority: must}
  - link: {a: $r1, b: $pay}
  - add: {type: S, as: s1, title: "Charge flow", summary: "How a card is charged."}
  - set: {ref: $r1, status: in_progress}
"""


# ----------------------------------------------------------------------- helpers
def write_plan(base: Path, name: str, text: str, *, body: str | None = None) -> Path:
    """Write one plan file (and its plan-relative body) under ``base``."""
    path = base / name
    path.write_text(text, encoding="utf-8")
    if body is not None:
        (base / "specs").mkdir(exist_ok=True)
        (base / "specs" / "charge.md").write_text(body, encoding="utf-8")
    return path


def backups() -> list[Path]:
    """Every ``product.db.bak-apply-*`` copy in the live product directory."""
    return sorted(store.db_path().parent.glob("product.db.bak-apply-*"))


def snapshot(capsys, project: str) -> dict[str, str]:
    """The `list --json` output of every kind: one comparable picture of the store."""
    return {kind: cli(capsys, "list", kind, "--project", project, "--json")[1]
            for kind in KINDS}


def summary_line(out: str) -> str:
    lines = [line for line in out.splitlines() if line.startswith("apply ")]
    assert len(lines) == 1, out
    return lines[0]


# ------------------------------------------------------- a. the example plan, one process
def test_example_plan_creates_everything_in_one_process(home, capsys, tmp_path) -> None:
    make_project("demo")
    plan = write_plan(tmp_path, "plan.yaml", EXAMPLE, body=BODY)

    rc, out, err = cli(capsys, "apply", str(plan))

    assert rc == 0, err
    assert "F1 created (alias pay)" in out
    assert "R1 created (alias r1)" in out
    assert "S1 created (alias s1)" in out
    line = summary_line(out)
    assert "3 created" in line and "2 linked" in line and "1 set" in line, line
    rc, shown, err = cli(capsys, "show", "S1", "--body", "--project", "demo")
    assert rc == 0, err
    assert "A card is charged once per order." in shown
    rc, shown, err = cli(capsys, "show", "R1", "--project", "demo")
    assert rc == 0, err
    assert "status: approved" in shown
    backup_lines = [row for row in out.splitlines() if row.startswith("backup: ")]
    assert len(backup_lines) == 1, out
    path = Path(backup_lines[0].split("backup: ", 1)[1])
    assert path.is_file() and path.name.startswith("product.db.bak-apply-")
    con = sqlite3.connect(str(path))
    try:
        assert con.execute("SELECT count(*) FROM sqlite_master WHERE type = 'table'"
                           " AND name = 'requirement'").fetchone()[0] == 1
        assert con.execute("SELECT count(*) FROM requirement").fetchone()[0] == 0   # pre-write
    finally:
        con.close()


# ------------------------------------------------------------------ b. idempotent rerun
def test_rerun_writes_nothing_and_reports_already_linked(home, capsys, tmp_path) -> None:
    make_project("demo")
    plan = write_plan(tmp_path, "plan.yaml", EXAMPLE, body=BODY)
    assert cli(capsys, "apply", str(plan))[0] == 0
    before = snapshot(capsys, "demo")
    history = {ref: history_rows("demo", kind, 1)
               for ref, kind in (("R1", "requirement"), ("F1", "feature"), ("S1", "spec"))}

    rc, out, err = cli(capsys, "apply", str(plan))

    assert rc == 0, err
    line = summary_line(out)
    assert "0 created" in line, line
    assert "3 existing" in line, line
    assert "2 already linked" in line, line
    assert "0 set" in line, line
    assert snapshot(capsys, "demo") == before
    for ref, kind in (("R1", "requirement"), ("F1", "feature"), ("S1", "spec")):
        assert history_rows("demo", kind, 1) == history[ref]
    # parent review: a pure re-run changes nothing, so it leaves no new backup and no `backup:` line
    assert len(backups()) == 1 and "backup:" not in out


# ----------------------------------------------------------------- c. atomic refusal
def test_a_bad_op_refuses_the_batch_and_writes_nothing(home, capsys, tmp_path) -> None:
    make_project("demo")
    assert cli(capsys, "add", "R", "--title", "Existing", "--statement",
               "A buyer sees the price", "--priority", "must", "--project", "demo")[0] == 0
    plan = write_plan(tmp_path, "plan.yaml", REFUSED)
    before = snapshot(capsys, "demo")

    rc, out, err = cli(capsys, "apply", str(plan))

    assert rc == 1
    assert "op 5" in err, err
    assert "nothing was written" in err, err
    assert snapshot(capsys, "demo") == before
    assert backups() == []


# -------------------------------------------------------------------- d. static errors
@pytest.mark.parametrize("text,index,needle", [
    ("ops:\n  - add: {type: F, title: 'Card payments', layer: core}\n"
     "  - link: {a: $nope, b: F1}\n", 2, "unknown alias $nope"),
    ("ops:\n  - add: {type: R, as: x, title: 'One', statement: 'A buyer pays',"
     " priority: must}\n  - add: {type: R, as: x, title: 'Two', statement: 'A buyer pays',"
     " priority: must}\n", 2, "already defined"),
    ("ops:\n  - add: {type: F, title: 'Card payments', layer: core, statement: 'nope'}\n",
     1, "not a field of add F"),
    ("ops:\n  - add: {type: R, title: 'One', statement: 'A buyer pays', priority: must}\n"
     "    set: {ref: R1, status: approved, rationale: 'x'}\n", 1, "exactly one"),
    ("ops:\n  - link: {a: R1, b: F1, as: x}\n", 1, "only valid on add"),
])
def test_static_errors_name_the_op_and_write_nothing(home, capsys, tmp_path, text, index,
                                                     needle) -> None:
    make_project("demo")
    plan = write_plan(tmp_path, "plan.yaml", text)
    before = snapshot(capsys, "demo")

    rc, out, err = cli(capsys, "apply", str(plan))

    assert rc == 1, out
    assert f"op {index}" in err, err
    assert needle in err, err
    assert snapshot(capsys, "demo") == before
    assert backups() == []


# ------------------------------------------------------------------------- e. dry run
def test_dry_run_writes_nothing_and_keeps_no_backup(home, capsys, tmp_path) -> None:
    make_project("demo")
    plan = write_plan(tmp_path, "plan.yaml", EXAMPLE, body=BODY)
    before = snapshot(capsys, "demo")

    rc, out, err = cli(capsys, "apply", str(plan), "--dry-run")

    assert rc == 0, err
    assert out.splitlines()
    assert all(line.startswith("(dry run) ") for line in out.splitlines()), out
    assert "3 created" in out
    assert snapshot(capsys, "demo") == before
    assert backups() == []


# ----------------------------------------------------------------------------- f. json
def test_json_prints_one_object_with_the_summary_line(home, capsys, tmp_path) -> None:
    make_project("demo")
    plan = write_plan(tmp_path, "plan.yaml", EXAMPLE, body=BODY)

    rc, out, err = cli(capsys, "apply", str(plan), "--json")

    assert rc == 0, err
    payload = json.loads(out)
    assert payload["ok"] is True and payload["exit"] == 0
    assert any(line.startswith("apply demo: ") and "3 created" in line
               for line in payload["lines"]), payload


# ---------------------------------------------------- g. JSON plans and --project rules
def test_a_json_plan_is_a_plan(home, capsys, tmp_path) -> None:
    make_project("demo")
    data = {"project": "demo", "ops": [
        {"add": {"type": "R", "as": "j1", "title": "Pay by card",
                 "statement": "A buyer can pay by card", "priority": "must"}},
    ]}
    plan = tmp_path / "plan.json"
    plan.write_text(json.dumps(data), encoding="utf-8")

    rc, out, err = cli(capsys, "apply", str(plan))

    assert rc == 0, err
    assert "R1 created (alias j1)" in out


def test_project_must_match_the_plans_project(home, capsys, tmp_path) -> None:
    make_project("demo")
    make_project("other")
    plan = write_plan(tmp_path, "plan.yaml", EXAMPLE, body=BODY)

    rc, out, err = cli(capsys, "apply", str(plan), "--project", "other")

    assert rc == 1
    assert "other" in err and "demo" in err, err
    assert backups() == []


# --------------------------------------------------------------------------- h. scale
def test_150_specs_and_300_links_run_in_one_process(home, capsys, tmp_path) -> None:
    make_project("demo")
    ops: list[dict] = []
    for number in range(1, 151):
        ops.append({"add": {"type": "S", "as": f"s{number}",
                            "title": f"Spec {number}", "summary": f"Summary {number}"}})
    pairs = ([(number, number - 1) for number in range(2, 151)]
             + [(number, number - 2) for number in range(3, 151)]
             + [(150, 147), (149, 146), (148, 145)])
    assert len(pairs) == 300
    for first, needs in pairs:
        ops.append({"link": {"a": f"$s{first}", "needs": f"$s{needs}"}})
    plan = tmp_path / "scale.json"
    plan.write_text(json.dumps({"project": "demo", "ops": ops}), encoding="utf-8")

    started = time.monotonic()
    rc, out, err = cli(capsys, "apply", str(plan))
    elapsed = time.monotonic() - started

    assert rc == 0, err
    line = summary_line(out)
    assert "450 ops" in line and "150 created" in line, line
    assert "300 linked" in line, line
    assert elapsed < 40, f"apply took {elapsed:.1f}s"


# ------------------------------------------------- i. an alias on an existing row links it
def test_alias_binds_to_the_existing_row(home, capsys, tmp_path) -> None:
    make_project("demo")
    assert cli(capsys, "add", "F", "--title", "Card payments", "--layer", "core",
               "--slug", "pay", "--project", "demo")[0] == 0
    assert cli(capsys, "add", "R", "--title", "Pay by card", "--statement",
               "A buyer can pay by card", "--priority", "must", "--project", "demo")[0] == 0
    plan = write_plan(tmp_path, "plan.yaml", """\
project: demo
ops:
  - add: {type: R, as: x, title: "Pay by card", statement: "A buyer can pay by card",
          priority: must}
  - link: {a: $x, b: F1}
""")

    rc, out, err = cli(capsys, "apply", str(plan))

    assert rc == 0, err
    assert "R1 exists (alias x)" in out
    rc, shown, err = cli(capsys, "show", "R1", "--project", "demo")
    assert rc == 0, err
    assert "links: F:1" in shown, shown
    assert "2 created" not in out
