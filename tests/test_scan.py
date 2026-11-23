"""HP-8a: the reference scanner core (``scan.py``), pure and store-free.

``find_references`` finds ``[R3]`` / ``[A21]`` / ``[F7]`` / ``[S12]`` / ``[DOC4]``
and ``[F:slug]`` in Markdown while ignoring fenced code, inline code spans and
escaped brackets, and keeps line/column positions and duplicates. ``check_references``
turns a resolver callback into problems, ``format_problem`` renders one line, and
``anchor_ids`` / ``prd_anchor_ok`` resolve ``PRD.md#anchor`` fragments.

A18 is the PRD case: a mention of R99 that does not exist is a broken reference.
"""

from __future__ import annotations

import pytest

import scan


# ------------------------------------------------------------ reference shapes
def test_numeric_prefixes_each_kind() -> None:
    refs = scan.find_references("[R3] [A21] [F7] [S12] [DOC4]")
    assert [ref.text for ref in refs] == ["R3", "A21", "F7", "S12", "DOC4"]
    assert [ref.raw for ref in refs] == ["[R3]", "[A21]", "[F7]", "[S12]", "[DOC4]"]
    assert [(ref.line, ref.col) for ref in refs] == [
        (1, 1), (1, 6), (1, 12), (1, 17), (1, 23),
    ]


def test_ref_named_tuple_equality() -> None:
    assert scan.find_references("[DOC4]") == [scan.Ref("DOC4", 1, 1, "[DOC4]")]


def test_slug_reference_form() -> None:
    refs = scan.find_references("[F:stripe] [F:a] [F:a1-b2] [F:9]")
    assert [ref.text for ref in refs] == ["F:stripe", "F:a", "F:a1-b2", "F:9"]


def test_slug_length_boundary() -> None:
    forty = "a" * 40
    forty_one = "a" * 41
    assert [ref.text for ref in scan.find_references(f"[F:{forty}]")] == [f"F:{forty}"]
    assert scan.find_references(f"[F:{forty_one}]") == []


def test_slug_rejects_uppercase_underscore_and_leading_hyphen() -> None:
    assert scan.find_references("[F:Stripe]") == []
    assert scan.find_references("[F:stri_pe]") == []
    assert scan.find_references("[F:-stripe]") == []
    assert scan.find_references("[F:stripe/]") == []


def test_lowercase_prefix_is_not_a_reference() -> None:
    assert scan.find_references("[r3] [a21] [f7] [s12] [doc4] [f:stripe]") == []


def test_leading_zero_is_not_a_reference() -> None:
    assert scan.find_references("[R03] [A021] [F07] [S012] [DOC04] [R0]") == []
    assert [ref.text for ref in scan.find_references("[R10] [DOC10]")] == ["R10", "DOC10"]


def test_empty_text_has_no_references() -> None:
    assert scan.find_references("") == []


def test_line_and_column_on_several_lines() -> None:
    text = "intro [R1]\n\nsee [A2] and [S3]\n"
    assert [(ref.text, ref.line, ref.col) for ref in scan.find_references(text)] == [
        ("R1", 1, 7),
        ("A2", 3, 5),
        ("S3", 3, 14),
    ]


def test_unicode_before_a_reference_counts_characters() -> None:
    # "héllo 日本語 " is 10 code points; byte offsets would report a larger column.
    refs = scan.find_references("héllo 日本語 [R3]")
    assert [(ref.line, ref.col) for ref in refs] == [(1, 11)]


# -------------------------------------------------------------- skipped forms
def test_fenced_code_block_is_skipped() -> None:
    text = "before [R1]\n```python\n[R2]\n```\n[R3]\n"
    assert [ref.text for ref in scan.find_references(text)] == ["R1", "R3"]


def test_tilde_fence_is_skipped() -> None:
    text = "~~~\n[S1]\n~~~\n[A1]\n"
    assert [ref.text for ref in scan.find_references(text)] == ["A1"]


def test_unclosed_fence_hides_the_rest() -> None:
    text = "```\n[R1]\n[R2]\n"
    assert scan.find_references(text) == []


def test_fence_closes_only_on_its_own_marker() -> None:
    # A ``` line inside a ~~~ block is code, not a closing marker (same rule as docs_text).
    text = "~~~\n[R1]\n```\n[R2]\n~~~\n[R3]\n"
    assert [ref.text for ref in scan.find_references(text)] == ["R3"]


def test_inline_code_span_is_skipped() -> None:
    assert [ref.text for ref in scan.find_references("`[R1]` and [R2]")] == ["R2"]


def test_double_backtick_code_span_is_skipped() -> None:
    assert scan.find_references("``[R3]`` [R4]") == [scan.Ref("R4", 1, 10, "[R4]")]


def test_escaped_bracket_is_not_a_reference() -> None:
    assert [ref.text for ref in scan.find_references(r"\[R3] and [R4]")] == ["R4"]


def test_markdown_link_text_is_a_reference() -> None:
    refs = scan.find_references("see [S1](specs/S1-data-model.md) and [F:stripe](x.md)")
    assert [(ref.text, ref.col, ref.raw) for ref in refs] == [
        ("S1", 5, "[S1]"),
        ("F:stripe", 38, "[F:stripe]"),
    ]


def test_multi_reference_bracket_group_is_not_a_reference() -> None:
    assert scan.find_references("[R1, R2] [R1 R2] [R1;R2]") == []


def test_bracket_with_inner_spaces_is_not_a_reference() -> None:
    assert scan.find_references("[ R3 ]") == []


def test_duplicates_are_kept_in_document_order() -> None:
    refs = scan.find_references("[R3] then [A1] then [R3]")
    assert [(ref.text, ref.col) for ref in refs] == [("R3", 1), ("A1", 11), ("R3", 21)]


# ------------------------------------------------------------- check_references
def test_check_references_unknown_and_retired() -> None:
    status = {"R1": "ok", "R2": "unknown", "R3": "retired"}

    def resolver(text: str) -> str:
        return status[text]

    problems = scan.check_references(scan.find_references("[R1] [R2] [R3]"), resolver)
    assert problems == [
        scan.Problem("unknown", "R2", 1, 6),
        scan.Problem("retired", "R3", 1, 11),
    ]


def test_ok_references_produce_no_problem() -> None:
    assert scan.check_references(scan.find_references("[R1] [A2]"), lambda text: "ok") == []


def test_scan_text_combines_find_and_check() -> None:
    problems = scan.scan_text("[R1] and [R2]", lambda text: "unknown" if text == "R2" else "ok")
    assert problems == [scan.Problem("unknown", "R2", 1, 10)]


def test_resolver_exception_propagates() -> None:
    def boom(_text: str) -> str:
        raise KeyError("resolver exploded")

    with pytest.raises(KeyError, match="resolver exploded"):
        scan.scan_text("[R1]", boom)


# ---------------------------------------------------------------- formatting
def test_format_problem_unknown_is_exact() -> None:
    problem = scan.Problem("unknown", "R99", 4, 9)
    assert scan.format_problem("docs/PRD.md", problem) == (
        "docs/PRD.md:4  [R99]  unknown reference"
    )


def test_format_problem_retired_is_exact() -> None:
    problem = scan.Problem("retired", "S2", 12, 3)
    assert scan.format_problem("PRD.md", problem) == "PRD.md:12  [S2]  retired reference"


def test_a18_prd_mention_of_r99_is_a_broken_reference() -> None:
    prd = "# PRD\n\nGoals: see [R1], [R2] and [R99].\n"
    known = {"R1": "ok", "R2": "ok"}

    def resolver(text: str) -> str:
        return known.get(text, "unknown")

    problems = scan.scan_text(prd, resolver)
    assert [(problem.kind, problem.ref, problem.line) for problem in problems] == [
        ("unknown", "R99", 3)
    ]
    assert scan.format_problem("docs/PRD.md", problems[0]) == (
        "docs/PRD.md:3  [R99]  unknown reference"
    )


# -------------------------------------------------------------------- anchors
def test_anchor_ids_explicit_and_slug_forms() -> None:
    markdown = (
        "# PRD: hermes-product\n"
        "\n"
        "## 4. Goals {#goals}\n"
        "\n"
        "## Status rules and lint\n"
    )
    ids = scan.anchor_ids(markdown)
    assert "goals" in ids
    assert "4-goals" in ids          # the heading still has a slug
    assert "prd-hermes-product" in ids
    assert "status-rules-and-lint" in ids


def test_anchor_ids_removes_punctuation_like_github() -> None:
    ids = scan.anchor_ids("## Goals: v0.1! (draft)\n")
    assert "goals-v01-draft" in ids


def test_anchor_ids_ignores_headings_inside_fences() -> None:
    markdown = "```\n## Fake {#nope}\n## fake-heading\n```\n## Real\n"
    ids = scan.anchor_ids(markdown)
    assert "nope" not in ids
    assert "fake-heading" not in ids
    assert "real" in ids


def test_anchor_ids_empty_markdown() -> None:
    assert scan.anchor_ids("") == set()


def test_prd_anchor_ok_cases() -> None:
    markdown = "## Goals {#goals}\n## Status rules and lint\n"
    assert scan.prd_anchor_ok("PRD.md#goals", markdown) is True
    assert scan.prd_anchor_ok("PRD.md#status-rules-and-lint", markdown) is True
    assert scan.prd_anchor_ok("PRD.md#missing", markdown) is False
    assert scan.prd_anchor_ok("PRD.md", markdown) is True
    assert scan.prd_anchor_ok("PRD.md#", markdown) is False
    assert scan.prd_anchor_ok("", markdown) is False


def test_prd_anchor_ok_does_not_see_fenced_headings() -> None:
    markdown = "```\n## Goals {#goals}\n```\n"
    assert scan.prd_anchor_ok("PRD.md#goals", markdown) is False
