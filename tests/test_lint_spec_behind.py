"""Lint tests (card HP-25): the S2 section 3 check ``spec_behind``.

A spec still ``draft`` or ``approved`` while a linked feature is ``shipped`` or a
linked requirement is ``done`` is behind its own work. The rule is pure, so the
cases here build the facts dicts by hand; the last one drives ``product lint`` on
a throwaway HERMES_HOME to prove the warning reaches the text output and never
changes the exit code.
"""

from __future__ import annotations

import lint
from _helpers import (  # fixtures imported for discovery
    add_feature, add_spec, cli, con, home, link_feature_spec, make_project, raw_update,
)

# ------------------------------------------------------------------ facts builders
def facts(**parts) -> dict:
    """A complete facts dict; tests override only the row lists they care about."""
    base = {
        "requirements": [],
        "criteria": [],
        "features": [],
        "specs": [],
        "docs": [],
        "deps": {"requirement": [], "feature": [], "spec": []},
        "history": [],
        "doc_problems": [],
        "doc_drift": [],
    }
    base.update(parts)
    return base


def req(entity_id: int = 1, status: str = "draft", *, legacy: int = 0, blocked: int = 0,
        features=(), specs=()) -> dict:
    return {"id": entity_id, "status": status, "legacy": legacy, "blocked": blocked,
            "links": {"features": list(features), "specs": list(specs)}}


def feat(entity_id: int = 1, status: str = "planned", *, infra: int = 0,
         blocked: int = 0, requirements=()) -> dict:
    return {"id": entity_id, "status": status, "infra": infra, "blocked": blocked,
            "links": {"requirements": list(requirements)}}


def spec(entity_id: int = 1, status: str = "draft", *, requirements=(), features=()) -> dict:
    return {"id": entity_id, "status": status,
            "links": {"requirements": list(requirements), "features": list(features)}}


def by_flag(findings, flag: str) -> list:
    return [finding for finding in findings if finding.flag == flag]


# ---------------------------------------------------------------- spec_behind (a)
def test_spec_behind_draft_spec_with_a_shipped_feature() -> None:
    found = by_flag(lint.check(facts(specs=[spec(1, "draft", features=[1])],
                                    features=[feat(1, "shipped", requirements=[1])])),
                    "spec_behind")
    assert found == [lint.Finding("spec_behind", "warning", "S1", "S1 draft but F1 shipped")]


# ---------------------------------------------------------------- spec_behind (b)
def test_spec_behind_approved_spec_with_a_done_requirement() -> None:
    found = by_flag(lint.check(facts(specs=[spec(1, "approved", requirements=[1])],
                                    requirements=[req(1, "done", features=[1])])),
                    "spec_behind")
    assert found == [lint.Finding("spec_behind", "warning", "S1", "S1 approved but R1 done")]


# ---------------------------------------------------------------- spec_behind (c)
def test_spec_behind_silent_for_later_spec_statuses() -> None:
    for status in ("implemented", "verified", "superseded"):
        found = lint.check(facts(specs=[spec(1, status, features=[1], requirements=[1])],
                                 features=[feat(1, "shipped", requirements=[1])],
                                 requirements=[req(1, "done", features=[1])]))
        assert by_flag(found, "spec_behind") == [], status


# ---------------------------------------------------------------- spec_behind (d)
def test_spec_behind_silent_while_the_work_is_still_open() -> None:
    for feature_status in ("planned", "in_progress"):
        found = lint.check(facts(
            specs=[spec(1, "draft", features=[1], requirements=[1])],
            features=[feat(1, feature_status, requirements=[1])],
            requirements=[req(1, "in_progress", features=[1], specs=[1])]))
        assert by_flag(found, "spec_behind") == [], feature_status


# ---------------------------------------------------------------- spec_behind (e)
def test_spec_behind_reports_once_and_names_the_feature() -> None:
    found = by_flag(lint.check(facts(
        specs=[spec(1, "draft", features=[1], requirements=[1])],
        features=[feat(1, "shipped", requirements=[1])],
        requirements=[req(1, "done", features=[1], specs=[1])])), "spec_behind")
    assert found == [lint.Finding("spec_behind", "warning", "S1", "S1 draft but F1 shipped")]


# ---------------------------------------------------------------- spec_behind (f)
def test_spec_behind_silent_for_a_spec_with_no_links() -> None:
    assert by_flag(lint.check(facts(specs=[spec(1, "draft")])), "spec_behind") == []
    assert by_flag(lint.check(facts(specs=[spec(1, "approved")])), "spec_behind") == []


# ---------------------------------------------------------------- spec_behind (g)
def test_spec_behind_flags_only_the_behind_spec_and_stays_sorted() -> None:
    found = lint.check(facts(
        specs=[spec(2, "draft", features=[1]), spec(1, "verified", features=[1])],
        features=[feat(1, "shipped", requirements=[1])]))
    assert by_flag(found, "spec_behind") == [
        lint.Finding("spec_behind", "warning", "S2", "S2 draft but F1 shipped")]
    assert found == sorted(found, key=lint._sort_key)
    assert [finding.ref for finding in by_flag(found, "spec_behind")] == ["S2"]


# ---------------------------------------------------------------- spec_behind (h)
def test_spec_behind_reaches_the_lint_output_as_a_warning(home, con, capsys) -> None:
    make_project()
    add_feature()
    add_spec()
    link_feature_spec(con, "shop", 1, 1)
    raw_update(con, "shop", "feature", 1, status="shipped")
    rc, out, err = cli(capsys, "lint")
    assert rc == 0, err
    assert out.startswith("lint (shop): 0 errors, "), out
    assert "warning spec_behind S1  S1 draft but F1 shipped\n" in out
