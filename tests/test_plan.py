"""Plan tests (HP-6): the pure derived values of S2 section 4.

plan.py must stay pure (no sqlite, no store import) and deterministic: every
function here is checked for its definition, its tie-breaks and its edges.
Nothing here reads docs/.
"""
from __future__ import annotations

import pytest

import plan


# ------------------------------------------------------------ requirement/feature progress
def test_requirement_progress_counts_passed_and_waived() -> None:
    assert plan.requirement_progress(["passed", "waived", "failed", "not_run"]) == (2, 4)


def test_requirement_progress_of_no_criteria() -> None:
    assert plan.requirement_progress([]) == (0, 0)


def test_requirement_progress_all_done() -> None:
    assert plan.requirement_progress(["passed", "waived"]) == (2, 2)


def test_feature_progress_counts_done_requirements() -> None:
    assert plan.feature_progress(["done", "dropped", "in_progress"]) == (1, 3)


def test_feature_progress_of_no_requirements() -> None:
    assert plan.feature_progress([]) == (0, 0)


# ------------------------------------------------------------------------ phase readiness
ROWS = [
    {"id": 1, "phase": "mvp", "status": "in_progress"},
    {"id": 2, "phase": "mvp", "status": "done"},
    {"id": 3, "phase": "mvp", "status": "approved"},
    {"id": 4, "phase": "mvp", "status": "dropped"},      # excluded
    {"id": 5, "phase": "v1.1", "status": "done"},        # other phase
]


def test_phase_readiness_started_over_total() -> None:
    assert plan.phase_readiness(ROWS, "mvp") == (2, 3)


def test_phase_readiness_drops_dropped_rows() -> None:
    assert plan.phase_readiness(ROWS, "mvp")[1] == 3


def test_phase_readiness_other_phase() -> None:
    assert plan.phase_readiness(ROWS, "v1.1") == (1, 1)


def test_phase_readiness_unknown_phase() -> None:
    assert plan.phase_readiness(ROWS, "growth") == (0, 0)


# -------------------------------------------------------------------------- evidence span
def test_evidence_span_of_nothing() -> None:
    assert plan.evidence_span([]) is None


def test_evidence_span_strongest_and_weakest() -> None:
    assert plan.evidence_span(["unit", "production", "none"]) == ("production", "none")


def test_evidence_span_one_level() -> None:
    assert plan.evidence_span(["integration"]) == ("integration", "integration")


def test_evidence_span_treats_missing_as_none() -> None:
    assert plan.evidence_span([None, "unit"]) == ("unit", "none")


def test_evidence_span_rejects_unknown_level() -> None:
    with pytest.raises(ValueError):
        plan.evidence_span(["strong"])


# --------------------------------------------------------------------- effective priority
def test_effective_priority_own_requirement() -> None:
    assert plan.effective_priority([1], {1: [("must", "approved")]}, {}) == {1: "must"}


def test_effective_priority_ignores_dropped_requirements() -> None:
    assert plan.effective_priority([1], {1: [("must", "dropped"), ("should", "approved")]},
                                   {}) == {1: "should"}


def test_effective_priority_without_requirements_is_could() -> None:
    assert plan.effective_priority([1], {}, {}) == {1: "could"}


def test_effective_priority_plumbing_inherits_from_dependent() -> None:
    # F2 (must) needs F1 (plumbing, no requirements): F1 inherits `must`
    priorities = plan.effective_priority([1, 2], {2: [("must", "approved")]}, {2: [1]})
    assert priorities == {1: "must", 2: "must"}


def test_effective_priority_is_transitive() -> None:
    # F3 (must) -> F2 -> F1: the priority walks two hops down the dependents
    priorities = plan.effective_priority([1, 2, 3], {3: [("must", "approved")]},
                                         {3: [2], 2: [1]})
    assert priorities == {1: "must", 2: "must", 3: "must"}


def test_effective_priority_takes_the_best_of_own_and_inherited() -> None:
    priorities = plan.effective_priority([1, 2], {1: [("could", "approved")],
                                                  2: [("must", "approved")]},
                                         {2: [1]})
    assert priorities[1] == "must"


def test_effective_priority_own_best_wins_without_dependents() -> None:
    assert plan.effective_priority([1], {1: [("should", "done"), ("must", "done")]},
                                   {}) == {1: "must"}


def test_effective_priority_lists_only_the_asked_features() -> None:
    priorities = plan.effective_priority([2], {2: [("should", "approved")]}, {2: [1]})
    assert priorities == {2: "should"}


# --------------------------------------------------------------------- ready and blocked
def feature(fid: int, status: str = "planned", layer: str = "core",
            priority: str = "could", blocked: int = 0,
            blocked_reason: str | None = None) -> dict:
    return {"id": fid, "status": status, "layer": layer, "priority": priority,
            "blocked": blocked, "blocked_reason": blocked_reason}


def test_ready_order_priority_first() -> None:
    ready, blocked = plan.ready_and_blocked(
        [feature(1, priority="could"), feature(2, priority="must"),
         feature(3, priority="should")], {}, {})
    assert [row["id"] for row in ready] == [2, 3, 1]
    assert blocked == []


def test_ready_order_in_progress_before_planned() -> None:
    ready, _blocked = plan.ready_and_blocked(
        [feature(1, status="planned", priority="must"),
         feature(2, status="in_progress", priority="must")], {}, {})
    assert [row["id"] for row in ready] == [2, 1]


def test_ready_order_layer_foundation_first() -> None:
    ready, _blocked = plan.ready_and_blocked(
        [feature(1, layer="growth"), feature(2, layer="foundation"),
         feature(3, layer="core")], {}, {})
    assert [row["id"] for row in ready] == [2, 3, 1]


def test_ready_order_id_as_the_last_tiebreak() -> None:
    ready, _blocked = plan.ready_and_blocked(
        [feature(3), feature(1), feature(2)], {}, {})
    assert [row["id"] for row in ready] == [1, 2, 3]


def test_a11_blocked_feature_absent_from_ready() -> None:
    ready, blocked = plan.ready_and_blocked(
        [feature(1, priority="must", blocked=1, blocked_reason="vendor API token"),
         feature(2, priority="must")], {}, {1: "planned", 2: "planned"})
    assert [row["id"] for row in ready] == [2]
    assert [row["id"] for row, _reason in blocked] == [1]
    assert blocked[0][1] == "vendor API token"


def test_blocked_reason_lists_waiting_dependencies() -> None:
    ready, blocked = plan.ready_and_blocked(
        [feature(1), feature(2, status="in_progress")],
        {1: [(2, 0)]}, {1: "planned", 2: "in_progress"})
    assert [row["id"] for row in ready] == [2]
    assert blocked[0][0]["id"] == 1
    assert blocked[0][1] == "waiting on F2 (in_progress)"


def test_blocked_reason_lists_all_unmet_dependencies() -> None:
    _ready, blocked = plan.ready_and_blocked(
        [feature(1)],
        {1: [(2, 0), (3, 0), (4, 0)]},
        {1: "planned", 2: "planned", 3: "cut", 4: "shipped"})
    assert blocked[0][1] == "waiting on F2 (planned)"


def test_optional_dependency_ignored() -> None:
    ready, blocked = plan.ready_and_blocked(
        [feature(1)], {1: [(2, 1)]}, {1: "planned", 2: "planned"})
    assert [row["id"] for row in ready] == [1]
    assert blocked == []


def test_cut_dependency_ignored() -> None:
    ready, blocked = plan.ready_and_blocked(
        [feature(1)], {1: [(2, 0)]}, {1: "planned", 2: "cut"})
    assert [row["id"] for row in ready] == [1]


def test_shipped_dependency_makes_ready() -> None:
    ready, blocked = plan.ready_and_blocked(
        [feature(1)], {1: [(2, 0)]}, {1: "planned", 2: "shipped"})
    assert [row["id"] for row in ready] == [1]
    assert blocked == []


def test_unknown_dependency_status_blocks() -> None:
    _ready, blocked = plan.ready_and_blocked([feature(1)], {1: [(2, 0)]}, {1: "planned"})
    assert blocked[0][1] == "waiting on F2 (unknown)"


def test_shipped_and_cut_features_are_not_candidates() -> None:
    ready, blocked = plan.ready_and_blocked(
        [feature(1, status="shipped"), feature(2, status="cut"),
         feature(3, status="planned")], {}, {})
    assert [row["id"] for row in ready] == [3]
    assert blocked == []


def test_blocked_flag_beats_a_met_dependency() -> None:
    _ready, blocked = plan.ready_and_blocked(
        [feature(1, blocked=1, blocked_reason="on hold")], {1: [(2, 0)]},
        {1: "planned", 2: "shipped"})
    assert blocked[0][1] == "on hold"
