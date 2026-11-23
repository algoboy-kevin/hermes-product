"""Trace tests (HP-6): `product trace <ref>`, one hop (`--depth 2` adds one more).

Trace must not invent information: every line comes from stored data or from
plan's derived values, sections are omitted when empty, and the renderers match
the brief's examples (`trace R3` lists features, specs, criteria and cards;
`trace A21` lists its requirement and `via R1:`; `trace DOC` lists its links).
Nothing here reads docs/.
"""
from __future__ import annotations

import pytest

import store
from _helpers import (  # `home`/`con` are fixtures imported for discovery
    add_criterion, add_doc, add_external_link, add_feature, add_requirement, add_spec,
    cli, con, home, link_feature_requirement, link_feature_spec, link_spec_requirement,
    make_project,
)


# ------------------------------------------------------------------ one rich project
def build_trace_fixture() -> None:
    make_project()
    add_requirement(title="Pay by card", priority="must", phase="mvp",
                    rationale="Revenue at checkout", prd_ref="PRD.md#goals")   # R1
    add_requirement(title="Keep a ledger", priority="should", phase="mvp")   # R2
    add_criterion(requirement=1, text="Visa pays")                # A1
    add_criterion(requirement=1, text="Mastercard pays")          # A2
    add_criterion(requirement=2, text="Ledger balances")          # A3
    add_feature(title="Stripe integration", layer="core")         # F1
    add_feature(title="Ledger service", layer="foundation")       # F2
    add_spec(title="Checkout spec")                           # S1
    add_spec(title="Ledger spec")                             # S2
    add_doc(title="Checkout design", kind="design")               # DOC1
    add_doc(title="A1 context", kind="context")                   # DOC2
    con = store.connect()
    try:
        link_feature_requirement(con, "shop", 1, 1)      # F1-R1
        link_feature_requirement(con, "shop", 2, 2)      # F2-R2
        link_spec_requirement(con, "shop", 1, 1)         # S1-R1
        link_spec_requirement(con, "shop", 2, 2)         # S2-R2
        link_feature_spec(con, "shop", 1, 1)             # F1-S1
        link_feature_spec(con, "shop", 2, 2)             # F2-S2
        store.link_entries("shop", "R2", needs="R1", con=con)   # R2 needs R1
        store.link_entries("shop", "S2", needs="S1", con=con)   # S2 needs S1
        store.link_entries("shop", "F1", needs="F2", con=con)   # F1 needs F2
        con.execute("INSERT INTO doc_link(project, doc_id, entity_type, entity_id)"
                    " VALUES ('shop', 1, 'requirement', 1)")
        con.execute("INSERT INTO doc_link(project, doc_id, entity_type, entity_id)"
                    " VALUES ('shop', 2, 'criterion', 1)")
        con.commit()
        add_external_link(con, "shop", "requirement", 1, system="kanban",
                          external_id="SHOP-3", url="https://kanban.example/SHOP-3",
                          role="tracks")
        add_external_link(con, "shop", "requirement", 2, system="issue",
                          external_id="GH-9", url="https://github.example/9")
        # states
        store.update_entity("shop", "R1", {"status": "approved"}, con=con)
        store.update_entity("shop", "R1", {"status": "in_progress"}, con=con)
        store.update_entity("shop", "A1", {"status": "passed", "evidence": "test_pay",
                                           "evidence_level": "unit"}, con=con)
        store.update_entity("shop", "S1", {"status": "approved"}, con=con)
        store.update_entity("shop", "S2", {"status": "approved"}, con=con)
    finally:
        con.close()


@pytest.fixture()
def shop(home) -> None:
    build_trace_fixture()


# ------------------------------------------------------------------------- entry lines
def test_trace_r_lists_four_kinds(shop, capsys) -> None:
    """The trace of an R lists its features, specs, criteria and cards."""
    out_text = trace(capsys, "trace", "R1")
    assert out_text.startswith("R1 ")
    assert "R1  in_progress  must  mvp" in out_text
    assert "features:" in out_text and "F1 " in out_text
    assert "specs:" in out_text and "S1 " in out_text
    assert "criteria:" in out_text and "A1 " in out_text and "A2 " in out_text
    assert "cards:" in out_text and "kanban:SHOP-3" in out_text


def test_trace_r_derived_progress_and_evidence(shop, capsys) -> None:
    out_text = trace(capsys, "trace", "R1")
    assert "progress: 1/2 criteria" in out_text
    assert "evidence: strongest unit, weakest unit" in out_text


def test_trace_r_evidence_none(shop, capsys) -> None:
    out_text = trace(capsys, "trace", "R2")
    assert "evidence: none" in out_text


def test_trace_empty_sections_omitted(shop, capsys) -> None:
    out_text = trace(capsys, "trace", "R2")
    assert "docs:" not in out_text          # nothing is linked to R2 or its criteria
    assert "needed by:" not in out_text     # nothing needs R2
    assert "features:" in out_text          # F2 is linked -> present


def test_trace_needs_and_needed_by(shop, capsys) -> None:
    out_text = trace(capsys, "trace", "R2")
    assert "needs:" in out_text
    needs = out_text.split("needs:")[1]
    assert "R1 " in needs.splitlines()[1]
    out_text = trace(capsys, "trace", "R1")
    assert "needed by:" in out_text and "R2 " in out_text


def test_trace_f_sections(shop, capsys) -> None:
    out_text = trace(capsys, "trace", "F1")
    assert out_text.startswith("F1 ")
    assert "progress: 0/1 requirements done" in out_text
    assert "requirements:" in out_text and "R1 " in out_text
    assert "specs:" in out_text and "S1 " in out_text
    assert "needs:" in out_text and "F2 " in out_text
    assert "needed by:" not in out_text


def test_trace_s_sections(shop, capsys) -> None:
    out_text = trace(capsys, "trace", "S1")
    assert out_text.startswith("S1 ")
    assert "requirements:" in out_text and "R1 " in out_text
    assert "features:" in out_text and "F1 " in out_text
    assert "needed by:" in out_text and "S2 " in out_text


def test_trace_a_sections(shop, capsys) -> None:
    out_text = trace(capsys, "trace", "A1")
    assert out_text.startswith("A1 ")
    assert "requirement:" in out_text and "R1 " in out_text
    assert "via R1:" in out_text and "F1 " in out_text
    assert "prd: PRD.md#goals" in out_text
    # DOC2 (linked to A1) and DOC1 (linked to R1) both show
    assert "docs:" in out_text and "DOC1 " in out_text and "DOC2 " in out_text


def test_trace_doc_lists_its_links(shop, capsys) -> None:
    out_text = trace(capsys, "trace", "DOC1")
    assert out_text.startswith("DOC1 ")
    assert "links:" in out_text and "R1 " in out_text


# --------------------------------------------------------------------------- formatting
def test_trace_unknown_ref_exits_1(shop, capsys) -> None:
    rc, _out_text, err = cli(capsys, "trace", "R99")
    assert rc == 1
    assert "R99" in err


def test_trace_bad_depth_exits_1(shop, capsys) -> None:
    rc, _out_text, err = cli(capsys, "trace", "R1", "--depth", "3")
    assert rc == 1
    assert "depth" in err


def test_trace_depth_one_is_the_default(shop, capsys) -> None:
    assert trace(capsys, "trace", "R1") == trace(capsys, "trace", "R1", "--depth", "1")


def test_trace_depth_two_nests_one_hop(shop, capsys) -> None:
    out_text = trace(capsys, "trace", "R1", "--depth", "2")
    lines = out_text.splitlines()
    # the F1 line inside `features:` and its nested sections one hop deeper
    f1_line = next(i for i, line in enumerate(lines) if line.strip().startswith("F1 "))
    assert lines[f1_line].startswith("  F1 ")
    nested = lines[f1_line + 1]
    assert nested.startswith("    ")                 # indented 4 spaces
    assert "progress: 0/1 requirements done" in nested
    assert any(line.startswith("    requirements:") for line in lines[f1_line + 1:])
    assert any(line.startswith("      R1 ") for line in lines[f1_line + 1:])
    # one hop only: F1's R1 expansion is not itself expanded
    assert not any(line.startswith("          ") for line in lines)


def test_trace_depth_two_expands_each_item_once(shop, capsys) -> None:
    out_text = trace(capsys, "trace", "R1", "--depth", "2")
    # F1 is directly linked to R1 (expanded once) and again inside S1's sections
    # (compact line only)
    assert out_text.count("progress: 0/1 requirements done") == 1


def test_trace_cards_line_format(shop, capsys) -> None:
    out_text = trace(capsys, "trace", "R2")
    assert "issue:GH-9  -  https://github.example/9" in out_text
    out_text = trace(capsys, "trace", "R1")
    assert "kanban:SHOP-3  tracks  https://kanban.example/SHOP-3" in out_text


def test_trace_section_lines_keep_links_except_docs(shop, capsys) -> None:
    """The brief: compact lines as in list; only DOC lines drop the links column."""
    out_text = trace(capsys, "trace", "F1")
    requirement_lines = [line for line in out_text.splitlines()
                         if line.strip().startswith("R1 ")]
    assert requirement_lines and " F:" in requirement_lines[0]   # R lines keep links
    out_text = trace(capsys, "trace", "A1")
    doc_lines = [line for line in out_text.splitlines()
                 if line.strip().startswith("DOC1 ")]
    assert doc_lines and "links" not in doc_lines[0]             # DOC lines do not


# ------------------------------------------------------------------------- the runner
def trace(capsys, *words: str) -> str:
    rc, out_text, err = cli(capsys, *words)
    assert rc == 0, err
    return out_text
