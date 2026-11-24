"""Lint tests (card HP-26): the S2 check ``no_card_links`` and the ``kanban_links`` fact.

Product board = context, kanban = work. When a project has asked for work
(``approved`` or later R#, ``planned`` or later F#) and no kanban card is linked,
nothing joins the two: ``trace`` never names a card and nobody is reminded to run
``sync --board``. One project-level Info says so. Info never changes the exit code.

The rule is pure, so the last cases build the facts dict by hand; the others drive
the store through a throwaway HERMES_HOME.
"""

from __future__ import annotations

import lint
from _helpers import (  # `home`/`con` are fixtures imported for discovery
    add_external_link, add_feature, add_requirement, cli, con, home,
    link_feature_requirement, make_project, raw_update,
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


def req(entity_id: int = 1, status: str = "draft", *, features=()) -> dict:
    return {"id": entity_id, "status": status, "legacy": 0, "blocked": 0,
            "links": {"features": list(features), "specs": []}}


def feat(entity_id: int = 1, status: str = "planned", *, requirements=()) -> dict:
    return {"id": entity_id, "status": status, "infra": 0, "blocked": 0,
            "links": {"requirements": list(requirements)}}


def by_flag(findings, flag: str) -> list:
    return [finding for finding in findings if finding.flag == flag]


MESSAGE = ('no kanban cards linked; put a "product: F# R#" line in each card body'
           " and run: product sync --board SLUG --apply")


# --------------------------------------------------------------- no_card_links (a)
def test_no_card_links_fires_once_for_an_approved_requirement_and_no_links(home, con) -> None:
    make_project()
    add_requirement()
    raw_update(con, "shop", "requirement", 1, status="approved")
    assert lint.load_facts(con, "shop")["kanban_links"] == 0
    found = by_flag(lint.run("shop"), "no_card_links")
    assert found == [lint.Finding("no_card_links", "info", "project", MESSAGE)]


# --------------------------------------------------------------- no_card_links (b)
def test_no_card_links_silent_once_a_kanban_link_exists(home, con) -> None:
    make_project()
    add_requirement()
    raw_update(con, "shop", "requirement", 1, status="approved")
    add_external_link(con, "shop", "requirement", 1, system="kanban",
                      external_id="SHOP-3", url="https://kanban.example/SHOP-3",
                      role="tracks")
    assert lint.load_facts(con, "shop")["kanban_links"] == 1
    assert by_flag(lint.run("shop"), "no_card_links") == []


# --------------------------------------------------------------- no_card_links (c)
def test_no_card_links_silent_for_draft_only_entries(home, con) -> None:
    make_project()
    add_requirement()                                  # draft
    add_feature()
    raw_update(con, "shop", "feature", 1, status="cut")  # retired, never asked for work
    assert by_flag(lint.run("shop"), "no_card_links") == []


def test_no_card_links_fires_for_a_planned_feature_beside_a_draft_requirement(home, con) -> None:
    """A ``planned`` F# is work already: the requirement's state alone does not decide."""
    make_project()
    add_feature()
    add_requirement()
    link_feature_requirement(con, "shop", 1, 1)
    assert by_flag(lint.run("shop"), "no_card_links") == [
        lint.Finding("no_card_links", "info", "project", MESSAGE)]


# --------------------------------------------------------------- no_card_links (d)
def test_no_card_links_still_fires_for_a_github_link_only(home, con) -> None:
    make_project()
    add_requirement()
    raw_update(con, "shop", "requirement", 1, status="approved")
    add_external_link(con, "shop", "requirement", 1, system="github", external_id="42",
                      url="https://github.com/example/repo/issues/42", role="tracks")
    assert lint.load_facts(con, "shop")["kanban_links"] == 0
    found = by_flag(lint.run("shop"), "no_card_links")
    assert found == [lint.Finding("no_card_links", "info", "project", MESSAGE)]


# --------------------------------------------------------------- no_card_links (e)
def test_no_card_links_is_info_and_the_exit_code_stays_zero(home, con, capsys) -> None:
    make_project()
    add_feature()
    add_requirement()
    link_feature_requirement(con, "shop", 1, 1)
    rc, out, err = cli(capsys, "lint")
    assert rc == 0, err
    assert out.splitlines()[0] == "lint (shop): 0 errors, 0 warnings"
    assert f"info no_card_links project  {MESSAGE}\n" in out


# --------------------------------------------------------------- no_card_links (f)
def test_no_card_links_reads_the_kanban_links_fact(home) -> None:
    hand_built = facts(requirements=[req(1, "approved")], kanban_links=0)
    assert by_flag(lint.check(hand_built), "no_card_links") == [
        lint.Finding("no_card_links", "info", "project", MESSAGE)]
    assert by_flag(lint.check(facts(requirements=[req(1, "approved")], kanban_links=1)),
                   "no_card_links") == []


def test_no_card_links_absent_fact_key_means_unknown(home) -> None:
    """A facts dict without ``kanban_links`` (an older caller) yields no finding."""
    assert by_flag(lint.check(facts(requirements=[req(1, "approved")])),
                   "no_card_links") == []
