"""`set --why-not` tests (card HP-13).

`set REF status=X --why-not` prints every unmet prerequisite of the move and
writes nothing: no status change, no history row, no `updated_at` change.
Exit 1 when the move is blocked, 0 when it is possible.
"""

from __future__ import annotations

from pathlib import Path

from _helpers import (
    add_criterion,
    add_feature,
    add_requirement,
    add_spec,
    cli,
    con,
    history_rows,
    home,
    link_feature_dependency,
    link_feature_requirement,
    link_spec_requirement,
    make_project,
    row,
    status_of,
)

PROJECT = "shop"


def _why_not(capsys, *pairs: str) -> tuple[int, str, str]:
    """`set <REF> ... --why-not`; pairs include `status=<target>`."""
    return cli(capsys, "set", *pairs, "--why-not")


def test_why_not_lists_every_blocker_of_an_approval(home: Path, capsys) -> None:
    make_project()
    add_requirement()
    rc, out, err = _why_not(capsys, "R1", "status=approved")
    assert rc == 1
    assert err == ""
    assert out == (
        "R1 draft -> approved is blocked:\n"
        "  - needs a rationale\n"
        "  - needs at least one linked feature (has 0 features)\n"
    )
    assert status_of(PROJECT, "requirement", 1) == "draft"


def test_why_not_counts_a_field_given_in_the_same_command(home: Path, capsys) -> None:
    make_project()
    add_requirement()
    rc, out, err = _why_not(capsys, "R1", "status=approved", "rationale=x")
    assert rc == 1, err
    assert out == (
        "R1 draft -> approved is blocked:\n"
        "  - needs at least one linked feature (has 0 features)\n"
    )
    assert status_of(PROJECT, "requirement", 1) == "draft"


def test_why_not_lists_the_step_to_go_through_and_what_it_needs(home: Path, capsys) -> None:
    make_project()
    add_requirement()
    rc, out, err = _why_not(capsys, "R1", "status=in_progress")
    assert rc == 1, err
    assert out == (
        "R1 draft -> in_progress is blocked:\n"
        "  - go through approved first\n"
        "  - then, to reach approved: needs a rationale\n"
        "  - then, to reach approved: needs at least one linked feature (has 0 features)\n"
        # parent review fix: the final target's own conditions are listed too, so one call shows everything
        "  - then, to reach in_progress: needs at least one spec (has 0 specs)\n"
        "  - then, to reach in_progress: needs at least one criterion (has 0 criteria)\n"
    )
    assert status_of(PROJECT, "requirement", 1) == "draft"


def test_why_not_says_possible_and_leaves_the_entry_alone(home: Path, con, capsys) -> None:
    make_project()
    add_requirement()
    add_feature()
    link_feature_requirement(con, PROJECT, 1, 1)
    rc, out, err = _why_not(capsys, "R1", "status=approved", "rationale=x")
    assert rc == 0, err
    assert out == "R1 draft -> approved is possible\n"
    assert status_of(PROJECT, "requirement", 1) == "draft"


def test_why_not_never_writes(home: Path, con, capsys) -> None:
    make_project()
    add_requirement()
    add_feature()
    link_feature_requirement(con, PROJECT, 1, 1)
    before = row(PROJECT, "requirement", 1)
    history_before = history_rows(PROJECT, "requirement", 1)

    calls = (
        ("R1", "status=approved"),                      # blocked
        ("R1", "status=approved", "rationale=x"),       # possible
        ("R1", "status=dropped"),                       # blocked
        ("R1", "status=done"),                          # blocked
    )
    for pairs in calls:
        _why_not(capsys, *pairs)
        assert row(PROJECT, "requirement", 1) == before
        assert history_rows(PROJECT, "requirement", 1) == history_before


def test_why_not_names_a_mandatory_planned_dependency(home: Path, con, capsys) -> None:
    make_project()
    add_feature(title="Accounts")
    add_feature(title="Stripe integration")
    link_feature_dependency(con, PROJECT, 2, 1)
    rc, out, err = _why_not(capsys, "F2", "status=in_progress")
    assert rc == 1, err
    assert out == "F2 planned -> in_progress is blocked:\n  - depends on F1 (planned)\n"
    assert status_of(PROJECT, "feature", 2) == "planned"

    rc, out, err = _why_not(capsys, "F2", "status=in_progress", "--override-reason=x")
    assert rc == 0, err
    assert out == "F2 planned -> in_progress is possible\n"
    assert status_of(PROJECT, "feature", 2) == "planned"


def test_why_not_applies_the_reason_rule_of_a_terminal_move(home: Path, capsys) -> None:
    make_project()
    add_requirement()
    rc, out, err = _why_not(capsys, "R1", "status=dropped", "--reason=why")
    assert rc == 0, err
    assert out == "R1 draft -> dropped is possible\n"
    assert status_of(PROJECT, "requirement", 1) == "draft"

    rc, out, err = _why_not(capsys, "R1", "status=dropped")
    assert rc == 1, err
    assert out == "R1 draft -> dropped is blocked:\n  - needs status_reason\n"
    assert status_of(PROJECT, "requirement", 1) == "draft"


# guards the same rule at a second kind, so the mechanism is not requirement-only
def test_why_not_covers_a_spec_verification(home: Path, con, capsys) -> None:
    make_project()
    add_spec()
    rc, out, err = _why_not(capsys, "S1", "status=approved")
    assert rc == 1, err
    assert "needs at least one linked requirement" in out
    assert status_of(PROJECT, "spec", 1) == "draft"


def test_why_not_refuses_a_call_without_a_status(home: Path, capsys) -> None:
    make_project()
    add_requirement()
    rc, out, err = _why_not(capsys, "R1", "rationale=x")
    assert rc == 1
    assert out == ""
    assert "--why-not needs status=<state>" in err


def test_a_real_set_succeeds_after_why_not_says_possible(home: Path, con, capsys) -> None:
    make_project()
    add_requirement()
    add_criterion()
    add_spec()
    add_feature()
    link_feature_requirement(con, PROJECT, 1, 1)
    link_spec_requirement(con, PROJECT, 1, 1)
    rc, out, err = _why_not(capsys, "R1", "status=in_progress")
    assert rc == 1, err
    assert out == (
        "R1 draft -> in_progress is blocked:\n"
        "  - go through approved first\n"
        "  - then, to reach approved: needs a rationale\n"
    )
    rc, out, err = cli(capsys, "set", "R1", "status=approved", "rationale=x")
    assert rc == 0, err
    assert out == "R1 draft -> approved\n"
    rc, out, err = _why_not(capsys, "R1", "status=in_progress")
    assert rc == 0, err
    assert out == "R1 approved -> in_progress is possible\n"
    rc, out, err = cli(capsys, "set", "R1", "status=in_progress")
    assert rc == 0, err
    assert status_of(PROJECT, "requirement", 1) == "in_progress"
