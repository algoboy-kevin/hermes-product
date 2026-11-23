"""HP-10: ``import-md`` turns the S6 markdown documents into store rows.

Three layers are pinned here:

* every mapper alone, fed TEXT (no sqlite): the R header line, the statement
  paragraph, the A table, the F table (Needs / Requirements, mermaid ignored),
  the S header + summary fallback, the DOC header (kind/status/links/note,
  ``PRD#risks`` is not an entry) and the PRD's id allocation above DOC2;
* the A25 cross-check, one problem per test: an id missing from index.md, an
  extra index id, a duplicate id, a dangling reference, a criteria-list
  mismatch, an R<->F and an R<->S disagreement, a status and a title mismatch;
  any problem means nothing is imported and exit 1;
* the end-to-end run through ``product_command``: the counts line, the rows
  read back with ``list``/``show``, a clean ``lint``, a PRD scan that sees the
  PRD body, a second run that is all skipped, ``--dry-run`` writing nothing,
  an unknown project, and a DOC body that round-trips byte-exact.

The docs tree every test parses is built under ``tmp_path``; the real ``docs/``
is never read.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import importmd
import store
from _helpers import cli, con, home  # noqa: F401  (fixtures imported for discovery)


# --------------------------------------------------------------- the docs tree
STANDARD = {
    "requirements.md": """\
# Requirements

Header line: `status | priority | phase | features | specs | criteria`.

## R1 One store

`draft | must | mvp | F1 | S1 | A1`

**Statement.** A user can create a project with a slug.
It is scoped to that project.

## R2 Create entities

`draft | should | - | F1,F2 | S1 | A2,A3`

**Statement.** A user can add entries.
""",
    "acceptance.md": """\
# Acceptance criteria

| ID | Req | Status | Check |
|---|---|---|---|
| A1 | R1 | not_run | Given nothing, when added, then it exists. |
| A2 | R2 | not_run | Given a payload, when added, then the ID prints. |
| A3 | R2 | not_run | Given a bad field, when added, then it fails. |
""",
    "features.md": """\
# Features

| ID | Title | Layer | Status | Needs | Requirements |
|---|---|---|---|---|---|
| F1 | Store | foundation | planned | - | R1,R2 |
| F2 | Extras | core | planned | F1 | R2 |

## Dependency graph

```mermaid
graph LR
  F1 --> F2
```
""",
    "specs/S1-store.md": """\
# S1 Store

`draft | requirements R1,R2 | features F1`

How rows are stored and scoped to one project.

## Layout

The store keeps one file.
""",
    "supporting/DOC1-notes.md": """\
# DOC1 Research notes

`kind: research | status: current | links: PRD#risks, R1, F1 | note: see the plan`

**Summary.** What we learned from the prototype.

## Body

Some body text.
""",
    "supporting/DOC2-design.md": """\
# DOC2 Design notes

`kind: design | status: current | links: R2, S1`

**Summary.** How the page is laid out.

Body line.
""",
    "PRD.md": """\
# PRD: shop

## 1. Goals

Keep [R1] simple.
""",
    "index.md": """\
# Index

- [PRD](PRD.md) | draft | shop requirements document

## Requirements

| ID | Status | Priority | Phase | Title |
|---|---|---|---|---|
| R1 | draft | must | mvp | One store |
| R2 | draft | should | - | Create entities |

## Acceptance criteria

A1 to A3, all `not_run`.

## Features

| ID | Status | Title |
|---|---|---|
| F1 | planned | Store |
| F2 | planned | Extras |

## Specs

| ID | Status | Title |
|---|---|---|
| S1 | draft | Store |

## Supporting documents

| ID | Kind | Status | Summary |
|---|---|---|---|
| DOC1 | research | current | [Research notes](supporting/DOC1-notes.md) |
| DOC2 | design | current | [Design notes](supporting/DOC2-design.md) |
""",
}


def edited(text: str, old: str, new: str) -> str:
    """``text`` with the first ``old`` replaced by ``new`` (the test's own edit)."""
    assert old in text, f"{old!r} is not in the fixture text"
    return text.replace(old, new, 1)


#: override keyword -> the file it replaces (a bad name is a loud KeyError)
OVERRIDE_FILES = {
    "index": "index.md",
    "requirements": "requirements.md",
    "acceptance": "acceptance.md",
    "features": "features.md",
    "prd": "PRD.md",
    "spec1": "specs/S1-store.md",
    "doc1": "supporting/DOC1-notes.md",
    "doc2": "supporting/DOC2-design.md",
}


def docs_tree(tmp_path: Path, **overrides: str) -> Path:
    """Write the standard (or overridden) docs tree under ``tmp_path/docs``."""
    files = dict(STANDARD)
    for name, text in overrides.items():
        files[OVERRIDE_FILES[name]] = text
    root = tmp_path / "docs"
    for name, text in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return root


@pytest.fixture()
def shop(home: Path) -> str:  # noqa: F811  (the `home` fixture sets HERMES_HOME)
    """An existing project the import can merge into."""
    store.create_project("shop", "Shop")
    return "shop"


def rows(kind: str, project: str = "shop") -> list[dict]:
    connection = store.connect()
    try:
        return [dict(row) for row in connection.execute(
            f"SELECT * FROM {store.ENTITY_TABLE[kind]} WHERE project = ? ORDER BY id",
            (project,))]
    finally:
        connection.close()


def doc_rows(project: str = "shop") -> list[dict]:
    return rows("doc", project)


# ------------------------------------------------------------------ the R mapper
def test_parse_requirements_header_with_dashes() -> None:
    text = """\
## R7 One thing

`draft | could | - | - | - | -`

**Statement.** The system shall do the thing.
"""
    [entry] = importmd.parse_requirements(text)
    assert entry.id == 7
    assert entry.title == "One thing"
    assert entry.status == "draft"
    assert entry.priority == "could"
    assert entry.phase is None
    assert entry.features == [] and entry.specs == [] and entry.criteria == []


def test_parse_requirements_statement_is_one_paragraph() -> None:
    text = """\
## R1 One store

`draft | must | mvp | F1 | S1 | A1`

**Statement.** A user can create a project with a slug.
It is scoped to that project.

A second paragraph is not the statement.
"""
    [entry] = importmd.parse_requirements(text)
    assert entry.statement == ("A user can create a project with a slug."
                              " It is scoped to that project.")


def test_parse_requirements_full_header_ids() -> None:
    text = """\
## R2 Create entities

`approved | should | v1.1 | F1,F2 | S1,S4 | A2,A3,A4`

**Statement.** A user can add entries.
"""
    [entry] = importmd.parse_requirements(text)
    assert entry.status == "approved"
    assert entry.phase == "v1.1"
    assert entry.features == [1, 2]
    assert entry.specs == [1, 4]
    assert entry.criteria == [2, 3, 4]


# ------------------------------------------------------------------ the A mapper
def test_parse_acceptance_rows() -> None:
    text = """\
# Acceptance criteria

| ID | Req | Status | Check |
|---|---|---|---|
| A1 | R1 | not_run | Given nothing, when added, then it exists. |
| A2 | R2 | passed | Given a payload, when added, then the ID prints. |
"""
    criteria = importmd.parse_acceptance(text)
    assert [(a.id, a.requirement_id, a.status) for a in criteria] == [
        (1, 1, "not_run"), (2, 2, "passed")]
    assert criteria[0].text == "Given nothing, when added, then it exists."


# ------------------------------------------------------------------ the F mapper
def test_parse_features_needs_dash_and_requirements() -> None:
    text = """\
| ID | Title | Layer | Status | Needs | Requirements |
|---|---|---|---|---|---|
| F1 | Store | foundation | planned | - | R1,R2 |
"""
    [feature] = importmd.parse_features(text)
    assert (feature.id, feature.title, feature.layer, feature.status) == (
        1, "Store", "foundation", "planned")
    assert feature.needs == []
    assert feature.requirements == [1, 2]


def test_parse_features_needs_ids() -> None:
    text = """\
| ID | Title | Layer | Status | Needs | Requirements |
|---|---|---|---|---|---|
| F2 | Extras | core | in_progress | F1,F3 | R2 |
"""
    [feature] = importmd.parse_features(text)
    assert feature.needs == [1, 3]
    assert feature.requirements == [2]
    assert feature.status == "in_progress"


def test_parse_features_ignores_the_mermaid_block() -> None:
    text = """\
| ID | Title | Layer | Status | Needs | Requirements |
|---|---|---|---|---|---|
| F1 | Store | foundation | planned | - | R1 |

```mermaid
graph LR
  F1 --> F2
```
"""
    features = importmd.parse_features(text)
    assert [f.id for f in features] == [1]


# ------------------------------------------------------------------ the S mapper
def test_parse_spec_header_and_summary() -> None:
    text = """\
# S1 Store

`draft | requirements R1,R2 | features F1`

How rows are stored and scoped to one project.

## Layout
"""
    spec = importmd.parse_spec(text)
    assert (spec.id, spec.title, spec.status) == (1, "Store", "draft")
    assert spec.requirements == [1, 2]
    assert spec.features == [1]
    assert spec.summary == "How rows are stored and scoped to one project."


def test_parse_spec_summary_falls_back_to_title() -> None:
    text = """\
# S5 External links

`draft | requirements R12 | features F7`

| Field | Type |
|---|---|
| entity_type | enum |

## Rules

- One rule.
"""
    spec = importmd.parse_spec(text)
    assert spec.requirements == [12]
    assert spec.features == [7]
    assert spec.summary == "External links"


# ---------------------------------------------------------------- the DOC mapper
DOC_TEXT = """\
# DOC1 Research notes

`kind: research | status: current | links: PRD#risks, R1, F1 | note: see the plan`

**Summary.** What we learned from the prototype.

## Body

Some body text.
"""


def test_parse_supporting_header_links_note_and_summary() -> None:
    doc = importmd.parse_supporting(DOC_TEXT)
    assert (doc.id, doc.title, doc.kind, doc.status) == (
        1, "Research notes", "research", "current")
    assert doc.links == [("requirement", 1), ("feature", 1)]
    assert doc.note == "see the plan"
    assert doc.summary == "What we learned from the prototype."


def test_parse_supporting_ignores_a_non_entry_token() -> None:
    doc = importmd.parse_supporting(DOC_TEXT)
    assert doc.ignored == ["PRD#risks"]


def test_parse_supporting_summary_falls_back_to_title() -> None:
    text = """\
# DOC2 Design notes

`kind: design | status: current | links: R2`

Body only, no Summary block.
"""
    doc = importmd.parse_supporting(text)
    assert doc.summary == "Design notes"


def test_parse_supporting_body_is_the_text_after_the_header() -> None:
    doc = importmd.parse_supporting(DOC_TEXT)
    expected = ("**Summary.** What we learned from the prototype.\n\n"
                "## Body\n\nSome body text.\n")
    assert doc.body == expected.encode("utf-8")


# ----------------------------------------------------------------- the PRD mapper
def test_prd_title_status_and_summary_from_the_index_line() -> None:
    prd = importmd.parse_prd(STANDARD["PRD.md"], "shop requirements document")
    assert prd.title == "shop"
    assert prd.kind == "prd"
    assert prd.status == "current"
    assert prd.summary == "shop requirements document"
    assert prd.body == STANDARD["PRD.md"].encode("utf-8")


def test_prd_summary_falls_back_to_the_title() -> None:
    prd = importmd.parse_prd(STANDARD["PRD.md"], "")
    assert prd.summary == "shop"


def test_prd_id_is_allocated_above_the_highest_doc(tmp_path: Path) -> None:
    parsed = importmd.build_documents(docs_tree(tmp_path))
    assert [(doc.id, doc.title) for doc in parsed.docs] == [
        (1, "Research notes"), (2, "Design notes"), (3, "shop")]
    assert parsed.prd.id == 3
    assert parsed.prd.kind == "prd"


# ---------------------------------------------------------------- the cross-check
def run_import(capsys, tree: Path, *extra: str) -> tuple[int, str, str]:
    return cli(capsys, "import-md", str(tree), "--project", "shop", *extra)


def test_problem_id_missing_from_index(tmp_path: Path, shop: str, capsys) -> None:
    tree = docs_tree(tmp_path, index=edited(
        STANDARD["index.md"], "| R1 | draft | must | mvp | One store |\n", ""))
    rc, out, err = run_import(capsys, tree)
    assert rc == 1
    assert "problem: requirements.md: R1 is not in index.md" in out
    assert rows("requirement") == []


def test_problem_extra_index_id(tmp_path: Path, shop: str, capsys) -> None:
    tree = docs_tree(tmp_path, index=edited(
        STANDARD["index.md"], "| R2 | draft | should | - | Create entities |",
        "| R2 | draft | should | - | Create entities |\n| R9 | draft | must | mvp | Ghost |"))
    rc, out, err = run_import(capsys, tree)
    assert rc == 1
    assert "problem: index.md: R9 is not in requirements.md" in out


def test_problem_duplicate_id(tmp_path: Path, shop: str, capsys) -> None:
    tree = docs_tree(tmp_path, requirements=STANDARD["requirements.md"] + """
## R1 One store

`draft | must | mvp | F1 | S1 | A1`

**Statement.** A duplicate.
""")
    rc, out, err = run_import(capsys, tree)
    assert rc == 1
    assert "problem: requirements.md: R1 appears twice" in out


def test_problem_dangling_header_reference(tmp_path: Path, shop: str, capsys) -> None:
    tree = docs_tree(tmp_path, requirements=edited(
        STANDARD["requirements.md"],
        "`draft | must | mvp | F1 | S1 | A1`", "`draft | must | mvp | F9 | S1 | A1`"))
    rc, out, err = run_import(capsys, tree)
    assert rc == 1
    assert "problem: requirements.md: R1 links F9 which does not exist" in out


def test_problem_criteria_list_mismatch(tmp_path: Path, shop: str, capsys) -> None:
    tree = docs_tree(tmp_path, requirements=edited(
        STANDARD["requirements.md"],
        "`draft | must | mvp | F1 | S1 | A1`", "`draft | must | mvp | F1 | S1 | A1,A2`"))
    rc, out, err = run_import(capsys, tree)
    assert rc == 1
    assert ("problem: requirements.md: R1 lists criteria A1,A2"
            " but acceptance.md has A1") in out


def test_problem_requirement_feature_disagreement(tmp_path: Path, shop: str,
                                                  capsys) -> None:
    tree = docs_tree(tmp_path, features=edited(
        STANDARD["features.md"],
        "| F1 | Store | foundation | planned | - | R1,R2 |",
        "| F1 | Store | foundation | planned | - | R2 |"))
    rc, out, err = run_import(capsys, tree)
    assert rc == 1
    assert ("problem: requirements.md: R1 links F1 but features.md F1"
            " does not link R1") in out


def test_problem_spec_requirement_disagreement(tmp_path: Path, shop: str,
                                               capsys) -> None:
    tree = docs_tree(tmp_path, spec1=edited(
        STANDARD["specs/S1-store.md"],
        "`draft | requirements R1,R2 | features F1`",
        "`draft | requirements R1 | features F1`"))
    rc, out, err = run_import(capsys, tree)
    assert rc == 1
    assert ("problem: requirements.md: R2 links S1 but specs/S1-store.md"
            " does not link R2") in out


def test_problem_status_mismatch_with_the_index(tmp_path: Path, shop: str,
                                                capsys) -> None:
    tree = docs_tree(tmp_path, requirements=edited(
        STANDARD["requirements.md"],
        "`draft | must | mvp | F1 | S1 | A1`", "`approved | must | mvp | F1 | S1 | A1`"))
    rc, out, err = run_import(capsys, tree)
    assert rc == 1
    assert "problem: requirements.md: R1 status is approved but index.md says draft" in out


def test_problem_title_mismatch_with_the_index(tmp_path: Path, shop: str,
                                               capsys) -> None:
    tree = docs_tree(tmp_path, requirements=edited(
        STANDARD["requirements.md"], "## R1 One store", "## R1 One storefront"))
    rc, out, err = run_import(capsys, tree)
    assert rc == 1
    assert ("problem: requirements.md: R1 title is 'One storefront'"
            " but index.md says 'One store'") in out


def test_problems_are_sorted(tmp_path: Path, shop: str, capsys) -> None:
    index = edited(STANDARD["index.md"],
                   "| R2 | draft | should | - | Create entities |",
                   "| R2 | draft | should | - | Create entities |\n"
                   "| R9 | draft | must | mvp | Ghost |")
    index = edited(index, "| R1 | draft | must | mvp | One store |\n", "")
    tree = docs_tree(tmp_path, index=index)
    rc, out, err = run_import(capsys, tree)
    assert rc == 1
    problems = [line for line in out.splitlines() if line.startswith("problem: ")]
    assert problems == sorted(problems)
    assert any("R1 is not in index.md" in line for line in problems)
    assert any("R9 is not in requirements.md" in line for line in problems)


def test_problems_import_nothing(tmp_path: Path, shop: str, capsys) -> None:
    tree = docs_tree(tmp_path, index=edited(
        STANDARD["index.md"], "| R1 | draft | must | mvp | One store |\n", ""))
    rc, out, err = run_import(capsys, tree)
    assert rc == 1
    for kind in ("requirement", "criterion", "feature", "spec", "doc"):
        assert rows(kind) == []
    assert not store.docs_dir("shop").exists() or not any(
        store.docs_dir("shop").iterdir())


# ---------------------------------------------------------------- end to end (A25)
COUNTS = "import-md shop: 2 requirements, 3 criteria, 2 features, 1 specs, 3 docs"


def test_counts_line_and_ids_preserved(tmp_path: Path, shop: str, capsys) -> None:
    rc, out, err = run_import(capsys, docs_tree(tmp_path))
    assert rc == 0, err
    assert out.splitlines()[0] == f"{COUNTS} (11 inserted, 0 skipped, 0 conflicts)"
    assert [row["id"] for row in rows("requirement")] == [1, 2]
    assert [row["id"] for row in rows("criterion")] == [1, 2, 3]
    assert [row["id"] for row in rows("feature")] == [1, 2]
    assert [row["id"] for row in rows("spec")] == [1]
    assert [row["id"] for row in doc_rows()] == [1, 2, 3]


def test_list_and_show_read_the_imported_rows(tmp_path: Path, shop: str,
                                              capsys) -> None:
    assert run_import(capsys, docs_tree(tmp_path))[0] == 0
    rc, out, err = cli(capsys, "list", "R", "--project", "shop")
    assert rc == 0, err
    assert out.splitlines() == [
        "R1  draft  must  mvp  One store  F:1  S:1  A:0/1",
        "R2  draft  should  -  Create entities  F:1,2  S:1  A:0/2",
    ]
    rc, out, err = cli(capsys, "show", "R1", "--project", "shop")
    assert rc == 0, err
    assert "statement: A user can create a project with a slug. It is scoped to that project." in out
    assert "links: F:1  S:1" in out


def test_lint_has_no_errors_after_import(tmp_path: Path, shop: str, capsys) -> None:
    assert run_import(capsys, docs_tree(tmp_path))[0] == 0
    rc, out, err = cli(capsys, "lint", "--project", "shop")
    assert rc == 0, out + err
    assert out.splitlines()[0].startswith("lint (shop): 0 errors")


def test_scan_prd_sees_the_prd_body(tmp_path: Path, shop: str, capsys) -> None:
    assert run_import(capsys, docs_tree(tmp_path))[0] == 0
    rc, out, err = cli(capsys, "scan-prd", "--project", "shop")
    assert rc == 0, err
    assert out.splitlines()[0] == "scan-prd (shop): 3 documents, 1 references, 0 problems"


def test_second_run_is_all_skipped(tmp_path: Path, shop: str, capsys) -> None:
    tree = docs_tree(tmp_path)
    assert run_import(capsys, tree)[0] == 0
    rc, out, err = run_import(capsys, tree)
    assert rc == 0, err
    assert out.splitlines()[0] == f"{COUNTS} (0 inserted, 11 skipped, 0 conflicts)"
    assert len(rows("requirement")) == 2


def test_dry_run_writes_nothing(tmp_path: Path, shop: str, capsys) -> None:
    rc, out, err = run_import(capsys, docs_tree(tmp_path), "--dry-run")
    assert rc == 0, err
    assert out.splitlines()[0] == (
        "import-md shop (dry run): 2 requirements, 3 criteria, 2 features,"
        " 1 specs, 3 docs")
    for kind in ("requirement", "criterion", "feature", "spec", "doc"):
        assert rows(kind) == []
    assert not store.docs_dir("shop").exists() or not any(
        store.docs_dir("shop").iterdir())


def test_dry_run_reports_problems_and_exits_one(tmp_path: Path, shop: str,
                                                capsys) -> None:
    tree = docs_tree(tmp_path, index=edited(
        STANDARD["index.md"], "| R1 | draft | must | mvp | One store |\n", ""))
    rc, out, err = run_import(capsys, tree, "--dry-run")
    assert rc == 1
    assert "problem: requirements.md: R1 is not in index.md" in out
    assert rows("requirement") == []


def test_unknown_project_exits_one_with_the_message(tmp_path: Path, home: Path,
                                                    capsys) -> None:
    tree = docs_tree(tmp_path)
    rc, out, err = cli(capsys, "import-md", str(tree), "--project", "nope")
    assert rc == 1
    assert err.strip() == (
        "project nope does not exist (create it with: project add nope <name> --root DIR)")


def test_doc_body_round_trips_byte_exact(tmp_path: Path, shop: str, capsys) -> None:
    assert run_import(capsys, docs_tree(tmp_path))[0] == 0
    expected = ("**Summary.** What we learned from the prototype.\n\n"
                "## Body\n\nSome body text.\n").encode("utf-8")
    connection = store.connect()
    try:
        row = dict(connection.execute(
            "SELECT * FROM doc WHERE project = 'shop' AND id = 1").fetchone())
    finally:
        connection.close()
    assert store.doc_body_path("shop", row).read_bytes() == expected
    # the PRD body is the whole file, byte-exact
    connection = store.connect()
    try:
        prd_row = dict(connection.execute(
            "SELECT * FROM doc WHERE project = 'shop' AND id = 3").fetchone())
    finally:
        connection.close()
    assert store.doc_body_path("shop", prd_row).read_bytes() == \
        STANDARD["PRD.md"].encode("utf-8")


def test_links_are_imported(tmp_path: Path, shop: str, capsys) -> None:
    assert run_import(capsys, docs_tree(tmp_path))[0] == 0

    def link_rows(sql: str) -> list[tuple]:
        connection = store.connect()
        try:
            return [tuple(row) for row in connection.execute(sql, ("shop",))]
        finally:
            connection.close()

    assert link_rows("SELECT feature_id, requirement_id FROM feature_requirement"
                     " WHERE project = ? ORDER BY feature_id, requirement_id") == [
        (1, 1), (1, 2), (2, 2)]
    assert link_rows("SELECT spec_id, requirement_id FROM spec_requirement"
                     " WHERE project = ? ORDER BY spec_id, requirement_id") == [(1, 1), (1, 2)]
    assert link_rows("SELECT feature_id, spec_id FROM feature_spec"
                     " WHERE project = ? ORDER BY feature_id, spec_id") == [(1, 1)]
    assert link_rows("SELECT feature_id, needs_id, optional FROM feature_dependency"
                     " WHERE project = ? ORDER BY feature_id, needs_id") == [(2, 1, 0)]
    assert link_rows("SELECT doc_id, entity_type, entity_id FROM doc_link"
                     " WHERE project = ? ORDER BY doc_id, entity_type, entity_id") == [
        (1, "feature", 1), (1, "requirement", 1), (2, "requirement", 2), (2, "spec", 1)]


def test_spec_summary_is_imported_and_no_body_stored(tmp_path: Path, shop: str,
                                                     capsys) -> None:
    assert run_import(capsys, docs_tree(tmp_path))[0] == 0
    [spec] = rows("spec")
    assert spec["summary"] == "How rows are stored and scoped to one project."
    assert spec["title"] == "Store"
    assert spec["status"] == "draft"


def test_doc_note_and_ignored_link_are_reported(tmp_path: Path, shop: str,
                                                capsys) -> None:
    rc, out, err = run_import(capsys, docs_tree(tmp_path))
    assert rc == 0, err
    assert "note: DOC1 link PRD#risks is not an entry" in out.splitlines()
    assert doc_rows()[0]["notes"] == "see the plan"


def test_doc_summary_is_clipped_to_the_maximum(tmp_path: Path, shop: str,
                                               capsys) -> None:
    long_summary = "word " * 90
    doc = STANDARD["supporting/DOC1-notes.md"].replace(
        "What we learned from the prototype.", long_summary.strip())
    tree = docs_tree(tmp_path, doc1=doc)
    assert run_import(capsys, tree)[0] == 0
    summary = doc_rows()[0]["summary"]
    assert len(summary) == 400
    assert summary.startswith("word word")


# ------------------------------------------- a stable PRD id across re-imports (HP-10 follow-up)
def test_reimport_keeps_the_stored_prd_id_when_a_supporting_doc_is_added(
        tmp_path: Path, shop: str, capsys) -> None:
    tree = docs_tree(tmp_path)
    rc, out, err = run_import(capsys, tree)
    assert rc == 0, err
    prd_before = [row["id"] for row in doc_rows() if row["kind"] == "prd"]
    assert prd_before == [3]
    # a new supporting document with a HIGHER id than the stored PRD
    (tree / "supporting" / "DOC4-extra.md").write_text(
        "# DOC4 Extra notes\n\n`kind: research | status: current | links: R1`\n\n"
        "**Summary.** More notes.\n\nBody.\n", encoding="utf-8")
    index = (tree / "index.md").read_text(encoding="utf-8")
    index = index.replace("| DOC2 |", "| DOC4 | research | current | [Extra notes](supporting/DOC4-extra.md) |\n| DOC2 |", 1)
    (tree / "index.md").write_text(index, encoding="utf-8")
    rc, out, err = run_import(capsys, tree)
    assert rc == 0, out + err
    assert "0 conflicts" in out
    prds = [row["id"] for row in doc_rows() if row["kind"] == "prd"]
    assert prds == [3]                                   # still ONE prd, same id
    assert sorted(row["id"] for row in doc_rows()) == [1, 2, 3, 4]


def test_new_prd_id_is_above_the_store_and_the_files(tmp_path: Path, shop: str, capsys) -> None:
    connection = store.connect()
    try:
        connection.execute("UPDATE id_counter SET next_id = 9 WHERE project = 'shop' AND kind = 'DOC'")
        connection.execute(
            "INSERT INTO doc(project, id, title, kind, summary, status) VALUES"
            " ('shop', 7, 'Old', 'other', 'old doc', 'current')")
        connection.commit()
    finally:
        connection.close()
    documents = importmd.build_documents(docs_tree(tmp_path))
    assert importmd._settle_prd_id(documents, "shop") == []
    assert documents.prd.id == 8


def test_stored_prd_id_claimed_by_a_supporting_file_is_a_problem(
        tmp_path: Path, shop: str, capsys) -> None:
    rc, out, err = run_import(capsys, docs_tree(tmp_path))
    assert rc == 0, err
    # now the files claim DOC3 for a supporting document while the store's DOC3 is the PRD
    (tmp_path / "docs" / "supporting" / "DOC3-clash.md").write_text(
        "# DOC3 Clash\n\n`kind: research | status: current | links: R1`\n\n**Summary.** x.\n", encoding="utf-8")
    index = (tmp_path / "docs" / "index.md").read_text(encoding="utf-8")
    index = index.replace("| DOC2 |", "| DOC3 | research | current | [Clash](supporting/DOC3-clash.md) |\n| DOC2 |", 1)
    (tmp_path / "docs" / "index.md").write_text(index, encoding="utf-8")
    rc, out, err = run_import(capsys, tmp_path / "docs")
    assert rc == 1
    assert "stored PRD" in out + err
