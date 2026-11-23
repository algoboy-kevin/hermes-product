"""Bounded document reading (card HP-4), pure and through the CLI.

Pure half: ``docs_text.headings`` (fenced code is never a heading, levels,
section end at the same or a higher level, nested subsections included,
duplicate headings, 1-6 ``#``), ``docs_text.section`` (case-insensitive,
whitespace-collapsed, first match) and ``docs_text.bounded`` (exact boundary,
never over the limit, multibyte safe).

CLI half: ``doc read`` prints the whole body or one section, always bounded, with
ONE trailer only when it cut, and refuses a URL body_ref, a missing file, a
document without a body, an unknown section (listing the headings) and an
out-of-range --max-chars. A17 is the ~50 KB / 4000 character case.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import docs_text
import store
from _helpers import add_doc, cli, home, make_project

PROJECT = "shop"

SIMPLE = (
    "# Title\n"
    "intro\n"
    "## Section A\n"
    "text a\n"
    "### Sub\n"
    "text sub\n"
    "## Section B\n"
    "tail\n"
)


# -------------------------------------------------------------------- headings
def test_headings_levels_and_section_ends() -> None:
    assert docs_text.headings(SIMPLE) == [
        (1, "Title", 1, 8),
        (2, "Section A", 3, 6),
        (3, "Sub", 5, 6),
        (2, "Section B", 7, 8),
    ]


def test_a_fenced_code_block_hides_hash_lines() -> None:
    text = (
        "# Title\n"
        "text\n"
        "```sh\n"
        "# not a heading\n"
        "```\n"
        "## Next\n"
        "body\n"
    )
    assert docs_text.headings(text) == [
        (1, "Title", 1, 7),
        (2, "Next", 6, 7),
    ]


def test_a_tilde_fence_hides_hash_lines_too() -> None:
    text = "~~~\n# hidden\n~~~\n## Real\n"
    assert docs_text.headings(text) == [
        (2, "Real", 4, 4),
    ]


def test_a_headings_hash_run_must_be_one_to_six() -> None:
    text = "####### seven\n#Six no space\n## Six\n"
    assert docs_text.headings(text) == [(2, "Six", 3, 3)]


def test_headings_are_empty_without_a_heading() -> None:
    assert docs_text.headings("just prose\nand more\n") == []


# --------------------------------------------------------------------- section
def test_section_ends_at_a_same_level_heading() -> None:
    found = docs_text.section(SIMPLE, "Section A")
    assert found is not None
    assert "### Sub" in found
    assert "text sub" in found
    assert "Section B" not in found


def test_section_includes_a_nested_subsection() -> None:
    found = docs_text.section(SIMPLE, "Sub")
    assert found == "### Sub\ntext sub\n"


def test_section_match_is_case_insensitive_and_collapses_spaces() -> None:
    text = "##  Retry   Policy  \nbody\n"
    assert docs_text.section(text, "retry policy") == "##  Retry   Policy  \nbody\n"


def test_section_first_match_wins() -> None:
    text = "## Retries\nfirst\n## Retries\nsecond\n"
    assert docs_text.section(text, "Retries") == "## Retries\nfirst\n"


def test_section_returns_none_when_missing() -> None:
    assert docs_text.section(SIMPLE, "Missing") is None


# --------------------------------------------------------------------- bounded
def test_bounded_at_the_exact_length_is_not_truncated() -> None:
    assert docs_text.bounded("abcdef", 6) == ("abcdef", 6, False)


def test_bounded_one_over_the_length_is_truncated() -> None:
    assert docs_text.bounded("abcdef", 5) == ("abcde", 6, True)


def test_bounded_never_exceeds_the_limit() -> None:
    piece, total, truncated = docs_text.bounded("x" * 50, 7)
    assert len(piece) == 7
    assert total == 50
    assert truncated is True


def test_bounded_cuts_at_a_character_boundary_with_multibyte_text() -> None:
    text = "héllo→世界"
    piece, total, truncated = docs_text.bounded(text, 3)
    assert piece == "hél"
    assert total == len(text) == 8
    assert truncated is True


def test_bounded_limit_of_one() -> None:
    assert docs_text.bounded("abc", 1) == ("a", 3, True)


# ------------------------------------------------------------------ doc read CLI
def _body_file(home: Path, doc_id: int = 1) -> Path:
    return store.docs_dir(PROJECT) / f"DOC{doc_id}.md"


def test_doc_read_prints_the_whole_body(home: Path, capsys) -> None:
    make_project()
    add_doc(body="line one\nline two\n")
    rc, out, err = cli(capsys, "doc", "read", "DOC1")
    assert rc == 0, err
    assert out == "line one\nline two\n"


def test_doc_read_truncates_with_one_trailer(home: Path, capsys) -> None:
    make_project()
    add_doc(body="0123456789abcdefghij")
    rc, out, err = cli(capsys, "doc", "read", "DOC1", "--max-chars", "10")
    assert rc == 0, err
    assert out.splitlines()[0] == "0123456789"
    assert out.splitlines()[-1] == "[truncated: showed 10 of 20 chars]"
    assert out.count("[truncated") == 1


def test_doc_read_at_exactly_the_limit_has_no_trailer(home: Path, capsys) -> None:
    make_project()
    add_doc(body="0123456789")
    rc, out, err = cli(capsys, "doc", "read", "DOC1", "--max-chars", "10")
    assert rc == 0, err
    assert out == "0123456789"
    assert "[truncated" not in out


def test_doc_read_one_section(home: Path, capsys) -> None:
    make_project()
    add_doc(body="# Payments\nintro\n## Retries\nretry text\n## Webhooks\nhook text\n")
    rc, out, err = cli(capsys, "doc", "read", "DOC1", "--section", "Retries")
    assert rc == 0, err
    assert out == "## Retries\nretry text\n"


def test_a_fifty_kilobyte_section_is_cut_at_four_thousand_chars_A17(home: Path, capsys) -> None:
    make_project()
    body = ("# Payments\n\nIntro.\n\n## Retries\n\n"
            + ("A retry line with enough detail to matter.\n" * 1300)
            + "\n## Webhooks\n\n" + ("Hook line.\n" * 100))
    assert len(body.encode("utf-8")) > 50_000
    add_doc(body=body)

    rc, out, err = cli(capsys, "doc", "read", "DOC1", "--section", "Retries",
                      "--max-chars", "4000")
    assert rc == 0, err
    before_trailer = out.split("[truncated:")[0].rstrip("\n")
    assert len(before_trailer) <= 4000
    assert before_trailer.startswith("## Retries")
    assert out.splitlines()[-1].startswith("[truncated: showed 4000 of ")


def test_an_unknown_section_lists_the_headings(home: Path, capsys) -> None:
    make_project()
    add_doc(body="# Payments\n## Retries\n## Webhooks\n")
    rc, out, err = cli(capsys, "doc", "read", "DOC1", "--section", "Missing")
    assert rc == 1
    assert out == ""
    assert "no section 'Missing'" in err
    assert "Payments" in err and "Retries" in err and "Webhooks" in err


def test_an_unknown_section_lists_at_most_twenty_headings(home: Path, capsys) -> None:
    make_project()
    body = "".join(f"## Heading {index}\n" for index in range(25))
    add_doc(body=body)
    rc, _out, err = cli(capsys, "doc", "read", "DOC1", "--section", "Nope")
    assert rc == 1
    assert err.count("Heading ") == 20


def test_max_chars_must_be_in_range(home: Path, capsys) -> None:
    make_project()
    add_doc(body="text")
    for value in ("0", "100001", "-3"):
        rc, _out, err = cli(capsys, "doc", "read", "DOC1", "--max-chars", value)
        assert rc == 1
        assert "--max-chars must be 1..100000" in err


def test_a_url_body_ref_is_refused(home: Path, capsys) -> None:
    make_project()
    add_doc(body_ref="https://example.com/design.md")
    rc, _out, err = cli(capsys, "doc", "read", "DOC1")
    assert rc == 1
    assert "body_ref is a URL; fetch it yourself" in err


def test_a_local_body_ref_is_read_at_call_time(home: Path, capsys, tmp_path: Path) -> None:
    make_project()
    source = tmp_path / "design.md"
    source.write_text("# Design\nfirst version\n", encoding="utf-8")
    add_doc(body_ref=str(source))

    rc, out, err = cli(capsys, "doc", "read", "DOC1")
    assert rc == 0, err
    assert out == "# Design\nfirst version\n"

    source.write_text("# Design\nsecond version\n", encoding="utf-8")
    rc, out, err = cli(capsys, "doc", "read", "DOC1")
    assert rc == 0, err
    assert "second version" in out


def test_a_missing_local_body_ref_names_the_file(home: Path, capsys, tmp_path: Path) -> None:
    make_project()
    missing = tmp_path / "gone.md"
    add_doc(body_ref=str(missing))
    rc, _out, err = cli(capsys, "doc", "read", "DOC1")
    assert rc == 1
    assert "gone.md" in err
    assert "does not exist" in err


def test_a_document_without_a_body_is_refused(home: Path, capsys) -> None:
    make_project()
    add_doc()
    rc, _out, err = cli(capsys, "doc", "read", "DOC1")
    assert rc == 1
    assert "DOC1 has no body" in err


def test_a_missing_body_file_names_it(home: Path, capsys) -> None:
    make_project()
    add_doc(body="temporary")
    _body_file(home).unlink()
    rc, _out, err = cli(capsys, "doc", "read", "DOC1")
    assert rc == 1
    assert "DOC1 body file is missing" in err
    assert "DOC1.md" in err


def test_doc_read_refuses_a_non_document(home: Path, capsys) -> None:
    make_project()
    store.create_entity(PROJECT, "feature", {"title": "Stripe integration", "layer": "core"})
    rc, _out, err = cli(capsys, "doc", "read", "F1")
    assert rc == 1
    assert "not a DOC#" in err


def test_doc_read_uses_the_project_option(home: Path, capsys) -> None:
    make_project("shop")
    make_project("other")
    add_doc(project="other", body="other body\n")
    rc, out, err = cli(capsys, "doc", "read", "DOC1", "--project", "other")
    assert rc == 0, err
    assert out == "other body\n"
    rc, _out, err = cli(capsys, "doc", "read", "DOC1", "--project", "shop")
    assert rc == 1
    assert "DOC1" in err
