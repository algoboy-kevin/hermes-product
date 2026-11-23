"""Supporting documents, DOC# (card HP-4).

Pinned here: `add DOC` with each body option (text, file copy, external ref with a
local hash) and the summary-only form; the field rules and the one-prd-per-project
rule; atomic creation (a failure after the body write leaves no row and no file);
`set` (title/kind/summary without history, body replace with history, the
supersede machine); doc links to every target kind with their refusals; and the
output contracts (compact DOC line, list ordering, show DOC, and A16's one-line
summary on `show F2` with no body).
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

import model
import store
from _helpers import (  # `home` is a pytest fixture imported for discovery
    add_criterion,
    add_doc,
    add_feature,
    add_requirement,
    add_spec,
    cli,
    history_rows,
    home,
    make_project,
    run,
    table_rows,
)

PROJECT = "shop"
SUMMARY = "How webhooks are processed."
SEP = "  "


# ------------------------------------------------------------------ add: bodies
def test_add_doc_minimal(home: Path, capsys) -> None:
    make_project()
    rc, out, err = cli(capsys, "add", "DOC", "--title", "Webhook flow",
                       "--kind", "design", "--summary", SUMMARY)
    assert rc == 0, err
    assert out == "DOC1 created\n"
    row = store.get_entity(PROJECT, "DOC1")["row"]
    assert row["status"] == "current"
    assert row["kind"] == "design"
    assert row["body_file"] is None
    assert row["body_ref"] is None


def test_add_doc_with_body_text_writes_the_file(home: Path, capsys) -> None:
    make_project()
    body = "# Payments\nintro\n"
    rc, out, err = cli(capsys, "add", "DOC", "--title", "Webhook flow", "--kind",
                       "design", "--summary", SUMMARY, "--body", body)
    assert rc == 0, err
    assert out == "DOC1 created\n"
    stored = store.docs_dir(PROJECT) / "DOC1.md"
    assert stored.read_text(encoding="utf-8") == body
    row = store.get_entity(PROJECT, "DOC1")["row"]
    assert row["body_file"] == "docs/DOC1.md"
    assert f"body: {len(body)} chars (doc read DOC1)" in cli(capsys, "show", "DOC1")[1]


def test_add_doc_with_body_file_copies_the_text(home: Path, capsys, tmp_path: Path) -> None:
    make_project()
    source = tmp_path / "notes.md"
    source.write_text("# Notes\nthe text\n", encoding="utf-8")
    rc, _out, err = cli(capsys, "add", "DOC", "--title", "Webhook flow", "--kind",
                        "research", "--summary", SUMMARY, "--body-file", str(source))
    assert rc == 0, err
    assert (store.docs_dir(PROJECT) / "DOC1.md").read_text(encoding="utf-8") == \
        "# Notes\nthe text\n"


def test_add_doc_with_a_local_body_ref_stores_ref_and_hash(home: Path, capsys,
                                                           tmp_path: Path) -> None:
    make_project()
    source = tmp_path / "design.md"
    source.write_text("# Design\nbody\n", encoding="utf-8")
    rc, _out, err = cli(capsys, "add", "DOC", "--title", "Webhook flow", "--kind",
                        "design", "--summary", SUMMARY, "--body-ref", str(source))
    assert rc == 0, err
    row = store.get_entity(PROJECT, "DOC1")["row"]
    assert row["body_ref"] == str(source)
    assert row["body_hash"] == hashlib.sha256(source.read_bytes()).hexdigest()
    assert row["body_file"] is None


def test_add_doc_with_a_url_body_ref_stores_no_hash(home: Path, capsys) -> None:
    make_project()
    rc, _out, err = cli(capsys, "add", "DOC", "--title", "Webhook flow", "--kind",
                        "design", "--summary", SUMMARY,
                        "--body-ref", "https://example.com/design.md")
    assert rc == 0, err
    row = store.get_entity(PROJECT, "DOC1")["row"]
    assert row["body_ref"] == "https://example.com/design.md"
    assert row["body_hash"] is None
    assert "body_ref: https://example.com/design.md" in cli(capsys, "show", "DOC1")[1]


def test_a_summary_only_document_is_allowed(home: Path, capsys) -> None:
    make_project()
    assert cli(capsys, "add", "DOC", "--title", "Webhook flow", "--kind", "context",
               "--summary", SUMMARY)[0] == 0
    row = store.get_entity(PROJECT, "DOC1")["row"]
    assert row["body_file"] is None and row["body_ref"] is None


def test_two_body_options_are_refused(home: Path, capsys) -> None:
    make_project()
    rc, _out, err = cli(capsys, "add", "DOC", "--title", "Webhook flow", "--kind",
                        "design", "--summary", SUMMARY, "--body", "x", "--body-ref", "y")
    assert rc == 1
    assert "pass only one of --body-file, --body, --body-ref" in err
    assert cli(capsys, "list", "DOC")[1] == ""


# ------------------------------------------------------------- add: field rules
def test_only_one_prd_per_project(home: Path, capsys) -> None:
    make_project()
    assert cli(capsys, "add", "DOC", "--title", "The PRD", "--kind", "prd",
               "--summary", "The product requirements.")[0] == 0
    rc, _out, err = cli(capsys, "add", "DOC", "--title", "Another PRD", "--kind",
                        "prd", "--summary", "A second one.")
    assert rc == 1
    assert "only one prd document per project (DOC1 is already prd)" in err


def test_moving_a_doc_away_from_prd_frees_the_slot(home: Path, capsys) -> None:
    make_project()
    assert cli(capsys, "add", "DOC", "--title", "The PRD", "--kind", "prd",
               "--summary", "The product requirements.")[0] == 0
    assert cli(capsys, "set", "DOC1", "kind=context")[0] == 0
    rc, out, err = cli(capsys, "add", "DOC", "--title", "Second PRD", "--kind", "prd",
                       "--summary", "Now the PRD.")
    assert rc == 0, err
    assert out == "DOC2 created\n"


def test_an_unknown_doc_kind_names_the_allowed_values(home: Path, capsys) -> None:
    make_project()
    rc, _out, err = cli(capsys, "add", "DOC", "--title", "Webhook flow", "--kind",
                        "nope", "--summary", SUMMARY)
    assert rc == 1
    assert "kind must be one of prd|spec|context|research|design|api|runbook|other" in err


@pytest.mark.parametrize("title", ["ab", "x" * 81])
def test_doc_title_length_is_validated(home: Path, capsys, title: str) -> None:
    make_project()
    rc, _out, err = cli(capsys, "add", "DOC", "--title", title, "--kind", "design",
                        "--summary", SUMMARY)
    assert rc == 1
    assert "title must be 3-80 characters" in err


def test_a_doc_summary_must_be_short_and_present(home: Path, capsys) -> None:
    make_project()
    rc, _out, err = cli(capsys, "add", "DOC", "--title", "Webhook flow", "--kind",
                        "design", "--summary", "")
    assert rc == 1
    assert "summary is required for DOC#" in err
    rc, _out, err = cli(capsys, "add", "DOC", "--title", "Webhook flow", "--kind",
                        "design", "--summary", "x" * 401)
    assert rc == 1
    assert "summary must be 1-2 sentences" in err and "401" in err
    add_doc(title="Webhook flow")
    rc, _out, err = cli(capsys, "set", "DOC1", "summary=")
    assert rc == 1
    assert "summary must not be empty" in err


def test_a_doc_needs_a_kind(home: Path, capsys) -> None:
    make_project()
    rc, _out, err = cli(capsys, "add", "DOC", "--title", "Webhook flow",
                        "--summary", SUMMARY)
    assert rc == 1
    assert "kind is required for DOC#" in err


# ------------------------------------------------------------------- atomic add
def test_a_failure_after_the_body_write_leaves_no_row_and_no_file(
    home: Path, capsys, monkeypatch
) -> None:
    make_project()

    def boom(_doc_id: int) -> None:
        raise RuntimeError("disk went away")

    # Scoped patch: a bare monkeypatch.undo() would also undo the `home` fixture's
    # HERMES_HOME and the conftest guard, so the asserts below would read the REAL store.
    with monkeypatch.context() as patch:
        patch.setattr(store, "_after_body_write", boom)
        with pytest.raises(RuntimeError):
            run(["add", "DOC", "--title", "Webhook flow", "--kind", "design",
                 "--summary", SUMMARY, "--body", "# Payments\n"])

    assert cli(capsys, "list", "DOC")[1] == ""
    assert not (store.docs_dir(PROJECT) / "DOC1.md").exists()
    assert history_rows(PROJECT, "doc", 1) == []


# ------------------------------------------------------------------------ set
def test_set_replaces_the_body_and_logs_it(home: Path, capsys, tmp_path: Path) -> None:
    make_project()
    add_doc(body="old body\n")
    replacement = tmp_path / "new.md"
    replacement.write_text("new body\n", encoding="utf-8")
    rc, out, err = cli(capsys, "set", "DOC1", "--body-file", str(replacement))
    assert rc == 0, err
    assert out == "DOC1 updated: body\n"
    assert (store.docs_dir(PROJECT) / "DOC1.md").read_text(encoding="utf-8") == "new body\n"
    assert [row["change"] for row in history_rows(PROJECT, "doc", 1)] == ["body changed"]


def test_set_kind_and_summary_write_no_history(home: Path, capsys) -> None:
    make_project()
    add_doc()
    rc, out, err = cli(capsys, "set", "DOC1", "kind=research", "summary=A new summary.")
    assert rc == 0, err
    assert out == "DOC1 updated: kind, summary\n"
    assert history_rows(PROJECT, "doc", 1) == []
    assert store.get_entity(PROJECT, "DOC1")["row"]["kind"] == "research"


def test_supersede_needs_superseded_by(home: Path, capsys) -> None:
    make_project()
    add_doc(title="Webhook flow")
    add_doc(title="Webhook flow v2")
    rc, _out, err = cli(capsys, "set", "DOC1", "status=superseded")
    assert rc == 1
    assert "needs superseded_by" in err
    assert store.get_entity(PROJECT, "DOC1")["row"]["status"] == "current"


def test_superseded_by_must_be_another_existing_doc_of_the_project(home: Path, capsys) -> None:
    make_project("shop")
    make_project("other")
    add_doc(title="Webhook flow")
    add_doc(title="Webhook flow v2")
    add_doc(project="other", title="Elsewhere")

    rc, _out, err = cli(capsys, "set", "DOC1", "status=superseded", "superseded_by=DOC1",
                        "--project", "shop")
    assert rc == 1
    assert "superseded_by cannot be DOC1 itself" in err
    rc, _out, err = cli(capsys, "set", "DOC1", "status=superseded", "superseded_by=DOC9",
                        "--project", "shop")
    assert rc == 1
    assert "DOC9 does not exist" in err
    # DOC3 exists, but in the other project: a supersede never crosses projects.
    rc, _out, err = cli(capsys, "set", "DOC1", "status=superseded", "superseded_by=DOC3",
                        "--project", "shop")
    assert rc == 1
    assert "DOC3 does not exist" in err
    assert store.get_entity("shop", "DOC1")["row"]["status"] == "current"


def test_supersede_works_once_and_is_terminal(home: Path, capsys) -> None:
    make_project()
    add_doc(title="Webhook flow")
    add_doc(title="Webhook flow v2")
    rc, out, err = cli(capsys, "set", "DOC1", "status=superseded", "superseded_by=DOC2")
    assert rc == 0, err
    assert out == "DOC1 current -> superseded\n"
    row = store.get_entity(PROJECT, "DOC1")["row"]
    assert row["status"] == "superseded" and row["superseded_by"] == 2
    assert [event["change"] for event in history_rows(PROJECT, "doc", 1)] == \
        ["status current -> superseded"]

    rc, _out, err = cli(capsys, "set", "DOC1", "status=current")
    assert rc == 1
    assert "terminal" in err


# ----------------------------------------------------------------- doc links
@pytest.mark.parametrize("target_kind", ["R", "A", "F", "S"])
def test_a_doc_links_to_every_target_kind(home: Path, capsys, target_kind: str) -> None:
    make_project()
    add_requirement()
    add_criterion()
    add_feature()
    add_spec()
    add_doc()
    target = f"{target_kind}1"
    rc, out, err = cli(capsys, "link", "DOC1", target)
    assert rc == 0, err
    assert out == f"DOC1 linked {target}\n"
    entity_type = {"R": "requirement", "A": "criterion", "F": "feature", "S": "spec"}[target_kind]
    assert table_rows(PROJECT, "doc_link") == [(1, entity_type, 1)]


def test_a_doc_link_is_order_insensitive(home: Path, capsys) -> None:
    make_project()
    add_feature()
    add_doc()
    rc, out, err = cli(capsys, "link", "F1", "DOC1")
    assert rc == 0, err
    assert out == "F1 linked DOC1\n"
    assert table_rows(PROJECT, "doc_link") == [(1, "feature", 1)]


def test_a_doc_link_logs_history_on_both_sides(home: Path, capsys) -> None:
    make_project()
    add_feature()
    add_doc()
    assert cli(capsys, "link", "DOC1", "F1")[0] == 0
    assert [row["change"] for row in history_rows(PROJECT, "doc", 1)] == ["linked F1"]
    assert [row["change"] for row in history_rows(PROJECT, "feature", 1)] == ["linked DOC1"]


def test_a_doc_cannot_link_to_another_doc(home: Path, capsys) -> None:
    make_project()
    add_doc(title="Webhook flow")
    add_doc(title="Webhook flow v2")
    rc, _out, err = cli(capsys, "link", "DOC1", "DOC2")
    assert rc == 1
    assert "a document links to an entry, not to another document" in err
    assert table_rows(PROJECT, "doc_link") == []


def test_a_retired_target_is_refused(home: Path, capsys) -> None:
    make_project()
    add_feature()
    add_doc()
    assert cli(capsys, "set", "F1", "status=cut", "status_reason=Deferred")[0] == 0
    rc, _out, err = cli(capsys, "link", "DOC1", "F1")
    assert rc == 1
    assert "cannot link F1: it is cut" in err


def test_a_superseded_doc_cannot_be_linked(home: Path, capsys) -> None:
    make_project()
    add_feature()
    add_doc(title="Webhook flow")
    add_doc(title="Webhook flow v2")
    assert cli(capsys, "set", "DOC1", "status=superseded", "superseded_by=DOC2")[0] == 0
    rc, _out, err = cli(capsys, "link", "DOC1", "F1")
    assert rc == 1
    assert "cannot link DOC1: it is superseded" in err


def test_a_duplicate_doc_link_is_refused(home: Path, capsys) -> None:
    make_project()
    add_feature()
    add_doc()
    assert cli(capsys, "link", "DOC1", "F1")[0] == 0
    rc, _out, err = cli(capsys, "link", "DOC1", "F1")
    assert rc == 1
    assert "already linked" in err
    assert table_rows(PROJECT, "doc_link") == [(1, "feature", 1)]


def test_a_cross_project_doc_link_is_refused(home: Path, capsys) -> None:
    make_project("shop")
    make_project("other")
    add_feature(project="other")
    add_doc(project="shop")
    rc, _out, err = cli(capsys, "link", "DOC1", "F1", "--project", "shop")
    assert rc == 1
    assert "F1 does not exist in project 'shop'" in err
    assert table_rows(PROJECT, "doc_link") == []


def test_unlink_a_doc_link_and_its_missing_case(home: Path, capsys) -> None:
    make_project()
    add_feature()
    add_doc()
    assert cli(capsys, "link", "DOC1", "F1")[0] == 0
    rc, out, err = cli(capsys, "unlink", "DOC1", "F1")
    assert rc == 0, err
    assert out == "DOC1 unlinked F1\n"
    assert table_rows(PROJECT, "doc_link") == []
    rc, _out, err = cli(capsys, "unlink", "DOC1", "F1")
    assert rc == 1
    assert "no such link: DOC1 F1" in err


# ---------------------------------------------------------------------- output
def test_the_compact_doc_line(home: Path, capsys) -> None:
    make_project()
    add_requirement()
    add_criterion()
    add_feature()
    add_doc()
    assert cli(capsys, "link", "DOC1", "A1")[0] == 0
    assert cli(capsys, "link", "DOC1", "R1")[0] == 0
    rc, out, err = cli(capsys, "list", "DOC")
    assert rc == 0, err
    assert out == "DOC1  design  current  Webhook flow  links R1,A1\n"


def test_bare_list_appends_doc_lines_after_the_a_lines(home: Path, capsys) -> None:
    make_project()
    add_requirement()
    add_criterion()
    add_feature()
    add_spec()
    add_doc()
    assert run(["list"]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert [line.split(SEP)[0] for line in lines] == ["R1", "F1", "S1", "A1", "DOC1"]


def test_bare_list_is_unchanged_without_docs(home: Path, capsys) -> None:
    make_project()
    add_requirement()
    assert run(["list"]) == 0
    assert capsys.readouterr().out == "R1  draft  must  -  Pay by card  F:-  S:-  A:0/0\n"


def test_show_doc_prints_fields_body_size_and_linked_entries(home: Path, capsys) -> None:
    make_project()
    add_requirement()
    add_doc(body="# Payments\nsecret body text\n")
    assert cli(capsys, "link", "DOC1", "R1")[0] == 0

    rc, out, err = cli(capsys, "show", "DOC1")
    assert rc == 0, err
    lines = out.splitlines()
    assert lines[0] == "id: DOC1"
    assert "kind: design" in lines
    assert f"body: {len('# Payments\nsecret body text\n')} chars (doc read DOC1)" in lines
    assert "secret body text" not in out
    assert any(line.startswith("R1  draft") for line in lines)


def test_show_feature_lists_the_doc_summary_and_no_body_A16(home: Path, capsys) -> None:
    make_project()
    add_feature(title="Accounts")
    add_doc(body="# Webhooks\nsecret body text\n",
            summary="How webhooks are processed.")
    assert cli(capsys, "link", "DOC1", "F1")[0] == 0

    rc, out, err = cli(capsys, "show", "F1")
    assert rc == 0, err
    lines = out.splitlines()
    assert "docs:" in lines
    assert "DOC1  design  How webhooks are processed." in lines
    assert "secret body text" not in out


def test_show_has_no_docs_section_without_docs(home: Path, capsys) -> None:
    make_project()
    add_requirement()
    rc, out, err = cli(capsys, "show", "R1")
    assert rc == 0, err
    assert "docs:" not in out.splitlines()


def test_a_superseded_doc_still_appears_and_shows_its_status(home: Path, capsys) -> None:
    make_project()
    add_feature()
    add_doc(title="Webhook flow")
    add_doc(title="Webhook flow v2")
    assert cli(capsys, "link", "DOC1", "F1")[0] == 0
    assert cli(capsys, "set", "DOC1", "status=superseded", "superseded_by=DOC2")[0] == 0

    assert "DOC1  design  How webhooks are processed." in cli(capsys, "show", "F1")[1]
    assert "status: superseded" in cli(capsys, "show", "DOC1")[1]
    assert "superseded_by: DOC2" in cli(capsys, "show", "DOC1")[1]
    assert "DOC1  design  superseded  Webhook flow  links F1" in cli(capsys, "list", "DOC")[1]


SEP = "  "
