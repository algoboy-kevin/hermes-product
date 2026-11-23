"""`adopt DIR`: a repository's Markdown becomes S# bodies and DOCs without a hand-written importer.

Pinned here: classification by path and name; the title and the one-line summary the
proposal derives (metadata lines skipped, at most two sentences / 400 chars, a visible
placeholder when there is no prose); the plan writes nothing; `--apply` writes exactly the
plan and a rerun reports `exists` instead of duplicating; a changed file is `changed`
until `--update`; one refused row does not stop the others; and the whole pass over a
realistic tree is one process call.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

import adopt
import lint
import store
from _helpers import cli, home, make_project  # noqa: F401  (fixture)


# ---------------------------------------------------------------- pure: classification
@pytest.mark.parametrize("rel,expected", [
    ("docs/specs/S1-core-primitives.md", ("S", None)),
    ("docs/specs/REC1-recording-spec.md", ("S", None)),
    ("ops/S19-SIG1-ACCEPTANCE.md", ("S", None)),
    ("docs/PRD.md", ("DOC", "prd")),
    ("docs/product-requirements.md", ("DOC", "prd")),
    ("docs/RUNBOOK-hl-mainnet.md", ("DOC", "runbook")),
    ("docs/DEPLOY-HL-MAINNET.md", ("DOC", "runbook")),
    ("docs/FINDINGS-res005.md", ("DOC", "report")),
    ("docs/DEFECTS-recording-2026-11-19.md", ("DOC", "report")),
    ("docs/reviews/S32-hip3-routing-audit.md", ("S", None)),      # S<n>- name wins
    ("docs/reviews/GMD-20-quiet-venue.md", ("DOC", "design")),
    ("docs/ARCHITECTURE.md", ("DOC", "design")),
    ("docs/PLAN.md", ("DOC", "design")),
    ("docs/REQUEST-BUDGET.md", ("DOC", "design")),
    ("docs/api.md", ("DOC", "api")),
    ("docs/notes.md", ("DOC", "context")),
    ("docs/planet-facts.md", ("DOC", "context")),                  # 'plan' is a whole word only
    ("README.md", ("skip", None)),
    ("metrics/README.md", ("skip", None)),
    ("AGENTS.md", ("skip", None)),
    ("CHANGELOG.md", ("skip", None)),
    ("LICENSE.md", ("skip", None)),
])
def test_classify(rel, expected) -> None:
    target, kind, _why = adopt.classify(rel)
    assert (target, kind) == expected


def test_a_second_prd_is_context() -> None:
    assert adopt.classify("docs/PRD.md", has_prd=True)[:2] == ("DOC", "context")


# ------------------------------------------------------------------ pure: title/summary
def test_title_from_h1_else_filename() -> None:
    assert adopt.propose_title("# S18 / MD1 — `marketstate/` tracker\n", "x.md") == \
        "S18 / MD1 — marketstate/ tracker"
    assert adopt.propose_title("no heading here\n", "docs/core-primitives.md") == "core primitives"
    assert adopt.propose_title("```\n# not a title\n```\n# Real title\n", "x.md") == "Real title"
    assert len(adopt.propose_title("# " + "w" * 200, "x.md")) == 80
    assert len(adopt.propose_title("", "a.md")) >= 3


def test_summary_skips_metadata_and_takes_two_sentences() -> None:
    text = ("# Tracker brief\n\n"
            "Status: DRAFT, owner decisions folded in.\n"
            "TIER: T2 (mimo): one new package.\n"
            "Repo: `/x/y`\n\n"
            "Defines the per-second market-state tracker that derives its rows live from the"
            " driver's own events. It keeps zero allocations on the hot path. A third sentence"
            " that must not appear.\n\nSecond paragraph.\n")
    summary = adopt.propose_summary(text, "docs/x.md")
    assert summary == ("Defines the per-second market-state tracker that derives its rows live "
                       "from the driver's own events. It keeps zero allocations on the hot path.")
    assert len(summary) <= 400


def test_summary_is_clipped_to_400_and_cleaned() -> None:
    long = "**Decimal** arithmetic with `tick` rounding and " + "very long clause " * 60 + "end."
    summary = adopt.propose_summary("# T\n\n" + long + "\n", "x.md")
    assert len(summary) <= 400 and summary.endswith("\u2026")
    assert "**" not in summary and "`" not in summary


def test_summary_placeholder_is_visible_to_the_thin_lint() -> None:
    for text in ("# Only a title\n", "# T\n\n| a | b |\n|---|---|\n| 1 | 2 |\n",
                 "# T\n\n- a bullet\n- another\n", "# T\n\nshort line\n"):
        summary = adopt.propose_summary(text, "docs/x.md")
        assert summary == "Imported from docs/x.md"
        assert lint.thin_reason("T", summary) == "is an import placeholder"


# ------------------------------------------------------------------- through the store
def _tree(root: Path) -> Path:
    files = {
        "README.md": "# Repo\nignored\n",
        "AGENTS.md": "# Agents\nignored\n",
        "docs/PRD.md": "# Product requirements\n\nThe driver quotes two sides of every market it"
                       " trades and keeps inventory flat.\n",
        "docs/RUNBOOK.md": "# Live runbook\n\nHow to start, watch and stop a live run safely on"
                           " the mainnet host.\n",
        "docs/FINDINGS-run7.md": "# Run 7 findings\n\nThe tracker emitted one row per second for"
                                 " 31 symbols across the whole run.\n",
        "docs/specs/S1-core.md": "# Core primitives\n\nDecimal arithmetic, tick rounding and the"
                                 " order book structure used everywhere.\n\n## Limits\n\nNone.\n",
        "docs/specs/S2-tracker.md": "# Tracker\n\nThe tracker derives per-second rows live from the"
                                    " driver events with no allocations.\n",
        "docs/specs/S3-empty.md": "   \n",
        "docs/specs/S4-bare.md": "# Bare spec\n",
        ".hidden/skip.md": "# hidden\ntext text text text text text text text text\n",
        "node_modules/x/README.md": "# vendored\n",
        "runs/sim/s9/summary.md": "# simcheck s9\n",
        "notes.txt": "not markdown\n",
    }
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return root


@pytest.fixture()
def shop(home: Path) -> str:
    make_project()
    return "shop"


def _snapshot() -> tuple:
    return tuple(len(store.list_entities("shop", k)) for k in ("spec", "doc", "requirement", "feature"))


def test_plan_lists_everything_and_writes_nothing(shop, capsys, tmp_path) -> None:
    root = _tree(tmp_path / "repo")
    rc, out, err = cli(capsys, "adopt", str(root))
    assert rc == 0, err
    assert _snapshot() == (0, 0, 0, 0)
    assert "+ S            docs/specs/S1-core.md  \"Core primitives\"" in out
    assert "+ DOC prd      docs/PRD.md" in out
    assert "+ DOC runbook  docs/RUNBOOK.md" in out
    assert "+ DOC report   docs/FINDINGS-run7.md" in out
    assert "- skip README.md" in out and "- skip AGENTS.md" in out
    assert "- skip docs/specs/S3-empty.md  empty" in out
    assert ".hidden" not in out and "node_modules" not in out and "notes.txt" not in out
    assert "runs/" not in out                              # run output is not product text
    assert "[summary needs a human]" in out and "S4-bare.md" in out
    assert out.splitlines()[-1].startswith(
        "adopt shop from ") and "(plan): 6 new, 0 unchanged, 0 changed on disk, 3 skipped;" in out.splitlines()[-1]


def test_apply_writes_exactly_the_plan_and_rerun_is_a_noop(shop, capsys, tmp_path) -> None:
    root = _tree(tmp_path / "repo")
    rc, out, err = cli(capsys, "adopt", str(root), "--apply")
    assert rc == 0, err
    assert "adopted shop: created 6, updated 0, skipped 3, failed 0" in out
    assert _snapshot() == (3, 3, 0, 0)               # three specs, three DOCs, no R#/F#
    s1 = store.get_entity("shop", "S1", with_body=True)["row"]
    assert s1["title"] == "Core primitives"
    assert s1["summary"].startswith("Decimal arithmetic, tick rounding")
    assert s1["body"] == (root / "docs/specs/S1-core.md").read_text(encoding="utf-8")
    assert s1["notes"] == "adopted: docs/specs/S1-core.md"
    kinds = {d["title"]: d["doc_kind"] for d in store.list_entities("shop", "doc")}
    assert kinds == {"Product requirements": "prd", "Live runbook": "runbook",
                     "Run 7 findings": "report"}
    # rerun: nothing duplicates, everything is `exists`
    rc, out, err = cli(capsys, "adopt", str(root), "--apply")
    assert rc == 0, err
    assert "created 0" in out and sum(1 for l in out.splitlines() if l.startswith("= ")) == 6
    assert "= DOC prd" in out                         # the PRD it made is still the PRD on rerun
    assert _snapshot() == (3, 3, 0, 0)


def test_changed_file_is_reported_and_only_updated_on_request(shop, capsys, tmp_path) -> None:
    root = _tree(tmp_path / "repo")
    cli(capsys, "adopt", str(root), "--apply")
    (root / "docs/specs/S2-tracker.md").write_text("# Tracker\n\nNew text that says something else"
                                                   " entirely about the tracker rows.\n",
                                                   encoding="utf-8")
    (root / "docs/RUNBOOK.md").write_text("# Live runbook\n\nChanged runbook text that is long"
                                          " enough to be a summary line.\n", encoding="utf-8")
    rc, out, _e = cli(capsys, "adopt", str(root))
    assert "~ S" in out and "~ DOC runbook" in out and "2 changed on disk" in out
    assert "(--update refreshes changed bodies)" in out
    cli(capsys, "adopt", str(root), "--apply")                       # no --update: untouched
    assert "something else" not in store.get_entity("shop", "S2", with_body=True)["row"]["body"]
    rc, out, err = cli(capsys, "adopt", str(root), "--apply", "--update")
    assert rc == 0, err
    assert "updated 2" in out
    assert "something else" in store.get_entity("shop", "S2", with_body=True)["row"]["body"]
    runbook = next(d["ref"] for d in store.list_entities("shop", "doc") if d["title"] == "Live runbook")
    rc, out, _e = cli(capsys, "show", runbook, "--body")
    assert "Changed runbook text" in out
    assert [h["change"] for h in __import__("_helpers").history_rows("shop", "spec", 2)][-1] == "body changed"


def test_update_without_apply_is_refused(shop, capsys, tmp_path) -> None:
    rc, _out, err = cli(capsys, "adopt", str(_tree(tmp_path / "r")), "--update")
    assert rc == 1 and "--update needs --apply" in err


def test_a_prd_already_in_the_project_makes_the_next_one_context(shop, capsys, tmp_path) -> None:
    cli(capsys, "add", "DOC", "--title", "Existing PRD", "--kind", "prd", "--summary", "Our PRD.")
    root = _tree(tmp_path / "repo")
    rc, out, _e = cli(capsys, "adopt", str(root), "--apply")
    assert rc == 0
    assert {d["title"]: d["doc_kind"] for d in store.list_entities("shop", "doc")}[
        "Product requirements"] == "context"


def test_one_refused_row_does_not_stop_the_rest(shop, capsys, tmp_path, monkeypatch) -> None:
    root = _tree(tmp_path / "repo")
    real = store.create_entity

    def flaky(project, kind, fields, con=None):
        if fields.get("title") == "Tracker":
            raise store.StoreError("disk went away")
        return real(project, kind, fields, con=con)

    monkeypatch.setattr(store, "create_entity", flaky)
    rc, out, err = cli(capsys, "adopt", str(root), "--apply")
    assert rc == 1
    assert "created 5, updated 0, skipped 3, failed 1" in out
    assert "! disk went away" in out
    assert _snapshot() == (2, 3, 0, 0)


def test_json_output(shop, capsys, tmp_path) -> None:
    root = _tree(tmp_path / "repo")
    rc, out, _e = cli(capsys, "adopt", str(root), "--json")
    plan = json.loads(out)
    assert rc == 0 and plan["applied"] is False and plan["counts"] is None
    assert [r["action"] for r in plan["rows"]] == ["new"] * 6
    assert all("text" not in r for r in plan["rows"])
    rc, out, _e = cli(capsys, "adopt", str(root), "--apply", "--json")
    done = json.loads(out)
    assert done["counts"] == {"created": 6, "updated": 0, "skipped": 0, "failed": 0}
    assert {r["ref"] for r in done["rows"]} == {"S1", "S2", "S3", "DOC1", "DOC2", "DOC3"}


def test_adopt_then_lint_counts_the_hollow_rows(shop, capsys, tmp_path) -> None:
    cli(capsys, "adopt", str(_tree(tmp_path / "repo")), "--apply")
    rc, out, _e = cli(capsys, "lint", "--json")
    flags = [(f["flag"], f["ref"]) for f in json.loads(out)["findings"]]
    assert ("thin_summary", "S3") in flags                 # the bare spec: "Imported from ..."
    assert not [f for f in flags if f[0] == "thin_summary" and f[1] != "S3"]


def test_missing_directory_and_project(shop, capsys, tmp_path) -> None:
    rc, _out, err = cli(capsys, "adopt", str(tmp_path / "nope"))
    assert rc == 1 and "is not a directory" in err
    rc, _out, err = cli(capsys, "adopt", str(tmp_path), "--project", "ghost")
    assert rc == 1 and "does not exist" in err
