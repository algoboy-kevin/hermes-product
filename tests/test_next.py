"""Next tests (HP-6): `product next` — the computed ready queue of S2 section 4.

Covers A11 (a blocked feature is not in the ready list and the unblocked must
comes first), the ready order (effective priority, then in_progress, then
layer, then id), the blocked reasons (the blocked flag's reason and `waiting on
F(n) (<status>)`), the `--limit` cut and the `nothing ready` case. Nothing here
reads docs/.
"""
from __future__ import annotations

import pytest

import store
from _helpers import (  # `home`/`con` are fixtures imported for discovery
    add_feature, add_requirement, cli, con, home, link_feature_dependency,
    link_feature_requirement, make_project, raw_update,
)


def feature_with_requirement(feature_title: str, layer: str, req_title: str,
                             priority: str, status: str = "planned",
                             **fields) -> tuple[int, int]:
    """One feature + its own requirement (the priority source)."""
    rid = add_requirement(title=req_title, priority=priority, **fields)
    fid = add_feature(title=feature_title, layer=layer)
    con = store.connect()
    try:
        link_feature_requirement(con, "shop", fid, rid)
        if status != "planned":
            store.update_entity("shop", f"F{fid}", {"status": status}, con=con)
    finally:
        con.close()
    return fid, rid


# ------------------------------------------------------------- the ordering rules
@pytest.fixture()
def ordered(home) -> None:
    make_project()
    feature_with_requirement("Payments", "core", "Pay by card", "must",
                             status="in_progress")                 # F1
    feature_with_requirement("Ledger", "foundation", "Keep a ledger", "must")   # F2
    feature_with_requirement("Stripe integration", "core", "Charge cards", "must")  # F3
    add_feature(title="Auth plumbing", layer="core", infra=True)   # F4, no reqs -> could


def test_next_first_line_counts(ordered, capsys) -> None:
    rc, out_text, err = cli(capsys, "next")
    assert rc == 0, err
    assert out_text.startswith("next (shop): 4 ready, 0 blocked")


def test_next_ready_order(ordered, capsys) -> None:
    """must before should/could, in_progress before planned, layer, id."""
    out_text = ready_lines(capsys)
    assert [line.split()[0] for line in out_text] == ["F1", "F2", "F3", "F4"]


def test_next_limit_cuts_the_ready_list(ordered, capsys) -> None:
    rc, out_text, err = cli(capsys, "next", "--limit", "2")
    assert rc == 0, err
    assert "F1 " in out_text and "F2 " in out_text
    assert "F3 " not in out_text
    assert "  … and 2 more" in out_text


def test_next_shipped_and_cut_never_show(ordered, capsys) -> None:
    con = store.connect()
    try:
        # statuses set raw: the status chain itself is S2's business, not next's
        raw_update(con, "shop", "feature", 3, status="shipped")
        raw_update(con, "shop", "feature", 4, status="cut")
    finally:
        con.close()
    rc, out_text, err = cli(capsys, "next")
    assert rc == 0, err
    assert "F3" not in out_text               # gone from every list
    assert "F4" not in out_text


# ------------------------------------------------------------------ blocked reasons
@pytest.fixture()
def blocked_project(home) -> None:
    make_project()
    feature_with_requirement("Risk rules", "core", "Screen risky cards", "must")  # F1
    feature_with_requirement("Payments", "core", "Pay by card", "must")           # F2
    con = store.connect()
    try:
        store.update_entity("shop", "F1", {"blocked": 1,
                                           "blocked_reason": "vendor API token"},
                            con=con)
        link_feature_dependency(con, "shop", 2, 1)      # F2 needs F1 (mandatory)
    finally:
        con.close()


def test_a11_blocked_feature_absent_from_ready(blocked_project, capsys) -> None:
    rc, out_text, err = cli(capsys, "next")
    assert rc == 0, err
    assert out_text.startswith("next (shop): 0 ready, 2 blocked")
    ready = out_text.split("blocked:")[0]
    assert "F1 " not in ready and "F2 " not in ready    # neither is ready
    blocked = out_text.split("blocked:")[1]
    assert "F1 " in blocked and "vendor API token" in blocked
    assert "F2 " in blocked and "waiting on F1 (planned)" in blocked


def test_next_unblocked_must_before_blocked_one(home, capsys) -> None:
    """A11: the unblocked must is listed first; the blocked one is not ready."""
    make_project()
    feature_with_requirement("Risk rules", "core", "Screen risky cards", "must")  # F1
    feature_with_requirement("Payments", "core", "Pay by card", "must")           # F2
    con = store.connect()
    try:
        store.update_entity("shop", "F1", {"blocked": 1,
                                           "blocked_reason": "vendor API token"},
                            con=con)
    finally:
        con.close()
    rc, out_text, err = cli(capsys, "next")
    assert rc == 0, err
    assert out_text.startswith("next (shop): 1 ready, 1 blocked")
    assert out_text.index("F2 ") < out_text.index("blocked:") < out_text.index("F1 ")
    assert "F1 " not in out_text.split("blocked:")[0]     # not in the ready list


def test_next_ready_section_header_absent_without_blocked(home, capsys) -> None:
    make_project()
    feature_with_requirement("Payments", "core", "Pay by card", "must")
    rc, out_text, err = cli(capsys, "next")
    assert rc == 0, err
    assert "blocked:" not in out_text


def test_next_nothing_ready(home, capsys) -> None:
    make_project()
    fid, _rid = feature_with_requirement("Risk rules", "core", "Screen risky cards",
                                         "must")
    con = store.connect()
    try:
        store.update_entity("shop", f"F{fid}", {"blocked": 1,
                                                "blocked_reason": "on hold"}, con=con)
    finally:
        con.close()
    rc, out_text, err = cli(capsys, "next")
    assert rc == 0, err
    assert "0 ready, 1 blocked" in out_text
    assert "nothing ready" in out_text


# ----------------------------------------------------------------- priority inheritance
def test_next_plumbing_inherits_must_from_dependent(home, capsys) -> None:
    make_project()
    infra = add_feature(title="Auth plumbing", layer="foundation")      # F1, no reqs
    app, _rid = feature_with_requirement("Payments", "core", "Pay by card", "must")
    search, _rid2 = feature_with_requirement("Search", "core", "Find products", "should")
    filler = add_feature(title="Polish", layer="growth", infra=True)   # F4, could
    con = store.connect()
    try:
        link_feature_dependency(con, "shop", app, infra)   # app needs the plumbing
    finally:
        con.close()
    out_text = ready_lines(capsys)
    # F1 inherits `must` from the feature that needs it: it sorts before the
    # feature with its own `should`, and the dependent itself is blocked by its
    # planned dependency
    assert [line.split()[0] for line in out_text] == \
        [f"F{infra}", f"F{search}", f"F{filler}"]
    rc, full, err = cli(capsys, "next")
    assert rc == 0, err
    assert f"F{app} " in full.split("blocked:")[1]
    assert f"waiting on F{infra} (planned)" in full


# ------------------------------------------------------------------------- the runner
def ready_lines(capsys) -> list[str]:
    rc, out_text, err = cli(capsys, "next")
    assert rc == 0, err
    body = out_text.split("\n", 1)[1]          # drop the summary line
    if "blocked:" in body:
        body = body.split("blocked:")[0]
    return [line for line in body.splitlines() if line.strip() and "…" not in line]


# ----------------------------------------------------------------- unplanned (DOC5 2.7)
def test_a_feature_with_nothing_behind_it_is_listed_apart_not_ready(home, capsys) -> None:
    make_project()
    feature_with_requirement("Payments", "core", "Pay by card", "must")         # F1
    rollup = add_feature(title="Everything about money", layer="foundation")    # F2: no R, S, paths
    rc, out, err = cli(capsys, "next")
    assert rc == 0, err
    assert out.splitlines()[0] == "next (shop): 1 ready, 0 blocked, 1 unplanned"
    ready = [line.split()[0] for line in out.splitlines()[1:] if line.startswith("F")]
    assert ready == ["F1"]
    assert out.splitlines()[-1].startswith("unplanned (")
    assert out.splitlines()[-1].endswith(f": F{rollup}")


def test_a_path_a_spec_or_infra_keeps_a_feature_in_the_queue(home, capsys) -> None:
    make_project()
    with_paths = add_feature(title="Has code", layer="core", paths="src/pay")
    infra = add_feature(title="Plain plumbing", layer="core", infra=True)
    spec_only = add_feature(title="Has a spec", layer="core")
    from _helpers import add_spec, link_feature_spec
    con = store.connect()
    try:
        link_feature_spec(con, "shop", spec_only, add_spec())
    finally:
        con.close()
    rc, out, err = cli(capsys, "next")
    assert rc == 0, err
    assert out.splitlines()[0] == "next (shop): 3 ready, 0 blocked"
    assert "unplanned" not in out
    assert {line.split()[0] for line in out.splitlines()[1:]} == {
        f"F{with_paths}", f"F{infra}", f"F{spec_only}"}


def test_unplanned_does_not_release_what_waits_for_it(home, capsys) -> None:
    make_project()
    hollow = add_feature(title="Hollow base", layer="foundation")
    app, _rid = feature_with_requirement("Payments", "core", "Pay by card", "must")
    # a feature another one waits for is plumbing: it stays in the queue ...
    con = store.connect()
    try:
        link_feature_dependency(con, "shop", app, hollow)
    finally:
        con.close()
    rc, out, err = cli(capsys, "next")
    assert rc == 0 and "unplanned" not in out
    assert f"waiting on F{hollow} (planned)" in out


def test_unplanned_respects_limit(home, capsys) -> None:
    make_project()
    ids = [add_feature(title=f"Hollow {n}", layer="core") for n in range(3)]
    rc, out, err = cli(capsys, "next", "--limit", "2")
    assert rc == 0, err
    assert out.splitlines()[0] == "next (shop): 0 ready, 0 blocked, 3 unplanned"
    assert "nothing ready" in out
    assert out.splitlines()[-1].endswith(f"F{ids[0]}, F{ids[1]} \u2026 and 1 more")
