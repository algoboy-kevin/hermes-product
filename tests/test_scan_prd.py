"""scan-prd and broken_reference tests (card HP-8).

A18 has two halves: ``scan-prd`` reports the broken reference, and ``lint``
reports it as a broken_reference Error. The resolver rules (ok/unknown/retired,
never raising on a malformed ref) and the document selection (a current DOC is
scanned; a URL or a missing body is skipped silently) are covered too.

Nothing here reads docs/ and nothing touches the real ~/.hermes.
"""

from __future__ import annotations

import lint
import store
from _helpers import (  # `home`/`con` are fixtures imported for discovery
    add_doc, add_feature, add_requirement, add_criterion, add_spec, cli, con, home,
    make_project, raw_update,
)


def prd(body: str | None = None, **fields) -> int:
    """The project's one `prd` DOC# (body text stored with the project)."""
    return add_doc(kind="prd", body=body, **fields)


# ----------------------------------------------------------------------- A18
def test_a18_scan_prd_reports_a_reference_to_a_missing_requirement(home, capsys) -> None:
    make_project()
    add_requirement()                                      # R1 exists
    prd("# Goals\n\nPay by card [R1].\n\nLater: [R99].\n")
    rc, out, err = cli(capsys, "scan-prd")
    assert rc == 1, err
    assert out == (
        "scan-prd (shop): 1 documents, 2 references, 1 problems\n"
        "DOC1:5  [R99]  unknown reference\n"
    )


def test_a18_lint_reports_a_broken_reference_as_an_error(home, capsys) -> None:
    make_project()
    prd("Looks fine [R99].\n")
    rc, out, err = cli(capsys, "lint")
    assert rc == 1, err
    assert "error broken_reference DOC1  line 1  [R99]  unknown reference" in out
    assert out.splitlines()[-1] == "not yet checked: uncovered_path"


def test_lint_reports_no_broken_reference_when_every_reference_resolves(home, capsys) -> None:
    make_project()
    add_requirement()
    prd("[R1]\n")
    rc, out, err = cli(capsys, "lint")
    assert rc == 0, err
    assert "broken_reference" not in out


# ------------------------------------------------------------------ resolver
def test_resolver_answers_ok_unknown_and_never_raises(home, con) -> None:
    make_project()
    add_requirement()                                      # R1
    add_feature(slug="stripe")                             # F1
    resolve = lint.make_resolver(con, "shop")
    assert resolve("R1") == "ok"
    assert resolve("F:stripe") == "ok"
    assert resolve("R2") == "unknown"
    assert resolve("F:paypal") == "unknown"
    assert resolve("R0") == "unknown"
    assert resolve("bogus") == "unknown"
    assert resolve("r1") == "unknown"


def test_resolver_answers_retired_for_dropped_and_superseded_rows(home, con) -> None:
    make_project()
    add_requirement()                                      # R1
    raw_update(con, "shop", "requirement", 1, status="dropped")
    add_feature()                                          # F1
    raw_update(con, "shop", "feature", 1, status="cut")
    add_spec()                                             # S1, superseded by S2
    add_spec(summary="The replacement spec.")              # S2
    raw_update(con, "shop", "spec", 1, status="superseded", superseded_by=2)
    add_doc(kind="design", body="x")                       # DOC1, superseded by DOC2
    add_doc(kind="design", summary="The replacement doc.", body="y")   # DOC2
    raw_update(con, "shop", "doc", 1, status="superseded", superseded_by=2)
    resolve = lint.make_resolver(con, "shop")
    assert resolve("R1") == "retired"
    assert resolve("F1") == "retired"
    assert resolve("S1") == "retired"
    assert resolve("DOC1") == "retired"
    assert resolve("S2") == "ok"
    assert resolve("DOC2") == "ok"


def test_scan_prd_reports_a_retired_reference(home, con, capsys) -> None:
    make_project()
    add_requirement()
    store.update_entity("shop", "R1", {"status": "dropped",
                                      "status_reason": "cut from scope"}, con=con)
    prd("[R1]\n")
    rc, out, err = cli(capsys, "scan-prd")
    assert rc == 1, err
    assert out == ("scan-prd (shop): 1 documents, 1 references, 1 problems\n"
                   "DOC1:1  [R1]  retired reference\n")


def test_slug_reference_ok_and_unknown(home, capsys) -> None:
    make_project()
    add_feature(slug="stripe")
    prd("[F:stripe] ships with [F:paypal].\n")
    rc, out, err = cli(capsys, "scan-prd")
    assert rc == 1, err
    assert out.splitlines()[0] == "scan-prd (shop): 1 documents, 2 references, 1 problems"
    assert out.splitlines()[1:] == ["DOC1:1  [F:paypal]  unknown reference"]


# -------------------------------------------------------------- what is skipped
def test_references_in_code_are_not_reported(home, capsys) -> None:
    make_project()
    prd("```\n[R99]\n```\n\nInline `[R98]` and escaped \\[R97].\n")
    rc, out, err = cli(capsys, "scan-prd")
    assert rc == 0, err
    assert out == "scan-prd (shop): 1 documents, 0 references, 0 problems\n"


def test_a_url_body_is_skipped(home, capsys) -> None:
    make_project()
    add_doc(kind="prd", body_ref="https://example.com/prd.md")
    rc, out, err = cli(capsys, "scan-prd")
    assert rc == 0, err
    assert out == "scan-prd (shop): 0 documents, 0 references, 0 problems\n"


def test_a_missing_body_file_is_skipped(home, capsys) -> None:
    make_project()
    prd("[R99]\n")
    body = store.doc_body_path("shop", store.get_entity("shop", "DOC1")["row"])
    body.unlink()
    rc, out, err = cli(capsys, "scan-prd")
    assert rc == 0, err
    assert out == "scan-prd (shop): 0 documents, 0 references, 0 problems\n"


def test_a_superseded_design_document_is_ignored(home, capsys) -> None:
    make_project()
    add_doc(kind="design", body="stale [R99]\n")                       # DOC1
    add_doc(kind="design", summary="The replacement.", body="fine\n")  # DOC2
    store.update_entity("shop", "DOC1", {"status": "superseded", "superseded_by": 2})
    rc, out, err = cli(capsys, "scan-prd")
    assert rc == 0, err
    assert out == "scan-prd (shop): 1 documents, 0 references, 0 problems\n"


def test_a_superseded_prd_is_ignored(home, capsys) -> None:
    make_project()
    add_doc(kind="prd", body="stale [R99]\n")                          # DOC1
    add_doc(kind="design", summary="The replacement.", body="fine\n")  # DOC2
    store.update_entity("shop", "DOC1", {"status": "superseded", "superseded_by": 2})
    rc, out, err = cli(capsys, "scan-prd")
    assert rc == 0, err
    assert out == "scan-prd (shop): 1 documents, 0 references, 0 problems\n"


# ------------------------------------------------------------- output and order
def test_problems_order_by_document_then_line_then_column(home, capsys) -> None:
    make_project()
    prd("[R98] [R97]\n")                                               # DOC1
    add_doc(kind="design", summary="Second document.", body="x [R96]\n")   # DOC2
    rc, out, err = cli(capsys, "scan-prd")
    assert rc == 1, err
    assert out.splitlines() == [
        "scan-prd (shop): 2 documents, 3 references, 3 problems",
        "DOC1:1  [R98]  unknown reference",
        "DOC1:1  [R97]  unknown reference",
        "DOC2:1  [R96]  unknown reference",
    ]


def test_empty_project_prints_zero_counts(home, capsys) -> None:
    make_project()
    rc, out, err = cli(capsys, "scan-prd")
    assert rc == 0, err
    assert out == "scan-prd (shop): 0 documents, 0 references, 0 problems\n"


def test_exit_is_zero_when_every_reference_resolves(home, capsys) -> None:
    make_project()
    add_requirement()
    add_criterion()                                        # A1 under R1
    prd("[R1] [A1]\n")
    rc, out, err = cli(capsys, "scan-prd")
    assert rc == 0, err
    assert out == "scan-prd (shop): 1 documents, 2 references, 0 problems\n"


def test_project_option_scans_only_that_project(home, capsys) -> None:
    make_project("shop", "Shop")
    make_project("other", "Other project")
    add_doc("shop", kind="prd", body="[R99]\n")
    add_doc("other", kind="prd", body="[R98]\n")
    rc, out, err = cli(capsys, "scan-prd", "--project", "other")
    assert rc == 1, err
    assert out == ("scan-prd (other): 1 documents, 1 references, 1 problems\n"
                   "DOC1:1  [R98]  unknown reference\n")


# -------------------------------------------------------------------- doc_text
def test_doc_text_reads_a_local_body_ref_relative_to_the_project_root(home, tmp_path) -> None:
    note = tmp_path / "note.md"
    note.write_text("# Note\n", encoding="utf-8")
    store.create_project("shop", "Shop", root=tmp_path)
    add_doc(kind="prd", body_ref="note.md")
    row = store.get_entity("shop", "DOC1")["row"]
    assert lint.doc_text("shop", row) == "# Note\n"


def test_doc_text_returns_none_for_a_url_and_for_a_missing_file(home, tmp_path) -> None:
    store.create_project("shop", "Shop", root=tmp_path)
    add_doc(kind="prd", body_ref="https://example.com/prd.md")
    add_doc(kind="design", summary="Gone.", body_ref="gone.md")
    url_row = store.get_entity("shop", "DOC1")["row"]
    gone_row = store.get_entity("shop", "DOC2")["row"]
    assert lint.doc_text("shop", url_row) is None
    assert lint.doc_text("shop", gone_row) is None
