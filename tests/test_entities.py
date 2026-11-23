"""Entity tests for the `product` plugin (card HP-2, STEP 1 and STEP 2).

What is pinned here: `add` for R/A/F/S (id printed, entry listed), per-project ids,
every required-field and enum error naming the field with nothing created and no
id consumed, atomic creation, `parse_ref`, and `set` (plain fields, unknown key
error, validated values). Every test runs against a tmp HERMES_HOME.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import model
import store
from _helpers import (  # `home` is a pytest fixture imported for discovery
    add_feature,
    add_requirement,
    home,
    make_project,
    run,
    set_field,
)

PROJECT = "shop"


def _out(capsys) -> str:
    return capsys.readouterr().out


def _err(capsys) -> str:
    return capsys.readouterr().err


# ------------------------------------------------------------------ add: happy path
def test_add_requirement_prints_the_new_id_and_lists_it(home: Path, capsys) -> None:
    make_project()
    rc = run(["add", "R", "--title", "Pay by card", "--statement",
              "A buyer can pay by card at checkout", "--priority", "must"])
    assert rc == 0
    assert _out(capsys) == "R1 created\n"

    assert run(["list", "R"]) == 0
    assert _out(capsys) == "R1  draft  must  -  Pay by card  F:-  S:-  A:0/0\n"


def test_add_criterion_needs_an_existing_requirement_and_lists_it(home: Path, capsys) -> None:
    make_project()
    add_requirement()
    rc = run(["add", "A", "--req", "R1", "--text", "Given a card, when paid, then it succeeds"])
    assert rc == 0
    assert _out(capsys) == "A1 created\n"

    assert run(["list", "A"]) == 0
    assert _out(capsys) == "A1  not_run  R1  -  Given a card, when paid, then it succeeds\n"


def test_add_feature_and_spec_list_with_their_own_format(home: Path, capsys) -> None:
    make_project()
    assert run(["add", "F", "--title", "Stripe integration", "--layer", "core"]) == 0
    assert _out(capsys) == "F1 created\n"
    assert run(["add", "S", "--title", "Payments", "--summary", "Use PaymentIntents with 3DS"]) == 0
    assert _out(capsys) == "S1 created\n"

    assert run(["list", "F"]) == 0
    assert _out(capsys) == "F1  planned  core  -  Stripe integration  needs F:-  R:-  S:-\n"
    assert run(["list", "S"]) == 0
    assert _out(capsys) == "S1  draft  Payments  R:-  F:-\n"


def test_optional_fields_are_stored(home: Path, capsys) -> None:
    make_project()
    assert run(["add", "R", "--title", "Pay by card", "--statement", "A buyer can pay",
                "--priority", "should", "--rationale", "Revenue", "--phase", "mvp",
                "--notes", "short note", "--hypothesis-ref", "H1", "--legacy",
                "--prd-ref", "PRD.md#goals"]) == 0
    capsys.readouterr()
    stored = store.get_entity(PROJECT, "R1")["row"]
    assert stored["priority"] == "should"
    assert stored["phase"] == "mvp"
    assert stored["rationale"] == "Revenue"
    assert stored["notes"] == "short note"
    assert stored["hypothesis_ref"] == "H1"
    assert stored["legacy"] == 1
    assert stored["prd_ref"] == "PRD.md#goals"
    assert stored["status"] == "draft"
    assert stored["blocked"] == 0


def test_paths_are_stored_as_a_json_list(home: Path, capsys) -> None:
    make_project()
    assert run(["add", "F", "--title", "Stripe integration", "--layer", "core",
                "--paths", "src/pay.py,src/cards/"]) == 0
    capsys.readouterr()
    assert store.get_entity(PROJECT, "F1")["row"]["paths"] == '["src/pay.py", "src/cards/"]'


def test_feature_slug_is_stored_and_addressable_by_slug(home: Path, capsys) -> None:
    make_project()
    assert run(["add", "F", "--title", "Stripe integration", "--layer", "core",
                "--slug", "stripe"]) == 0
    capsys.readouterr()
    assert store.get_entity(PROJECT, "F:stripe")["row"]["id"] == 1
    assert run(["show", "F:stripe"]) == 0
    assert "slug: stripe" in _out(capsys)


# ------------------------------------------------------------------ ids per project
def test_ids_are_per_project(home: Path, capsys) -> None:
    make_project("shop")
    make_project("other")
    assert run(["add", "R", "--title", "Pay by card", "--statement", "A buyer can pay",
                "--priority", "must", "--project", "shop"]) == 0
    assert _out(capsys) == "R1 created\n"
    assert run(["add", "R", "--title", "Pay later", "--statement", "A buyer can defer",
                "--priority", "could", "--project", "other"]) == 0
    assert _out(capsys) == "R1 created\n"
    assert run(["add", "R", "--title", "Refund", "--statement", "A buyer can refund",
                "--priority", "must", "--project", "shop"]) == 0
    assert _out(capsys) == "R2 created\n"


# ------------------------------------------------------- required fields and enums
@pytest.mark.parametrize(
    "argv,word",
    [
        (["--statement", "A buyer can pay", "--priority", "must"], "title"),
        (["--title", "Pay by card", "--priority", "must"], "statement"),
        (["--title", "Pay by card", "--statement", "A buyer can pay"], "priority"),
    ],
)
def test_missing_required_field_names_it_and_creates_nothing(
    home: Path, capsys, argv: list, word: str
) -> None:
    make_project()
    rc = run(["add", "R", *argv])
    assert rc == 1
    assert word in _err(capsys)
    assert store.list_entities(PROJECT, "requirement") == []

    # the id was not consumed: the next successful add is R1
    assert run(["add", "R", "--title", "Pay by card", "--statement", "A buyer can pay",
                "--priority", "must"]) == 0
    assert _out(capsys) == "R1 created\n"


@pytest.mark.parametrize("title", ["ab", "x" * 81, ""])
def test_title_length_is_validated(home: Path, capsys, title: str) -> None:
    make_project()
    rc = run(["add", "R", "--title", title, "--statement", "A buyer can pay",
              "--priority", "must"])
    assert rc == 1
    assert "title" in _err(capsys)
    assert store.list_entities(PROJECT, "requirement") == []


@pytest.mark.parametrize(
    "argv,word,allowed",
    [
        (["add", "R", "--title", "Pay by card", "--statement", "A buyer can pay",
          "--priority", "urgent"], "priority", "must|should|could"),
        (["add", "F", "--title", "Stripe integration", "--layer", "plumbing"],
         "layer", "foundation|core|growth"),
    ],
)
def test_bad_enum_names_the_field_and_the_allowed_values(
    home: Path, capsys, argv: list, word: str, allowed: str
) -> None:
    make_project()
    rc = run(argv)
    captured = capsys.readouterr()
    assert rc == 1
    assert word in captured.err
    assert allowed in captured.err
    assert captured.out == ""


def test_empty_summary_is_refused(home: Path, capsys) -> None:
    make_project()
    assert run(["add", "S", "--title", "Payments", "--summary", "  "]) == 1
    assert "summary" in _err(capsys)


def test_duplicate_feature_slug_is_refused(home: Path, capsys) -> None:
    make_project()
    assert run(["add", "F", "--title", "Stripe integration", "--layer", "core",
                "--slug", "stripe"]) == 0
    capsys.readouterr()
    rc = run(["add", "F", "--title", "Adyen integration", "--layer", "core",
              "--slug", "stripe"])
    err = _err(capsys)
    assert rc == 1
    assert "slug" in err
    assert "already" in err
    assert len(store.list_entities(PROJECT, "feature")) == 1


@pytest.mark.parametrize("slug", ["Stripe", "-stripe", "stripe_1", "x" * 41])
def test_bad_feature_slug_names_the_rule(home: Path, capsys, slug: str) -> None:
    make_project()
    assert run(["add", "F", "--title", "Stripe integration", "--layer", "core",
                f"--slug={slug}"]) == 1
    assert "slug" in _err(capsys)


def test_unknown_requirement_ref_is_refused(home: Path, capsys) -> None:
    make_project()
    rc = run(["add", "A", "--req", "R9", "--text", "Given a card, then it succeeds"])
    assert rc == 1
    assert "R9" in _err(capsys)
    assert store.list_entities(PROJECT, "criterion") == []


def test_a_criterion_cannot_hang_off_a_feature_ref(home: Path, capsys) -> None:
    make_project()
    add_feature()
    rc = run(["add", "A", "--req", "F1", "--text", "Given a card, then it succeeds"])
    assert rc == 1
    assert "requirement" in _err(capsys)


def test_cross_project_requirement_ref_is_refused(home: Path, capsys) -> None:
    make_project("shop")
    make_project("other")
    add_requirement("shop")
    rc = run(["add", "A", "--req", "R1", "--text", "Given a card, then it succeeds",
              "--project", "other"])
    assert rc == 1
    assert "R1" in _err(capsys)
    assert store.list_entities("other", "criterion") == []


# --------------------------------------------------------------------- atomic ids
def test_creation_is_atomic_so_a_failure_leaves_no_gap(home: Path, capsys) -> None:
    make_project()
    add_requirement()                      # R1 exists

    def boom(*args, **kwargs):
        raise RuntimeError("forced failure after the id was allocated")

    with pytest.MonkeyPatch.context() as patch:      # scoped: keeps HERMES_HOME set
        patch.setattr(store, "_insert_row", boom)
        with pytest.raises(RuntimeError):
            store.create_entity(PROJECT, "requirement",
                                {"title": "Pay by card", "statement": "A buyer can pay",
                                 "priority": "must"})

    assert [row["id"] for row in store.list_entities(PROJECT, "requirement")] == [1]
    # the rolled-back allocation did not burn R2
    assert store.create_entity(PROJECT, "requirement",
                               {"title": "Pay by card", "statement": "A buyer can pay",
                                "priority": "must"})["id"] == 2


def test_next_id_commit_flag_does_not_commit(home: Path) -> None:
    make_project()
    connection = store.connect()
    try:
        assert store.next_id(connection, PROJECT, "R", commit=False) == 1
        connection.rollback()
        assert store.next_id(connection, PROJECT, "R") == 1
    finally:
        connection.close()


# ---------------------------------------------------------------------- parse_ref
@pytest.mark.parametrize(
    "text,expected",
    [
        ("R3", ("requirement", 3)),
        ("A21", ("criterion", 21)),
        ("F7", ("feature", 7)),
        ("S2", ("spec", 2)),
        ("DOC12", ("doc", 12)),
        ("F:stripe", ("feature", "stripe")),
    ],
)
def test_parse_ref_accepts_the_documented_spellings(text: str, expected: tuple) -> None:
    assert model.parse_ref(text) == expected


@pytest.mark.parametrize("text", ["r3", "3R", "R", "R3x", "R-3", "", "X1", "F:", "R 3"])
def test_parse_ref_rejects_anything_else(text: str) -> None:
    with pytest.raises(model.ValidationError):
        model.parse_ref(text)


def test_format_ref_round_trips() -> None:
    for text in ("R3", "A21", "F7", "S2", "DOC12"):
        kind, value = model.parse_ref(text)
        assert model.format_ref(kind, value) == text
    assert model.format_ref("feature", "stripe") == "F:stripe"


# ------------------------------------------------------------------------- DOC
def test_add_doc_is_not_implemented_here(home: Path, capsys) -> None:
    make_project()
    rc = run(["add", "DOC", "--title", "Webhook flow"])
    captured = capsys.readouterr()
    assert rc == 2
    assert captured.err == "NOT IMPLEMENTED: add DOC (card HP-4)\n"
    assert captured.out == ""


# ------------------------------------------------------------- add cannot set status
@pytest.mark.parametrize("kind", ["R", "A", "F", "S"])
def test_add_has_no_status_option(home: Path, capsys, kind: str) -> None:
    from _helpers import parser

    with pytest.raises(SystemExit):
        parser().parse_args(["add", kind, "--status", "approved"])


# -------------------------------------------------------------------------- show
def test_show_prints_fields_in_s1_order(home: Path, capsys) -> None:
    make_project()
    add_requirement(title="Pay by card", statement="A buyer can pay by card",
                    priority="must", rationale="Revenue")
    assert run(["show", "R1"]) == 0
    lines = _out(capsys).splitlines()
    assert lines[0] == "id: R1"
    assert lines[1] == "title: Pay by card"
    assert lines[2] == "statement: A buyer can pay by card"
    assert lines[3] == "rationale: Revenue"
    assert lines[4] == "priority: must"
    assert "status: draft" in lines
    assert lines[-1] == "history:"


def test_show_unknown_ref_exits_1(home: Path, capsys) -> None:
    make_project()
    assert run(["show", "R9"]) == 1
    assert "R9" in _err(capsys)


# -------------------------------------------------------------------------- list
def test_list_limit_and_status_filter(home: Path, capsys) -> None:
    make_project()
    for index in range(3):
        add_requirement(title=f"Requirement {index}")
    assert run(["list", "R", "--limit", "2"]) == 0
    assert len(_out(capsys).splitlines()) == 2
    assert run(["list", "R", "--status", "approved"]) == 0
    assert _out(capsys) == ""


# --------------------------------------------------------------------------- set
def test_set_edits_plain_fields(home: Path, capsys) -> None:
    make_project()
    add_requirement()
    rc, out, err = set_field(capsys, "R1", "title=Pay by card online", "phase=mvp")
    assert rc == 0, err
    assert out == "R1 updated: title, phase\n"
    stored = store.get_entity(PROJECT, "R1")["row"]
    assert stored["title"] == "Pay by card online"
    assert stored["phase"] == "mvp"


def test_set_unknown_key_lists_the_editable_keys(home: Path, capsys) -> None:
    make_project()
    add_requirement()
    rc, out, err = set_field(capsys, "R1", "colour=blue")
    assert rc == 1
    assert "colour" in err
    assert out == ""
    for key in ("title", "statement", "status", "priority", "superseded_by"):
        assert key in err


@pytest.mark.parametrize(
    "pair,word",
    [
        ("priority=urgent", "priority"),
        ("status=shipped", "shipped"),
        ("blocked=1", "blocked_reason"),
        ("title=ab", "title"),
    ],
)
def test_set_validates_the_value_it_is_given(home: Path, capsys, pair: str, word: str) -> None:
    make_project()
    add_requirement()
    rc, _out, err = set_field(capsys, "R1", pair)
    assert rc == 1
    assert word in err


def test_set_requires_key_equals_value(home: Path, capsys) -> None:
    make_project()
    add_requirement()
    rc, _out, err = set_field(capsys, "R1", "approved")
    assert rc == 1
    assert "key=value" in err


def test_set_on_a_missing_ref_exits_1(home: Path, capsys) -> None:
    make_project()
    rc, _out, err = set_field(capsys, "R9", "phase=mvp")
    assert rc == 1
    assert "R9" in err


def test_set_feature_slug_must_stay_unique(home: Path, capsys) -> None:
    make_project()
    add_feature(slug="stripe")
    add_feature(title="Accounts")
    rc, _out, err = set_field(capsys, "F2", "slug=stripe")
    assert rc == 1
    assert "slug" in err
    assert store.get_entity(PROJECT, "F2")["row"]["slug"] is None


def test_set_does_not_consume_an_id(home: Path, capsys) -> None:
    make_project()
    add_requirement()
    set_field(capsys, "R1", "colour=blue")
    assert run(["add", "R", "--title", "Pay later", "--statement", "A buyer can defer",
                "--priority", "could"]) == 0
    assert _out(capsys) == "R2 created\n"


def test_set_paths_round_trips_as_a_json_list(home: Path, capsys) -> None:
    make_project()
    add_feature()
    rc, out, err = set_field(capsys, "F1", "paths=src/a.py,src/b/")
    assert rc == 0, err
    assert out == "F1 updated: paths\n"
    assert store.get_entity(PROJECT, "F1")["row"]["paths"] == '["src/a.py", "src/b/"]'
    assert run(["show", "F1"]) == 0
    assert "paths: src/a.py, src/b/" in _out(capsys)
