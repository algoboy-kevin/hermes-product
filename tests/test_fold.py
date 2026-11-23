"""``fold-docs``: spec DOC text moves onto the S# it defines (schema v2, DOC5 section 7).

Pinned here: the plan writes nothing; one DOC folds verbatim; several fold into
`## <doc title>` sections with headings pushed two levels down outside code fences;
what is NOT foldable is left alone with a reason (context DOCs cited by several
entries, other kinds, superseded, body_ref-only, a spec that already has a body);
apply is one transaction (S# body, history row, DOC row + links + body file gone,
ids never reused); and a second apply has nothing to do.
"""
from __future__ import annotations

from pathlib import Path

import pytest

import fold
import model
import store
from _helpers import (  # `home` is a pytest fixture imported for discovery
    add_doc, add_feature, add_requirement, add_spec, cli, history_rows, home, make_project,
)


@pytest.fixture()
def shop(home: Path) -> str:
    make_project()
    return "shop"


def _link(capsys, a: str, b: str) -> None:
    rc, _out, err = cli(capsys, "link", a, b)
    assert rc == 0, err


def _spec_doc(shop, capsys, spec: str, title: str, text: str, **fields) -> int:
    doc_id = add_doc(shop, title=title, kind="spec", body=text, **fields)
    _link(capsys, f"DOC{doc_id}", spec)
    return doc_id


def _body(shop, ref: str):
    return store.get_entity(shop, ref, with_body=True)["row"]["body"]


def _snapshot(shop) -> tuple:
    con = store.connect()
    try:
        tables = ("spec", "doc", "doc_link", "history", "id_counter")
        rows = tuple(tuple(tuple(r) for r in con.execute(f"SELECT * FROM {t} ORDER BY 1,2,3"))
                     for t in tables)
    finally:
        con.close()
    files = tuple(sorted(p.name for p in store.docs_dir(shop).glob("*")))
    return rows, files


# ------------------------------------------------------------------ pure composition
def test_demote_headings_skips_fences_and_clamps() -> None:
    text = "# A\nbody\n```sh\n# comment\n```\n## B\n~~~\n# tilde\n~~~\n###### Six\n#hash\n"
    assert fold.demote_headings(text, 2) == (
        "### A\nbody\n```sh\n# comment\n```\n#### B\n~~~\n# tilde\n~~~\n###### Six\n#hash\n")


def test_demote_keeps_line_endings() -> None:
    assert fold.demote_headings("# A\r\ntext\r\n", 2) == "### A\r\ntext\r\n"


def test_compose_one_doc_is_verbatim_and_many_are_sections() -> None:
    one = [{"id": 1, "title": "Only", "text": "# Only\nx"}]
    assert fold.compose_body(one) == "# Only\nx"
    two = [{"id": 1, "title": "First", "text": "# First brief\ntext\n"},
           {"id": 2, "title": "Second", "text": "# Second brief\n```\n# keep\n```"}]
    assert fold.compose_body(two) == (
        "## First\n\n### First brief\ntext\n\n"
        "## Second\n\n### Second brief\n```\n# keep\n```\n")


# ----------------------------------------------------------------------------- plan
def test_plan_writes_nothing(shop, capsys) -> None:
    add_spec(shop)
    _spec_doc(shop, capsys, "S1", "Contract", "# Contract\nx\n")
    before = _snapshot(shop)
    rc, out, err = cli(capsys, "fold-docs")
    assert rc == 0, err
    assert "S1  <- DOC1  " in out and "verbatim" in out
    assert "(dry run): 1 documents into 1 spec bodies, 0 skipped; apply with --apply" in out
    assert _snapshot(shop) == before
    assert _body(shop, "S1") is None


def test_one_doc_folds_verbatim_and_everything_old_is_gone(shop, capsys) -> None:
    add_spec(shop)
    text = "# Contract\n\nStatus: draft-looking line.\n```\n# code\n```\ntrailing  \nno newline"
    _spec_doc(shop, capsys, "S1", "Contract", text)
    path = store.docs_dir(shop) / "DOC1.md"
    assert path.is_file()

    rc, out, err = cli(capsys, "fold-docs", "--apply")
    assert rc == 0, err
    assert "folded 1 documents into 1 spec bodies in shop (0 skipped)" in out
    assert _body(shop, "S1") == text
    assert not path.exists()
    assert store.list_entities(shop, "doc") == []
    assert history_rows(shop, "spec", 1)[-1]["change"] == "body folded from DOC1"
    assert store.get_entity(shop, "S1")["docs"] == []          # no stale link
    # ids are never reused: the next DOC is DOC2
    assert add_doc(shop, title="Next", kind="design") == 2


def test_several_docs_become_sections_readable_by_heading(shop, capsys) -> None:
    add_spec(shop)
    _spec_doc(shop, capsys, "S1", "Shutdown", "# S18 shutdown\n\n## Steps\n1. cancel\n")
    _spec_doc(shop, capsys, "S1", "Sizing", "# S18 sizing\n\nmin order is USD12\n")
    rc, out, err = cli(capsys, "fold-docs", "--apply")
    assert rc == 0, err
    assert "S1 spec bodies" not in out and "folded 2 documents into 1 spec bodies" in out
    assert _body(shop, "S1") == (
        "## Shutdown\n\n### S18 shutdown\n\n#### Steps\n1. cancel\n\n"
        "## Sizing\n\n### S18 sizing\n\nmin order is USD12\n")
    assert history_rows(shop, "spec", 1)[-1]["change"] == "body folded from DOC1, DOC2"
    rc, out, err = cli(capsys, "show", "S1", "--body", "--section", "Sizing")
    assert rc == 0, err
    tail = out.partition("--- body ---\n")[2]
    assert "min order is USD12" in tail and "cancel" not in tail


def test_apply_is_idempotent(shop, capsys) -> None:
    add_spec(shop)
    _spec_doc(shop, capsys, "S1", "Contract", "# Contract\nx\n")
    cli(capsys, "fold-docs", "--apply")
    after = _snapshot(shop)
    rc, out, err = cli(capsys, "fold-docs", "--apply")
    assert rc == 0, err
    assert "nothing to fold" in out
    assert _snapshot(shop) == after


# ------------------------------------------------------------------- what is left alone
def test_context_and_other_cases_stay_and_say_why(shop, capsys, tmp_path) -> None:
    add_spec(shop, title="Spec one")                         # S1
    add_spec(shop, title="Spec two")                         # S2
    add_requirement(shop)                                    # R1
    add_feature(shop)                                        # F1
    ok = _spec_doc(shop, capsys, "S1", "Fine", "# Fine\nx\n")                 # DOC1 folds
    shared = _spec_doc(shop, capsys, "S1", "Shared", "# Shared\nx\n")         # DOC2
    _link(capsys, f"DOC{shared}", "S2")                      # linked to two specs
    to_req = _spec_doc(shop, capsys, "S2", "Req too", "# R\nx\n")             # DOC3
    _link(capsys, f"DOC{to_req}", "R1")
    design = add_doc(shop, title="A UX review", kind="design", body="# UX\n")  # DOC4 (design)
    _link(capsys, f"DOC{design}", "S1")
    refonly = add_doc(shop, title="External", kind="spec", body_ref=str(tmp_path / "x.md"))
    _link(capsys, f"DOC{refonly}", "S2")                                      # DOC5
    unlinked = add_doc(shop, title="Floating", kind="spec", body="# F\n")     # DOC6

    rc, out, err = cli(capsys, "fold-docs", "--apply")
    assert rc == 0, err
    assert "folded 1 documents into 1 spec bodies" in out
    assert "skip DOC2  linked to 2 entries" in out
    assert "skip DOC3  linked to 2 entries" in out
    assert "skip DOC5  no stored body (body_ref only)" in out
    assert "skip DOC6  linked to nothing" in out
    assert _body(shop, "S1") == "# Fine\nx\n"
    assert _body(shop, "S2") is None
    left = {item["id"] for item in store.list_entities(shop, "doc")}
    assert left == {shared, to_req, design, refonly, unlinked} and ok not in left


def test_a_spec_with_a_body_is_never_overwritten(shop, capsys) -> None:
    add_spec(shop, body="# Hand-written\n")
    _spec_doc(shop, capsys, "S1", "Old copy", "# Old\n")
    rc, out, err = cli(capsys, "fold-docs", "--apply")
    assert rc == 0, err
    assert "skip DOC1  S1 already has a body (never overwritten)" in out
    assert _body(shop, "S1") == "# Hand-written\n"
    assert [d["id"] for d in store.list_entities(shop, "doc")] == [1]


def test_a_doc_named_in_superseded_by_is_left(shop, capsys) -> None:
    add_spec(shop)
    old = _spec_doc(shop, capsys, "S1", "Old", "# Old\n")
    new = add_doc(shop, title="Newer design", kind="design", body="# N\n")
    store.update_entity(shop, f"DOC{old}", {"status": "superseded", "superseded_by": f"DOC{new}"})
    keep = add_doc(shop, title="Spec text", kind="spec", body="# Keep\n")
    _link(capsys, f"DOC{keep}", "S1")
    store.update_entity(shop, f"DOC{new}", {"title": "Newer design (v2)"})
    # a spec DOC that another DOC names as its replacement must survive the fold
    store.update_entity(shop, f"DOC{old}", {"superseded_by": f"DOC{keep}"})
    rc, out, err = cli(capsys, "fold-docs", "--apply")
    assert rc == 0, err
    assert f"skip DOC{keep}  another DOC names it in superseded_by" in out
    assert f"skip DOC{old}  status is superseded" in out
    assert _body(shop, "S1") is None


def test_a_failure_mid_fold_changes_nothing(shop, capsys, monkeypatch) -> None:
    add_spec(shop)
    add_spec(shop, title="Spec two")
    _spec_doc(shop, capsys, "S1", "Brief A", "# A\n")
    _spec_doc(shop, capsys, "S2", "Brief B", "# B\n")
    before = _snapshot(shop)
    real = store.doc_body_path
    calls = {"n": 0}

    def flaky(project, row):
        # plan() calls it once per DOC, apply() once per DOC again: fail on the 2nd apply call
        calls["n"] += 1
        if calls["n"] == 4:
            raise OSError("disk went away")
        return real(project, row)

    monkeypatch.setattr(store, "doc_body_path", flaky)
    with pytest.raises(OSError):
        fold.apply(shop)
    monkeypatch.setattr(store, "doc_body_path", real)
    assert _snapshot(shop) == before                      # rolled back, files still there
