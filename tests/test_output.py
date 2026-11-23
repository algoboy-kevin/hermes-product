"""Compact-output tests for the `product` plugin (card HP-2, A9 and S4 section 1).

Pinned here: the exact one-line format per entity kind, two spaces between
columns, newlines collapsed, the 80-character cut on criterion text, `list`
order/filters/limits and the `show` layout with its history block.
"""

from __future__ import annotations

from pathlib import Path

import store
from _helpers import (
    add_criterion,
    add_feature,
    add_requirement,
    add_spec,
    con,
    home,
    link_feature_requirement,
    link_feature_spec,
    link_spec_requirement,
    make_project,
    move,
    run,
    set_field,
)

PROJECT = "shop"


def _out(capsys) -> str:
    return capsys.readouterr().out


# ------------------------------------------------------------------ A9: 20 lines
def test_list_r_prints_exactly_one_line_per_requirement(home: Path, capsys) -> None:
    make_project()
    for index in range(20):
        add_requirement(title=f"Requirement number {index}")

    assert run(["list", "R"]) == 0
    lines = _out(capsys).splitlines()
    assert len(lines) == 20
    assert lines[0].startswith("R1  draft  must  -  Requirement number 0  ")
    assert lines[-1].startswith("R20  ")
    for line in lines:
        assert "\n" not in line
        assert len(line) <= 200


# ------------------------------------------------------------- exact line formats
def test_requirement_line_format(home: Path, capsys) -> None:
    make_project()
    add_requirement(title="Pay by card", priority="must", phase="mvp")
    add_feature(title="Stripe integration")
    add_spec(title="Payments")
    add_criterion(requirement=1, text="Given a card, then it succeeds")
    add_criterion(requirement=1, text="Given a decline, then it is shown")
    connection = store.connect()
    try:
        link_feature_requirement(connection, PROJECT, 1, 1)
        link_spec_requirement(connection, PROJECT, 1, 1)
    finally:
        connection.close()

    assert run(["list", "R"]) == 0
    assert _out(capsys) == (
        "R1  draft  must  mvp  Pay by card  F:1  S:1  A:0/2\n"
    )


def test_criterion_line_format_and_the_80_character_cut(home: Path, capsys) -> None:
    make_project()
    add_requirement()
    long_text = "x" * 100
    add_criterion(requirement=1, text="Given a card, then it succeeds")
    add_criterion(requirement=1, text=long_text)

    assert run(["list", "A"]) == 0
    lines = _out(capsys).splitlines()
    assert lines[0] == "A1  not_run  R1  -  Given a card, then it succeeds"
    prefix, text = lines[1].rsplit("  ", 1)
    assert prefix == "A2  not_run  R1  -"
    assert text == "x" * 79 + "\u2026"
    assert len(text) == 80


def test_feature_and_spec_line_formats_with_links(home: Path, capsys) -> None:
    make_project()
    add_requirement()
    add_spec()
    add_feature(title="Stripe integration", layer="foundation", phase="mvp")
    add_feature(title="Accounts", layer="core")
    connection = store.connect()
    try:
        link_feature_requirement(connection, PROJECT, 1, 1)
        link_feature_spec(connection, PROJECT, 1, 1)
        link_spec_requirement(connection, PROJECT, 1, 1)
    finally:
        connection.close()

    assert run(["list", "F"]) == 0
    assert _out(capsys) == (
        "F1  planned  foundation  mvp  Stripe integration  needs F:-  R:1  S:1\n"
        "F2  planned  core  -  Accounts  needs F:-  R:-  S:-\n"
    )
    assert run(["list", "S"]) == 0
    assert _out(capsys) == "S1  draft  Payments  R:1  F:1\n"


def test_absent_optional_columns_are_dashes(home: Path, capsys) -> None:
    make_project()
    add_requirement(phase=None)
    assert run(["list", "R"]) == 0
    assert _out(capsys) == "R1  draft  must  -  Pay by card  F:-  S:-  A:0/0\n"


def test_newlines_in_text_never_break_a_line(home: Path, capsys) -> None:
    make_project()
    add_requirement(title="Pay\nby card", statement="A buyer can pay\nso that orders close")
    assert run(["list", "R"]) == 0
    lines = _out(capsys).splitlines()
    assert len(lines) == 1
    assert lines[0] == "R1  draft  must  -  Pay by card  F:-  S:-  A:0/0"

    assert run(["show", "R1"]) == 0
    shown = _out(capsys).splitlines()
    assert "title: Pay by card" in shown
    assert "statement: A buyer can pay so that orders close" in shown


# --------------------------------------------------------------------- list order
def test_list_order_is_r_then_f_then_s_then_a(home: Path, capsys) -> None:
    make_project()
    add_requirement()          # a criterion needs its requirement first
    add_criterion()
    add_spec()
    add_feature()

    assert run(["list"]) == 0
    lines = _out(capsys).splitlines()
    assert [line.split("  ")[0] for line in lines] == ["R1", "F1", "S1", "A1"]


def test_list_status_filter_keeps_only_that_state(home: Path, capsys) -> None:
    make_project()
    add_requirement(title="Pay by card")
    add_feature(title="Stripe integration")
    assert run(["list", "--status", "planned"]) == 0
    assert _out(capsys) == "F1  planned  core  -  Stripe integration  needs F:-  R:-  S:-\n"


def test_list_without_entries_prints_nothing(home: Path, capsys) -> None:
    make_project()
    assert run(["list"]) == 0
    assert _out(capsys) == ""


# -------------------------------------------------------------------------- show
def test_show_requirement_lists_its_criteria_and_history(home: Path, capsys) -> None:
    make_project()
    add_requirement(title="Pay by card", statement="A buyer can pay by card",
                    priority="must", rationale="Revenue", phase="mvp")
    add_criterion(requirement=1, text="Given a card, then it succeeds")
    add_criterion(requirement=1, text="Given a decline, then it is shown")

    assert run(["show", "R1"]) == 0
    lines = _out(capsys).splitlines()
    assert lines[0] == "id: R1"
    assert "rationale: Revenue" in lines
    assert "A1  not_run  R1  -  Given a card, then it succeeds" in lines
    assert "A2  not_run  R1  -  Given a decline, then it is shown" in lines
    assert lines[-1] == "history:"
    # no history row exists for a creation (S1 section 9)
    assert lines[lines.index("history:") + 1:] == []


def test_show_feature_and_spec_print_their_fields(home: Path, capsys) -> None:
    make_project()
    add_feature(title="Stripe integration", layer="core", slug="stripe",
                description="Card payments", infra=1, paths="src/pay.py")
    add_spec(title="Payments", summary="Use PaymentIntents with 3DS", paths="src/pay.py")

    assert run(["show", "F1"]) == 0
    feature_lines = _out(capsys).splitlines()
    assert feature_lines[0] == "id: F1"
    assert "slug: stripe" in feature_lines
    assert "paths: src/pay.py" in feature_lines
    assert "infra: 1" in feature_lines

    assert run(["show", "S1"]) == 0
    spec_lines = _out(capsys).splitlines()
    assert spec_lines[0] == "id: S1"
    assert "summary: Use PaymentIntents with 3DS" in spec_lines
    assert "paths: src/pay.py" in spec_lines


def test_show_history_lists_the_last_ten_newest_first(home: Path, con, capsys) -> None:
    make_project()
    add_requirement()
    add_feature()
    link_feature_requirement(con, PROJECT, 1, 1)
    for index in range(12):
        target = "approved" if index % 2 == 0 else "draft"
        pairs = ["rationale=Revenue"] if target == "approved" else []
        rc, _stdout, err = set_field(capsys, "R1", f"status={target}", *pairs,
                                     reason=f"r{index}")
        assert rc == 0, err

    assert run(["show", "R1"]) == 0
    lines = _out(capsys).splitlines()
    block = lines[lines.index("history:") + 1:]
    assert len(block) == 10                       # capped at the last 10
    reasons = [line.rsplit("[", 1)[1].rstrip("]") for line in block]
    assert reasons == [f"r{index}" for index in range(11, 1, -1)]
    assert "status approved -> draft" in block[0]


def test_show_after_a_status_change_prints_the_new_state(home: Path, con, capsys) -> None:
    make_project()
    add_requirement(statement="A buyer can pay by card")
    add_feature()
    link_feature_requirement(con, PROJECT, 1, 1)
    rc, out, err = set_field(capsys, "R1", "status=approved", "rationale=Revenue")
    assert rc == 0, err
    assert out == "R1 draft -> approved\n"

    assert run(["show", "R1"]) == 0
    lines = _out(capsys).splitlines()
    assert "status: approved" in lines
    assert "rationale: Revenue" in lines
    history = lines[lines.index("history:") + 1:]
    assert len(history) == 1
    assert history[0].endswith("status draft -> approved")


def test_list_shows_the_new_state_after_a_move(home: Path, con, capsys) -> None:
    make_project()
    add_requirement()
    add_feature()
    link_feature_requirement(con, PROJECT, 1, 1)
    move(capsys, "R1", "approved", "rationale=Revenue")

    assert run(["list", "R"]) == 0
    assert _out(capsys) == "R1  approved  must  -  Pay by card  F:1  S:-  A:0/0\n"
    assert run(["list", "R", "--status", "approved"]) == 0
    assert _out(capsys).startswith("R1  approved  ")
