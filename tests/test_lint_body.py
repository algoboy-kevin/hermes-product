"""Lint over text: thin_summary, status_in_body, and references inside entity bodies.

Pinned here: what counts as a thin one-line field (title restated, import placeholder,
bare path, too short) and what does not; retired entries stay out; `Status:` lines are
found outside code fences only and in R#/F#/S# bodies and DOCs alike; and a broken
`[R99]` inside an S# body is a broken_reference (so folding spec DOCs into bodies did
not shrink the reference scan), with `scan-prd` counting those bodies too.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

import docs_text
import lint
import store
from _helpers import add_doc, add_feature, add_requirement, add_spec, cli, home, make_project  # noqa: F401


# ------------------------------------------------------------------ pure: thin_reason
@pytest.mark.parametrize("title,text,expected", [
    ("Market tracker", "Market tracker", "restates the title"),
    ("Market tracker", "market  TRACKER.", "restates the title"),
    ("Core primitives", "Imported spec document docs/specs/S1-core.md (legacy)",
     "is an import placeholder"),
    ("Core primitives", "Defines things (legacy)", "is an import placeholder"),
    ("Core primitives", "docs/specs/S1-core-primitives.md", "is only a file path"),
    ("Core primitives", "S1-core.md", "is only a file path"),
    ("Core primitives", "Decimal and price rules", None),               # 4 words is enough
    ("Core primitives", "Money type", "is 2 word(s); say what it decides"),
    ("Core primitives", "", None),
    ("Core primitives", None, None),
    ("Core primitives", "Decimal arithmetic, tick rounding and the order-book structure.", None),
])
def test_thin_reason(title, text, expected) -> None:
    got = lint.thin_reason(title, text)
    if expected is None:
        assert got is None
    else:
        assert got == expected


# --------------------------------------------------------------- pure: status lines
def test_status_lines_outside_fences_only() -> None:
    text = ("# T\n"
            "Status: DRAFT\n"
            "**Status:** done\n"
            "> status : x\n"
            "- Status: nested\n"
            "```\n"
            "Status: inside code\n"
            "```\n"
            "The status of the system is fine.\n"
            "Statuses: not a declaration\n"
            "  status:   indented\n")
    assert [number for number, _line in docs_text.status_lines(text)] == [2, 3, 4, 5, 11]


# ------------------------------------------------------------------ through the store
def _flags(capsys, project: str = "shop") -> list[tuple[str, str]]:
    rc, out, err = cli(capsys, "lint", "--json", "--project", project)
    assert rc in (0, 1), err
    return [(f["flag"], f["ref"]) for f in json.loads(out)["findings"]]


def test_thin_summary_on_s_doc_and_feature(home: Path, capsys) -> None:
    make_project()
    add_spec("shop", title="Tracker", summary="Imported spec document x.md (legacy)")       # S1
    add_spec("shop", title="Good spec", summary="Defines the per-second market-state rows.")  # S2
    add_doc("shop", title="Run log", summary="Run log")                                       # DOC1
    add_feature("shop", title="Rollup", description="Money")                                  # F1
    add_feature("shop", title="Fine", description="The ledger and its posting rules.")        # F2
    add_feature("shop", title="No text")                                                      # F3
    flags = _flags(capsys)
    thin = sorted(ref for flag, ref in flags if flag == "thin_summary")
    assert thin == ["DOC1", "F1", "S1"]


def test_a_retired_entry_is_not_linted_for_thin_text(home: Path, capsys) -> None:
    make_project()
    add_spec("shop", title="Old", summary="Old")
    add_spec("shop", title="New one", summary="Replaces the old contract entirely.")
    store.update_entity("shop", "S1", {"status": "superseded", "superseded_by": "S2"})
    assert [f for f in _flags(capsys) if f[0] == "thin_summary"] == []


def test_status_in_body_for_entity_bodies_and_docs(home: Path, capsys) -> None:
    make_project()
    add_spec("shop", summary="What the tracker decides in one line.",
             body="# Contract\nStatus: DRAFT\n```\nStatus: in code\n```\n")             # S1
    add_requirement("shop", body="plain context\n")                                     # R1
    add_feature("shop", body="**Status:** shipped\n")                                   # F1
    add_doc("shop", body="Status: current\n")                                           # DOC1
    flags = [f for f in _flags(capsys) if f[0] == "status_in_body"]
    assert sorted(ref for _flag, ref in flags) == ["DOC1", "F1", "S1"]
    rc, out, _err = cli(capsys, "lint", "--project", "shop")
    assert "status_in_body S1" in out and "line 2" in out


def test_retired_entity_body_is_not_linted(home: Path, capsys) -> None:
    make_project()
    add_spec("shop", title="Old", summary="Old contract that nobody uses.", body="Status: x\n")
    add_spec("shop", title="New", summary="The contract that replaces it.")
    store.update_entity("shop", "S1", {"status": "superseded", "superseded_by": "S2"})
    assert [f for f in _flags(capsys) if f[0] == "status_in_body"] == []


def test_broken_reference_inside_a_spec_body_is_an_error(home: Path, capsys) -> None:
    make_project()
    add_requirement("shop")
    add_spec("shop", summary="Defines the tracker rows live.",
             body="See [R1] and the missing [R99].\n")
    rc, out, _err = cli(capsys, "lint", "--json", "--project", "shop")
    payload = json.loads(out)
    broken = [f for f in payload["findings"] if f["flag"] == "broken_reference"]
    assert [f["ref"] for f in broken] == ["S1"]
    assert "R99" in broken[0]["message"]
    assert rc == 1 and payload["errors"] == 1


def test_scan_prd_counts_entity_bodies(home: Path, capsys) -> None:
    make_project()
    add_requirement("shop", body="Depends on [R1] and [F:nope].\n")
    rc, out, err = cli(capsys, "scan-prd", "--project", "shop")
    assert "R1" in out                                    # the problem line names the body's ref
    assert "F:nope" in out
