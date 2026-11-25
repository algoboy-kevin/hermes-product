"""Phase-filter tests (HP-36): `--phase vX` on `list` and `next` (R24 / F14, A42).

Pinned here: R#/F# rows filtered by exact phase (case-sensitive), the no-kind run
skipping the kinds without a phase (S#, A#, DOC#), the refusal of `--phase` with
an explicit S/A/DOC kind (nothing printed, exit 1, "phase is a field of R and F
only"), an unknown phase listing nothing at exit 0, the AND with `--status` and
`--origin`, `--limit`, `--json` rows carrying `phase`, `next --phase` counting
only the filtered features in the unchanged summary line (a cross-phase
dependency keeps its real status in the reason), and the empty-phase refusal
("phase must not be empty") on list, next and store.list_entities. A42: given
requirements with phases v0.1 and v0.2, `list R --phase v0.1` lists only the
v0.1 ones; an unknown phase lists nothing, exit 0. Nothing here reads docs/.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

import agent_tools
import model
import store
from _helpers import (  # `home`/`con` are fixtures imported for discovery
    add_criterion,
    add_doc,
    add_feature,
    add_requirement,
    add_spec,
    cli,
    con,
    home,
    link_feature_dependency,
    link_feature_requirement,
    make_project,
)

PROJECT = "shop"


def _refs(out: str) -> list[str]:
    """The ref (first column) of each compact line."""
    return [line.split("  ")[0] for line in out.splitlines()]


def _seed_rows() -> None:
    """R#/F# in v0.1 and v0.2 plus one row of every kind without a phase."""
    add_requirement(PROJECT, title="Pay by card",
                    statement="A buyer can pay by card at checkout",
                    priority="must", phase="v0.1")
    add_requirement(PROJECT, title="Refunds",
                    statement="A buyer can get a refund",
                    priority="should", phase="v0.2")
    add_requirement(PROJECT, title="Search",
                    statement="A buyer can search the catalog",
                    priority="could")
    add_feature(PROJECT, title="Stripe integration", layer="core", phase="v0.1")
    add_feature(PROJECT, title="Refund flow", layer="core", phase="v0.2")
    add_spec(PROJECT, title="PaymentIntents", summary="Use PaymentIntents with 3DS")
    add_criterion(PROJECT, requirement=1,
                  text="Given a card, when paid, then the order is placed")
    add_doc(PROJECT, title="Webhook flow", kind="design",
            summary="How webhooks are processed.")


# ------------------------------------------------------------------- list filter
def test_list_filters_r_and_f_by_phase(home: Path, capsys) -> None:
    make_project()
    _seed_rows()
    rc, out, err = cli(capsys, "list", "R", "--phase", "v0.1")
    assert rc == 0 and err == ""
    assert _refs(out) == ["R1"]
    rc, out, err = cli(capsys, "list", "F", "--phase", "v0.1")
    assert rc == 0
    assert _refs(out) == ["F1"]
    rc, out, err = cli(capsys, "list", "R", "--phase", "v0.2")
    assert rc == 0
    assert _refs(out) == ["R2"]
    # exact match, case-sensitive; a row with no phase matches no phase
    rc, out, err = cli(capsys, "list", "R", "--phase", "V0.1")
    assert rc == 0 and out == ""
    rc, out, err = cli(capsys, "list", "R", "--phase", "v0")
    assert rc == 0 and out == ""


def test_no_kind_run_skips_kinds_without_a_phase(home: Path, capsys) -> None:
    make_project()
    _seed_rows()
    rc, out, err = cli(capsys, "list", "--phase", "v0.1")
    assert rc == 0
    assert _refs(out) == ["R1", "F1"]            # no S1, A1 or DOC1 can match


def test_explicit_kind_without_a_phase_is_refused(home: Path, capsys) -> None:
    make_project()
    _seed_rows()
    for kind in ("S", "A", "DOC"):
        rc, out, err = cli(capsys, "list", kind, "--phase", "v0.1")
        assert rc == 1
        assert out == ""                          # refused before any listing
        assert "phase is a field of R and F only" in err


def test_unknown_phase_lists_nothing_and_exits_zero(home: Path, capsys) -> None:
    make_project()
    _seed_rows()
    rc, out, err = cli(capsys, "list", "R", "--phase", "v9.9")
    assert rc == 0 and out == ""
    rc, out, err = cli(capsys, "list", "F", "--phase", "v9.9")
    assert rc == 0 and out == ""
    rc, out, err = cli(capsys, "list", "--phase", "v9.9")
    assert rc == 0 and out == ""


def test_phase_combines_with_status_origin_and_limit(home: Path, capsys) -> None:
    make_project()
    add_requirement(PROJECT, title="Pay by card",
                    statement="A buyer can pay by card at checkout",
                    priority="must", phase="v0.1", origin="owner")
    add_requirement(PROJECT, title="Refunds",
                    statement="A buyer can get a refund",
                    priority="should", phase="v0.1", origin="import")
    # AND with --status
    rc, out, err = cli(capsys, "list", "R", "--phase", "v0.1", "--status", "draft")
    assert rc == 0
    assert _refs(out) == ["R1", "R2"]
    rc, out, err = cli(capsys, "list", "R", "--phase", "v0.1", "--status", "approved")
    assert rc == 0 and out == ""
    # AND with --origin
    rc, out, err = cli(capsys, "list", "R", "--phase", "v0.1", "--origin", "owner",
                       "--status", "draft")
    assert rc == 0
    assert _refs(out) == ["R1"]
    rc, out, err = cli(capsys, "list", "R", "--phase", "v0.1", "--origin", "import",
                       "--status", "approved")
    assert rc == 0 and out == ""
    # and with --limit
    rc, out, err = cli(capsys, "list", "R", "--phase", "v0.1", "--limit", "1")
    assert rc == 0
    assert _refs(out) == ["R1"]


def test_json_rows_carry_the_phase(home: Path, capsys) -> None:
    make_project()
    _seed_rows()
    rc, out, err = cli(capsys, "list", "R", "--phase", "v0.1", "--json")
    assert rc == 0
    payload = json.loads(out)
    assert payload["count"] == 1
    assert [item["ref"] for item in payload["items"]] == ["R1"]
    assert payload["items"][0]["phase"] == "v0.1"
    rc, out, err = cli(capsys, "list", "--phase", "v0.1", "--json")
    payload = json.loads(out)
    assert [item["ref"] for item in payload["items"]] == ["R1", "F1"]


# ----------------------------------------------------------------- next filter
def _seed_next(con) -> None:
    """v0.1: F1 ready, F3 blocked on out-of-phase F4. v0.2: F2, F4 ready, F5 unplanned."""
    add_requirement(PROJECT, title="Pay by card",
                    statement="A buyer can pay by card at checkout",
                    priority="must", phase="v0.1")
    add_requirement(PROJECT, title="Refunds",
                    statement="A buyer can get a refund",
                    priority="should", phase="v0.2")
    f1 = add_feature(PROJECT, title="Stripe integration", layer="core", phase="v0.1")
    f2 = add_feature(PROJECT, title="Refund flow", layer="core", phase="v0.2")
    f3 = add_feature(PROJECT, title="3DS checks", layer="core", phase="v0.1")
    f4 = add_feature(PROJECT, title="Auth plumbing", layer="foundation", phase="v0.2")
    add_feature(PROJECT, title="Rollup", layer="growth", phase="v0.2")
    link_feature_requirement(con, PROJECT, f1, 1)
    link_feature_requirement(con, PROJECT, f2, 2)
    link_feature_requirement(con, PROJECT, f3, 1)
    link_feature_requirement(con, PROJECT, f4, 2)
    link_feature_dependency(con, PROJECT, f3, f4)      # F3 waits on F4 (mandatory)


def test_next_phase_counts_only_the_filtered_features(home: Path, capsys, con) -> None:
    make_project()
    _seed_next(con)
    rc, out, err = cli(capsys, "next", "--phase", "v0.1")
    assert rc == 0
    lines = out.splitlines()
    assert lines[0] == "next (shop): 1 ready, 1 blocked"
    assert lines[1].split("  ")[0] == "F1"
    assert lines[2] == "blocked:"
    assert lines[3].split("  ")[0] == "F3"
    assert lines[3].endswith("waiting on F4 (planned)")   # real status, not unknown
    assert len(lines) == 4                                # no F2/F4/F5 line anywhere

    rc, out, err = cli(capsys, "next", "--phase", "v0.2")
    assert rc == 0
    lines = out.splitlines()
    assert lines[0] == "next (shop): 2 ready, 0 blocked, 1 unplanned"
    assert {line.split("  ")[0] for line in lines[1:3]} == {"F2", "F4"}
    assert lines[-1].startswith("unplanned (") and "F5" in lines[-1]

    # unfiltered sanity: the counts only differ by the filter
    rc, out, err = cli(capsys, "next")
    assert out.splitlines()[0] == "next (shop): 3 ready, 1 blocked, 1 unplanned"


def test_next_unknown_phase_is_empty_and_exits_zero(home: Path, capsys, con) -> None:
    make_project()
    _seed_next(con)
    rc, out, err = cli(capsys, "next", "--phase", "v9.9")
    assert rc == 0 and err == ""
    assert out.splitlines() == ["next (shop): 0 ready, 0 blocked", "nothing ready"]


# ------------------------------------------------------------ empty-phase refusal
def test_empty_phase_is_refused(home: Path, capsys) -> None:
    make_project()
    _seed_rows()
    rc, out, err = cli(capsys, "list", "R", "--phase", "")
    assert rc == 1 and out == ""
    assert "phase must not be empty" in err
    rc, out, err = cli(capsys, "next", "--phase", "")
    assert rc == 1 and out == ""
    assert "phase must not be empty" in err
    with pytest.raises(model.ValidationError, match="phase must not be empty"):
        store.list_entities(PROJECT, "R", phase="")


# ----------------------------------------------------------------- store filter
def test_store_list_entities_phase_filter(home: Path) -> None:
    make_project()
    _seed_rows()
    assert [i["ref"] for i in store.list_entities(PROJECT, "R", phase="v0.1")] == ["R1"]
    assert [i["ref"] for i in store.list_entities(PROJECT, "F", phase="v0.1")] == ["F1"]
    # no kind: the kinds without a phase are skipped, never listed
    assert [i["ref"] for i in store.list_entities(PROJECT, phase="v0.1")] == ["R1", "F1"]
    # an explicit kind without the field is refused, naming the kinds that have one
    for kind in ("S", "A", "DOC"):
        with pytest.raises(model.ValidationError,
                           match="phase is a field of R and F only"):
            store.list_entities(PROJECT, kind, phase="v0.1")


# ----------------------------------------------------------------- agent tools
def test_agent_tools_accept_phase(home: Path, capsys, con) -> None:
    make_project()
    _seed_rows()
    link_feature_requirement(con, PROJECT, 1, 1)       # F1 is real work, not unplanned
    result = json.loads(agent_tools.handle_product_list(
        {"kind": "R", "phase": "v0.1", "project": PROJECT}))
    assert result["ok"]
    assert _refs(result["output"]) == ["R1"]
    refused = json.loads(agent_tools.handle_product_list(
        {"kind": "R", "phase": "", "project": PROJECT}))
    assert not refused["ok"] and "phase must not be empty" in refused["error"]

    result = json.loads(agent_tools.handle_product_next(
        {"phase": "v0.1", "project": PROJECT}))
    assert result["ok"]
    assert result["output"].splitlines()[0] == "next (shop): 1 ready, 0 blocked"
