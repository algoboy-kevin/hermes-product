"""Lint tests (card HP-7): the S2 section 3 checks, the text/JSON output, A12 and A13.

``lint.check(facts)`` is pure, so every rule is exercised here on hand-built facts
dicts; the store-reading side (``load_facts``/``run``) is covered end to end
through ``product lint`` on the tmp HERMES_HOME fixtures. Nothing here reads docs/.
"""

from __future__ import annotations

import json

import lint
from _helpers import (  # `home`/`con` are fixtures imported for discovery
    add_doc, add_feature, add_requirement, cli, con, home,
    make_project, raw_update,
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


def crit(entity_id: int = 1, requirement_id: int = 1, status: str = "not_run") -> dict:
    return {"id": entity_id, "requirement_id": requirement_id, "status": status}


def feat(entity_id: int = 1, status: str = "planned", *, infra: int = 0,
         blocked: int = 0, requirements=()) -> dict:
    return {"id": entity_id, "status": status, "infra": infra, "blocked": blocked,
            "links": {"requirements": list(requirements)}}


def spec(entity_id: int = 1, status: str = "draft", *, requirements=()) -> dict:
    return {"id": entity_id, "status": status,
            "links": {"requirements": list(requirements)}}


def doc(entity_id: int = 1, status: str = "current", *, links=()) -> dict:
    return {"id": entity_id, "status": status, "doc_links": list(links),
            "body_ref": None, "body_hash": None}


def by_flag(findings, flag: str) -> list:
    return [finding for finding in findings if finding.flag == flag]


def only(flag: str, **parts):
    """The single finding with ``flag`` (asserts there is exactly one)."""
    found = by_flag(lint.check(facts(**parts)), flag)
    assert len(found) == 1, found
    return found[0]


# ------------------------------------------------------------ orphan_requirement
def test_orphan_requirement_warns_for_approved_without_feature() -> None:
    finding = only("orphan_requirement", requirements=[req(1, "approved")])
    assert finding == lint.Finding("orphan_requirement", "warning", "R1",
                                   "approved with no linked feature")


def test_orphan_requirement_covers_in_progress_and_done() -> None:
    assert only("orphan_requirement", requirements=[req(1, "in_progress")]).ref == "R1"
    assert only("orphan_requirement",
                requirements=[req(1, "done", features=())]).ref == "R1"


def test_orphan_requirement_silent_with_a_feature() -> None:
    assert by_flag(lint.check(facts(requirements=[req(1, "approved", features=[1])])),
                   "orphan_requirement") == []


def test_orphan_requirement_skips_draft_and_dropped() -> None:
    found = lint.check(facts(requirements=[req(1, "draft"), req(2, "dropped")]))
    assert by_flag(found, "orphan_requirement") == []


# --------------------------------------------------------------- unspecified
def test_unspecified_warns_for_in_progress_without_spec() -> None:
    finding = only("unspecified", requirements=[req(1, "in_progress")])
    assert finding == lint.Finding("unspecified", "warning", "R1",
                                   "in_progress with no linked spec")


def test_unspecified_silent_with_a_spec() -> None:
    assert by_flag(lint.check(facts(requirements=[req(1, "in_progress", specs=[1])])),
                   "unspecified") == []


def test_unspecified_skips_approved() -> None:
    assert by_flag(lint.check(facts(requirements=[req(1, "approved")])), "unspecified") == []


# ---------------------------------------------------------------- no_criteria
def test_no_criteria_is_an_error_for_in_progress_without_criteria() -> None:
    finding = only("no_criteria", requirements=[req(1, "in_progress")])
    assert finding == lint.Finding("no_criteria", "error", "R1",
                                   "in_progress with no criterion")


def test_no_criteria_is_an_error_for_done_without_criteria() -> None:
    assert only("no_criteria", requirements=[req(1, "done")]).severity == "error"


def test_no_criteria_silent_with_a_criterion() -> None:
    assert by_flag(lint.check(facts(requirements=[req(1, "in_progress")],
                                    criteria=[crit(1, 1)])), "no_criteria") == []


def test_no_criteria_downgrades_to_warning_for_legacy() -> None:
    finding = only("no_criteria", requirements=[req(1, "in_progress", legacy=1)])
    assert finding.severity == "warning"


# ------------------------------------------------------------ unverified_done
def test_unverified_done_is_an_error_when_a_criterion_is_not_passed() -> None:
    finding = only("unverified_done",
                   requirements=[req(1, "done", features=[1])],
                   features=[feat(1, "shipped", requirements=[1])],
                   criteria=[crit(1, 1, "passed"), crit(2, 1, "failed")])
    assert finding == lint.Finding("unverified_done", "error", "R1",
                                   "done but A2 is failed")


def test_unverified_done_silent_when_every_criterion_passed_or_waived() -> None:
    found = lint.check(facts(requirements=[req(1, "done", features=[1])],
                             features=[feat(1, "shipped", requirements=[1])],
                             criteria=[crit(1, 1, "passed"), crit(2, 1, "waived")]))
    assert by_flag(found, "unverified_done") == []


# ---------------------------------------------------------------- orphan_spec
def test_orphan_spec_is_an_error_for_approved_without_requirement() -> None:
    finding = only("orphan_spec", specs=[spec(1, "approved")])
    assert finding == lint.Finding("orphan_spec", "error", "S1",
                                   "approved with no linked requirement")


def test_orphan_spec_silent_with_a_requirement() -> None:
    assert by_flag(lint.check(facts(specs=[spec(1, "approved", requirements=[1])])),
                   "orphan_spec") == []


def test_orphan_spec_skips_draft() -> None:
    assert by_flag(lint.check(facts(specs=[spec(1, "draft")])), "orphan_spec") == []


# ----------------------------------------------------------------- stale_spec
def _stale_history(*rows) -> list[dict]:
    return [{"id": row_id, "entity_type": kind, "entity_id": entity_id, "change": change,
             "at": "2026-01-01T00:00:00+00:00"}
            for row_id, kind, entity_id, change in rows]


def test_stale_spec_warns_when_the_statement_changed_after_approval() -> None:
    finding = only("stale_spec",
                   requirements=[req(1, "approved", features=[1])],
                   features=[feat(1, "in_progress", requirements=[1])],
                   specs=[spec(1, "approved", requirements=[1])],
                   history=_stale_history((5, "spec", 1, "status draft -> approved"),
                                          (7, "requirement", 1, "statement changed")))
    assert finding == lint.Finding("stale_spec", "warning", "S1",
                                   "R1 statement changed after approval")


def test_stale_spec_silent_when_the_change_came_before_approval() -> None:
    found = lint.check(facts(
        requirements=[req(1, "approved", features=[1])],
        features=[feat(1, "in_progress", requirements=[1])],
        specs=[spec(1, "approved", requirements=[1])],
        history=_stale_history((3, "requirement", 1, "statement changed"),
                               (5, "spec", 1, "status draft -> approved"))))
    assert by_flag(found, "stale_spec") == []


def test_stale_spec_silent_without_an_approval_row() -> None:
    found = lint.check(facts(
        requirements=[req(1, "approved", features=[1])],
        features=[feat(1, "in_progress", requirements=[1])],
        specs=[spec(1, "approved", requirements=[1])],
        history=_stale_history((7, "requirement", 1, "statement changed"))))
    assert by_flag(found, "stale_spec") == []


# ----------------------------------------------- feature_without_requirement
def test_feature_without_requirement_warns() -> None:
    finding = only("feature_without_requirement", features=[feat(1, "planned")])
    assert finding == lint.Finding("feature_without_requirement", "warning", "F1",
                                   "planned with no requirement")


def test_feature_without_requirement_silent_for_infra() -> None:
    assert by_flag(lint.check(facts(features=[feat(1, "planned", infra=1)])),
                   "feature_without_requirement") == []


def test_feature_without_requirement_silent_with_a_requirement() -> None:
    assert by_flag(lint.check(facts(features=[feat(1, "shipped", requirements=[1])])),
                   "feature_without_requirement") == []


# --------------------------------------------------------------------- at_risk
def test_at_risk_feature_whose_mandatory_dependency_is_cut() -> None:
    finding = only("at_risk",
                   requirements=[req(1, "approved", features=[1])],
                   features=[feat(1, "planned", requirements=[1]), feat(2, "cut")],
                   deps={"requirement": [], "feature": [(1, 2, 0)], "spec": []})
    assert finding == lint.Finding("at_risk", "warning", "F1", "F2 is cut")


def test_at_risk_ignores_an_optional_dependency() -> None:
    found = lint.check(facts(
        requirements=[req(1, "approved", features=[1])],
        features=[feat(1, "planned", requirements=[1]), feat(2, "cut")],
        deps={"requirement": [], "feature": [(1, 2, 1)], "spec": []}))
    assert by_flag(found, "at_risk") == []


def test_at_risk_requirement_whose_dependency_was_dropped() -> None:
    finding = only("at_risk",
                   requirements=[req(1, "approved", features=[1]), req(2, "dropped")],
                   features=[feat(1, "in_progress", requirements=[1])],
                   deps={"requirement": [(1, 2, 0)], "feature": [], "spec": []})
    assert finding == lint.Finding("at_risk", "warning", "R1", "R2 is dropped")


def test_at_risk_names_a_blocked_dependency() -> None:
    finding = only("at_risk",
                   requirements=[req(1, "approved", features=[1])],
                   features=[feat(1, "planned", requirements=[1]),
                             feat(2, "in_progress", blocked=1)],
                   deps={"requirement": [], "feature": [(1, 2, 0)], "spec": []})
    assert finding.message == "F2 is blocked"


def test_at_risk_silent_when_the_dependency_is_healthy() -> None:
    found = lint.check(facts(
        requirements=[req(1, "approved", features=[1])],
        features=[feat(1, "planned", requirements=[1]), feat(2, "in_progress")],
        deps={"requirement": [], "feature": [(1, 2, 0)], "spec": []}))
    assert by_flag(found, "at_risk") == []


# ----------------------------------------------------------- dependency_cycle
def test_dependency_cycle_in_features_is_an_error() -> None:
    finding = only("dependency_cycle",
                   features=[feat(1, "planned", infra=1), feat(2, "planned", infra=1)],
                   deps={"requirement": [], "feature": [(1, 2, 0), (2, 1, 0)], "spec": []})
    assert finding == lint.Finding("dependency_cycle", "error", "F1", "F1 -> F2 -> F1")


def test_dependency_cycle_in_requirements_is_an_error() -> None:
    finding = only("dependency_cycle",
                   requirements=[req(1, "draft"), req(2, "draft")],
                   deps={"requirement": [(1, 2, 0), (2, 1, 0)], "feature": [], "spec": []})
    assert finding == lint.Finding("dependency_cycle", "error", "R1", "R1 -> R2 -> R1")


def test_dependency_cycle_silent_without_a_loop() -> None:
    found = lint.check(facts(
        features=[feat(1, "planned", infra=1), feat(2, "planned", infra=1)],
        deps={"requirement": [], "feature": [(1, 2, 0)], "spec": []}))
    assert by_flag(found, "dependency_cycle") == []


# ------------------------------------------- cut_with_open_requirements
def test_cut_with_open_requirements_is_an_error() -> None:
    finding = only("cut_with_open_requirements",
                   requirements=[req(1, "in_progress", features=[1])],
                   features=[feat(1, "cut", requirements=[1])])
    assert finding == lint.Finding("cut_with_open_requirements", "error", "F1",
                                   "cut with open requirements R1")


def test_cut_with_open_requirements_silent_when_reassigned() -> None:
    found = lint.check(facts(
        requirements=[req(1, "in_progress", features=[1, 2])],
        features=[feat(1, "cut", requirements=[1]),
                  feat(2, "in_progress", requirements=[1])]))
    assert by_flag(found, "cut_with_open_requirements") == []


def test_cut_with_open_requirements_silent_for_a_dropped_requirement() -> None:
    found = lint.check(facts(
        requirements=[req(1, "dropped", features=[1])],
        features=[feat(1, "cut", requirements=[1])]))
    assert by_flag(found, "cut_with_open_requirements") == []


# ------------------------------------------------------------------ orphan_doc
def test_orphan_doc_warns_for_current_without_links() -> None:
    finding = only("orphan_doc", docs=[doc(1)])
    assert finding == lint.Finding("orphan_doc", "warning", "DOC1", "current with no link")


def test_orphan_doc_silent_with_a_link() -> None:
    assert by_flag(lint.check(facts(docs=[doc(1, links=[("requirement", 1)])])),
                   "orphan_doc") == []


def test_orphan_doc_skips_a_superseded_document() -> None:
    assert by_flag(lint.check(facts(docs=[doc(1, "superseded")])), "orphan_doc") == []


# ------------------------------------------------------------------ doc_drift
def test_doc_drift_warns_when_the_hash_differs() -> None:
    finding = only("doc_drift", docs=[doc(1, links=[("requirement", 1)])], doc_drift=[1])
    assert finding == lint.Finding("doc_drift", "warning", "DOC1",
                                   "body hash differs from the stored hash")


def test_doc_drift_silent_when_the_hash_matches() -> None:
    found = lint.check(facts(docs=[doc(1, links=[("requirement", 1)])], doc_drift=[]))
    assert by_flag(found, "doc_drift") == []


# --------------------------------------------------------------------- sorting
def test_findings_sort_by_kind_then_id_then_flag() -> None:
    found = lint.check(facts(
        requirements=[req(2, "approved"),
                      req(1, "in_progress")],
        features=[feat(2, "planned"), feat(1, "planned", infra=1)],
        specs=[spec(1, "approved")],
        docs=[doc(2, links=[("requirement", 1)]), doc(1, links=[("requirement", 1)])],
        doc_drift=[2],
        deps={"requirement": [], "feature": [], "spec": []}))
    assert [(finding.ref, finding.flag) for finding in found] == [
        ("R1", "no_criteria"),
        ("R1", "orphan_requirement"),
        ("R1", "unspecified"),
        ("R2", "orphan_requirement"),
        ("F2", "feature_without_requirement"),
        ("S1", "orphan_spec"),
        ("DOC2", "doc_drift"),
    ]


# ------------------------------------------------------------------ end to end
def test_a12_no_criteria_is_an_error_and_exit_is_non_zero(home, con, capsys) -> None:
    make_project()
    add_requirement()
    raw_update(con, "shop", "requirement", 1, status="in_progress")
    rc, out, err = cli(capsys, "lint")
    assert rc == 1, err
    assert out == (
        "lint (shop): 1 errors, 2 warnings\n"
        "error no_criteria R1  in_progress with no criterion\n"
        "warning orphan_requirement R1  in_progress with no linked feature\n"
        "warning unspecified R1  in_progress with no linked spec\n"
        "info no_card_links project  no kanban cards linked; put a \"product: F# R#\" line in each card body and run: product sync --board SLUG --apply\n"
        "not yet checked: uncovered_path\n"
    )


def test_a13_orphan_requirement_is_a_warning_and_exit_is_zero(home, con, capsys) -> None:
    make_project()
    add_requirement()
    raw_update(con, "shop", "requirement", 1, status="approved")
    rc, out, err = cli(capsys, "lint")
    assert rc == 0, err
    assert out == (
        "lint (shop): 0 errors, 1 warnings\n"
        "warning orphan_requirement R1  approved with no linked feature\n"
        "info no_card_links project  no kanban cards linked; put a \"product: F# R#\" line in each card body and run: product sync --board SLUG --apply\n"
        "not yet checked: uncovered_path\n"
    )


def test_empty_project_prints_zero_counts_and_the_not_yet_line(home, capsys) -> None:
    make_project()
    rc, out, err = cli(capsys, "lint")
    assert rc == 0, err
    assert out == (
        "lint (shop): 0 errors, 0 warnings\n"
        "not yet checked: uncovered_path\n"
    )


def test_json_output_is_one_sorted_object(home, con, capsys) -> None:
    make_project()
    add_requirement()
    raw_update(con, "shop", "requirement", 1, status="approved")
    rc, out, err = cli(capsys, "lint", "--json")
    assert rc == 0, err
    payload = json.loads(out)
    assert payload == {
        "project": "shop",
        "errors": 0,
        "warnings": 1,
        "findings": [{"flag": "orphan_requirement", "severity": "warning",
                      "ref": "R1", "message": "approved with no linked feature"},
                     {"flag": "no_card_links", "severity": "info", "ref": "project",
                      "message": ('no kanban cards linked; put a "product: F# R#" line in each card body and run: product sync --board SLUG --apply')}],
        "not_yet": ["uncovered_path"],
    }
    assert out == json.dumps(payload, sort_keys=True) + "\n"


def test_unknown_project_exits_one_with_a_stderr_message(home, capsys) -> None:
    make_project()
    rc, out, err = cli(capsys, "lint", "--project", "nope")
    assert rc == 1
    assert out == ""
    assert "does not exist" in err


def test_doc_drift_end_to_end_when_the_local_file_changed(home, tmp_path, capsys) -> None:
    make_project()
    note = tmp_path / "note.md"
    note.write_text("# Note\n", encoding="utf-8")
    add_doc(kind="prd", body_ref=str(note))
    note.write_text("# Note\n\nchanged after the hash was stored\n", encoding="utf-8")
    rc, out, err = cli(capsys, "lint")
    assert rc == 0, err
    assert "warning doc_drift DOC1  body hash differs from the stored hash" in out


def test_doc_drift_silent_when_the_local_file_is_gone(home, tmp_path, capsys) -> None:
    make_project()
    note = tmp_path / "note.md"
    note.write_text("# Note\n", encoding="utf-8")
    add_doc(kind="prd", body_ref=str(note))
    note.unlink()
    rc, out, err = cli(capsys, "lint")
    assert rc == 0, err
    assert "doc_drift" not in out


def test_doc_drift_silent_for_a_url_body_ref(home, capsys) -> None:
    make_project()
    add_doc(kind="prd", body_ref="https://example.com/prd.md")
    rc, out, err = cli(capsys, "lint")
    assert rc == 0, err
    assert "doc_drift" not in out


def test_lint_reads_the_store_through_run(home, con, capsys) -> None:
    make_project()
    add_feature()
    findings = lint.run("shop")
    assert [(finding.flag, finding.ref) for finding in findings] == [
        ("feature_without_requirement", "F1"),
        ("no_card_links", "project"),
    ]
