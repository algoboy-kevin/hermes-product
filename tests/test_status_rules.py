"""Status-machine tests for the `product` plugin (card HP-2, S2 section 1).

One test per exit condition, in both directions where the brief asks for it:
the move is refused naming the missing field or rule, then allowed once the
condition holds. Also: backward moves are allowed and logged, a skipped step is
refused naming the state to go through, terminal states cannot be left, and a
statement change needs a reason once the requirement is approved.
"""

from __future__ import annotations

from pathlib import Path

import store
from _helpers import (
    add_criterion,
    add_external_link,
    add_feature,
    add_requirement,
    add_spec,
    con,
    finish_requirement,
    history_rows,
    home,
    link_feature_requirement,
    link_spec_requirement,
    make_project,
    move,
    pass_criterion,
    set_field,
    status_of,
)

PROJECT = "shop"


# --------------------------------------------------------------------- requirement
def test_approved_needs_a_rationale(home: Path, capsys) -> None:
    make_project()
    add_requirement()
    add_feature()
    rc, out, err = move(capsys, "R1", "approved")
    assert rc == 1
    assert "rationale" in err
    assert out == ""
    assert status_of(PROJECT, "requirement", 1) == "draft"


def test_approved_needs_a_linked_feature(home: Path, con, capsys) -> None:
    make_project()
    add_requirement()
    rc, _out, err = move(capsys, "R1", "approved", "rationale=Revenue")
    assert rc == 1
    assert "feature" in err
    assert status_of(PROJECT, "requirement", 1) == "draft"

    add_feature()
    link_feature_requirement(con, PROJECT, 1, 1)
    rc, out, err = move(capsys, "R1", "approved", "rationale=Revenue")
    assert rc == 0, err
    assert out == "R1 draft -> approved\n"
    assert status_of(PROJECT, "requirement", 1) == "approved"


def test_in_progress_needs_a_linked_spec(home: Path, con, capsys) -> None:
    make_project()
    add_requirement()
    add_feature()
    add_criterion()
    link_feature_requirement(con, PROJECT, 1, 1)
    move(capsys, "R1", "approved", "rationale=Revenue")

    rc, _out, err = move(capsys, "R1", "in_progress")
    assert rc == 1
    assert "spec" in err
    assert status_of(PROJECT, "requirement", 1) == "approved"

    add_spec()
    link_spec_requirement(con, PROJECT, 1, 1)
    rc, out, err = move(capsys, "R1", "in_progress")
    assert rc == 0, err
    assert out == "R1 approved -> in_progress\n"


def test_in_progress_needs_a_criterion(home: Path, con, capsys) -> None:
    make_project()
    add_requirement()
    add_feature()
    add_spec()
    link_feature_requirement(con, PROJECT, 1, 1)
    link_spec_requirement(con, PROJECT, 1, 1)
    move(capsys, "R1", "approved", "rationale=Revenue")

    rc, _out, err = move(capsys, "R1", "in_progress")
    assert rc == 1
    assert "criterion" in err

    add_criterion()
    rc, out, err = move(capsys, "R1", "in_progress")
    assert rc == 0, err
    assert out == "R1 approved -> in_progress\n"


def test_done_needs_every_criterion_passed_or_waived(home: Path, con, capsys) -> None:
    """A8: a requirement with a not_run criterion cannot be done."""
    make_project()
    add_requirement()
    add_feature()
    add_spec()
    add_criterion(text="Given a card, then it succeeds")
    add_criterion(text="Given a decline, then it is shown")
    link_feature_requirement(con, PROJECT, 1, 1)
    link_spec_requirement(con, PROJECT, 1, 1)
    move(capsys, "R1", "approved", "rationale=Revenue")
    move(capsys, "R1", "in_progress")

    rc, _out, err = move(capsys, "R1", "done")
    assert rc == 1
    assert "criterion" in err
    assert "2" in err                       # both are still not_run
    assert status_of(PROJECT, "requirement", 1) == "in_progress"

    pass_criterion(capsys, 1)
    rc, _out, err = move(capsys, "R1", "done")
    assert rc == 1                          # one criterion still open
    pass_criterion(capsys, 2, evidence="waived: acceptance agreed with sales")
    rc, out, err = move(capsys, "R1", "done")
    assert rc == 0, err
    assert out == "R1 in_progress -> done\n"


def test_done_needs_at_least_one_criterion(home: Path, con, capsys) -> None:
    make_project()
    add_requirement()
    add_feature()
    add_spec()
    link_feature_requirement(con, PROJECT, 1, 1)
    link_spec_requirement(con, PROJECT, 1, 1)
    move(capsys, "R1", "approved", "rationale=Revenue")
    rc, _out, err = move(capsys, "R1", "in_progress")
    assert rc == 1                          # in_progress also needs a criterion
    assert "criterion" in err


def test_backward_move_is_allowed_and_logged(home: Path, con, capsys) -> None:
    make_project()
    add_requirement()
    add_feature()
    add_spec()
    finish_requirement(capsys, con, 1)

    rc, out, err = move(capsys, "R1", "in_progress")
    assert rc == 0, err
    assert out == "R1 done -> in_progress\n"
    assert status_of(PROJECT, "requirement", 1) == "in_progress"
    changes = [event["change"] for event in history_rows(PROJECT, "requirement", 1)]
    assert changes[-1] == "status done -> in_progress"


def test_skipped_step_is_refused_naming_the_state_to_go_through(home: Path, capsys) -> None:
    make_project()
    add_requirement()
    rc, _out, err = move(capsys, "R1", "in_progress")
    assert rc == 1
    assert "approved" in err
    assert status_of(PROJECT, "requirement", 1) == "draft"


def test_terminal_state_cannot_be_left(home: Path, capsys) -> None:
    make_project()
    add_requirement()
    rc, out, err = move(capsys, "R1", "dropped", "status_reason=No budget")
    assert rc == 0, err
    assert out == "R1 draft -> dropped\n"

    rc, _out, err = move(capsys, "R1", "approved", "rationale=Revenue")
    assert rc == 1
    assert "terminal" in err or "dropped" in err
    assert status_of(PROJECT, "requirement", 1) == "dropped"


def test_dropped_needs_status_reason(home: Path, capsys) -> None:
    make_project()
    add_requirement()
    rc, _out, err = move(capsys, "R1", "dropped")
    assert rc == 1
    assert "status_reason" in err
    assert status_of(PROJECT, "requirement", 1) == "draft"

    rc, out, err = move(capsys, "R1", "dropped", "status_reason=No budget")
    assert rc == 0, err
    assert out == "R1 draft -> dropped\n"


def test_superseded_by_must_be_another_existing_requirement(home: Path, capsys) -> None:
    make_project()
    add_requirement()
    add_requirement(title="Pay by invoice")
    add_requirement(title="Refund a card")

    rc, _out, err = move(capsys, "R1", "dropped", "status_reason=Replaced",
                         "superseded_by=R1")
    assert rc == 1
    assert "superseded_by" in err

    rc, _out, err = move(capsys, "R1", "dropped", "status_reason=Replaced",
                         "superseded_by=R9")
    assert rc == 1
    assert "superseded_by" in err or "R9" in err

    rc, out, err = move(capsys, "R1", "dropped", "status_reason=Replaced",
                        "superseded_by=R2")
    assert rc == 0, err
    assert out == "R1 draft -> dropped\n"
    assert store.get_entity(PROJECT, "R1")["row"]["superseded_by"] == 2


def test_superseded_by_cross_project_is_refused(home: Path, con, capsys) -> None:
    make_project("shop")
    make_project("other")
    add_requirement("shop")
    add_requirement("other", title="Pay by invoice")
    add_requirement("other", title="Refund a card")     # other has R2, shop does not

    rc, _out, err = move(capsys, "R1", "dropped", "status_reason=Replaced",
                         "superseded_by=R2", project="shop")
    assert rc == 1
    assert "superseded_by" in err or "R2" in err
    assert status_of("shop", "requirement", 1) == "draft"


def test_statement_change_needs_a_reason_once_approved(home: Path, con, capsys) -> None:
    make_project()
    add_requirement()
    add_feature()
    link_feature_requirement(con, PROJECT, 1, 1)

    # before approval: no reason needed, but the change is still logged
    rc, out, err = set_field(capsys, "R1", "statement=A buyer can pay once")
    assert rc == 0, err
    assert out == "R1 updated: statement\n"
    assert [e["change"] for e in history_rows(PROJECT, "requirement", 1)] == ["statement changed"]

    move(capsys, "R1", "approved", "rationale=Revenue")
    rc, _out, err = set_field(capsys, "R1", "statement=A buyer can pay by card")
    assert rc == 1
    assert "reason" in err

    rc, out, err = set_field(capsys, "R1", "statement=A buyer can pay by card",
                            reason="Plainer wording")
    assert rc == 0, err
    assert out == "R1 updated: statement\n"
    last = history_rows(PROJECT, "requirement", 1)[-1]
    assert last["change"] == "statement changed"
    assert last["reason"] == "Plainer wording"


def test_set_updates_updated_at_and_logs_only_changes(home: Path, capsys) -> None:
    make_project()
    add_requirement()
    before = store.get_entity(PROJECT, "R1")["row"]["updated_at"]
    rc, out, err = set_field(capsys, "R1", "phase=mvp", "notes=needs ops sign-off")
    assert rc == 0, err
    assert out == "R1 updated: phase, notes\n"
    after = store.get_entity(PROJECT, "R1")["row"]
    assert after["phase"] == "mvp"
    assert after["notes"] == "needs ops sign-off"
    assert after["updated_at"] >= before
    assert history_rows(PROJECT, "requirement", 1) == []


# ------------------------------------------------------------------------- feature
def test_shipped_needs_every_linked_requirement_done(home: Path, con, capsys) -> None:
    make_project()
    add_requirement()
    add_feature()
    add_spec()
    link_feature_requirement(con, PROJECT, 1, 1)     # R1 is draft
    move(capsys, "F1", "in_progress")

    rc, _out, err = move(capsys, "F1", "shipped")
    assert rc == 1
    assert "requirement" in err
    assert status_of(PROJECT, "feature", 1) == "in_progress"

    finish_requirement(capsys, con, 1)
    rc, out, err = move(capsys, "F1", "shipped")
    assert rc == 0, err
    assert out == "F1 in_progress -> shipped\n"


def test_shipped_needs_a_requirement_unless_infra(home: Path, capsys) -> None:
    make_project()
    add_feature()
    move(capsys, "F1", "in_progress")

    rc, _out, err = move(capsys, "F1", "shipped")
    assert rc == 1
    assert "requirement" in err

    rc, _out, err = set_field(capsys, "F1", "infra=1")
    assert rc == 0, err
    rc, out, err = move(capsys, "F1", "shipped")
    assert rc == 0, err
    assert out == "F1 in_progress -> shipped\n"


def test_cut_needs_status_reason(home: Path, capsys) -> None:
    make_project()
    add_feature()
    rc, _out, err = move(capsys, "F1", "cut")
    assert rc == 1
    assert "status_reason" in err
    assert status_of(PROJECT, "feature", 1) == "planned"

    rc, out, err = move(capsys, "F1", "cut", "status_reason=Deferred to v2")
    assert rc == 0, err
    assert out == "F1 planned -> cut\n"


def test_cut_is_refused_while_an_open_requirement_is_only_on_this_feature(
    home: Path, con, capsys
) -> None:
    make_project()
    add_requirement()
    add_feature()
    add_feature(title="Accounts")
    link_feature_requirement(con, PROJECT, 1, 1)     # R1 (draft) only on F1

    rc, _out, err = move(capsys, "F1", "cut", "status_reason=Deferred")
    assert rc == 1
    assert "requirement" in err
    assert status_of(PROJECT, "feature", 1) == "planned"

    link_feature_requirement(con, PROJECT, 2, 1)     # R1 now also on F2
    rc, out, err = move(capsys, "F1", "cut", "status_reason=Deferred")
    assert rc == 0, err
    assert out == "F1 planned -> cut\n"


def test_feature_backward_move_is_allowed(home: Path, capsys) -> None:
    make_project()
    add_feature(infra=1)
    move(capsys, "F1", "in_progress")
    move(capsys, "F1", "shipped")
    rc, out, err = move(capsys, "F1", "in_progress")
    assert rc == 0, err
    assert out == "F1 shipped -> in_progress\n"
    assert [e["change"] for e in history_rows(PROJECT, "feature", 1)][-1] == \
        "status shipped -> in_progress"


# ---------------------------------------------------------------------------- spec
def test_spec_approved_needs_a_linked_requirement(home: Path, con, capsys) -> None:
    make_project()
    add_requirement()
    add_spec()
    rc, _out, err = move(capsys, "S1", "approved")
    assert rc == 1
    assert "requirement" in err
    assert status_of(PROJECT, "spec", 1) == "draft"

    link_spec_requirement(con, PROJECT, 1, 1)
    rc, out, err = move(capsys, "S1", "approved")
    assert rc == 0, err
    assert out == "S1 draft -> approved\n"


def test_spec_implemented_needs_notes_or_an_external_link(home: Path, con, capsys) -> None:
    make_project()
    add_requirement()
    add_spec()
    link_spec_requirement(con, PROJECT, 1, 1)
    move(capsys, "S1", "approved")

    rc, _out, err = move(capsys, "S1", "implemented")
    assert rc == 1
    assert "notes" in err or "external" in err
    assert status_of(PROJECT, "spec", 1) == "approved"

    rc, out, err = set_field(capsys, "S1", "notes=src/pay.py:pay")
    assert rc == 0, err
    rc, out, err = move(capsys, "S1", "implemented")
    assert rc == 0, err
    assert out == "S1 approved -> implemented\n"


def test_spec_implemented_is_allowed_with_an_external_link_row(
    home: Path, con, capsys
) -> None:
    make_project()
    add_requirement()
    add_spec()
    link_spec_requirement(con, PROJECT, 1, 1)
    add_external_link(con, PROJECT, "spec", 1, system="github", external_id="PR-42")
    move(capsys, "S1", "approved")

    rc, out, err = move(capsys, "S1", "implemented")
    assert rc == 0, err
    assert out == "S1 approved -> implemented\n"


def test_spec_verified_needs_passing_criteria(home: Path, con, capsys) -> None:
    make_project()
    add_requirement()
    add_spec()
    add_criterion()                                   # A1 not_run
    link_spec_requirement(con, PROJECT, 1, 1)
    move(capsys, "S1", "approved")
    move(capsys, "S1", "implemented", "notes=src/pay.py")

    rc, _out, err = move(capsys, "S1", "verified")
    assert rc == 1
    assert "criterion" in err
    assert status_of(PROJECT, "spec", 1) == "implemented"

    pass_criterion(capsys, 1)
    rc, out, err = move(capsys, "S1", "verified")
    assert rc == 0, err
    assert out == "S1 implemented -> verified\n"


def test_spec_verified_from_draft_is_refused_for_the_skipped_step(home: Path, capsys) -> None:
    make_project()
    add_spec()
    rc, _out, err = move(capsys, "S1", "verified")
    assert rc == 1
    assert "approved" in err                      # go through approved first


def test_spec_verified_needs_at_least_one_criterion(home: Path, con, capsys) -> None:
    make_project()
    add_requirement()                              # no criterion on it
    add_spec()
    link_spec_requirement(con, PROJECT, 1, 1)
    move(capsys, "S1", "approved")
    move(capsys, "S1", "implemented", "notes=src/pay.py")

    rc, _out, err = move(capsys, "S1", "verified")
    assert rc == 1
    assert "criterion" in err
    assert status_of(PROJECT, "spec", 1) == "implemented"


def test_superseded_needs_an_existing_other_spec(home: Path, capsys) -> None:
    make_project()
    add_spec()
    add_spec(title="Payments v2")
    rc, _out, err = move(capsys, "S1", "superseded")
    assert rc == 1
    assert "superseded_by" in err

    rc, _out, err = move(capsys, "S1", "superseded", "superseded_by=S1")
    assert rc == 1
    assert "superseded_by" in err

    rc, out, err = move(capsys, "S1", "superseded", "superseded_by=S2")
    assert rc == 0, err
    assert out == "S1 draft -> superseded\n"
    assert store.get_entity(PROJECT, "S1")["row"]["superseded_by"] == 2


# ----------------------------------------------------------------------- criterion
def test_passed_needs_evidence(home: Path, capsys) -> None:
    make_project()
    add_requirement()
    add_criterion()
    rc, _out, err = move(capsys, "A1", "passed")
    assert rc == 1
    assert "evidence" in err
    assert status_of(PROJECT, "criterion", 1) == "not_run"

    rc, out, err = pass_criterion(capsys, 1)
    assert rc == 0, err
    assert out == "A1 not_run -> passed\n"
    stored = store.get_entity(PROJECT, "A1")["row"]
    assert stored["evidence"] == "test_pay_by_card"
    assert stored["evidence_level"] == "none"      # optional, default stays


def test_waived_needs_the_reason_in_evidence(home: Path, capsys) -> None:
    make_project()
    add_requirement()
    add_criterion()
    rc, _out, err = move(capsys, "A1", "waived")
    assert rc == 1
    assert "evidence" in err

    rc, out, err = move(capsys, "A1", "waived", "evidence=waived: out of scope for MVP")
    assert rc == 0, err
    assert out == "A1 not_run -> waived\n"


def test_criterion_moves_are_unrestricted_and_logged(home: Path, capsys) -> None:
    make_project()
    add_requirement()
    add_criterion()
    rc, out, err = move(capsys, "A1", "failed")
    assert rc == 0, err
    assert out == "A1 not_run -> failed\n"
    rc, out, err = move(capsys, "A1", "not_run")
    assert rc == 0, err
    assert out == "A1 failed -> not_run\n"
    assert [e["change"] for e in history_rows(PROJECT, "criterion", 1)] == [
        "status not_run -> failed", "status failed -> not_run",
    ]


def test_unknown_state_is_refused(home: Path, capsys) -> None:
    make_project()
    add_requirement()
    rc, _out, err = move(capsys, "R1", "shipped")
    assert rc == 1
    assert "shipped" in err
    assert status_of(PROJECT, "requirement", 1) == "draft"
