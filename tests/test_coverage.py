"""HP-13a: the code-path coverage core (``code_coverage.py``), pure and store-free.

``owner_of`` matches entries' ``paths`` on path-segment boundaries; a package is a
directory that directly holds at least one non-test source file; spec files are the
``*.md`` files under ``spec_dirs``. ``coverage_report`` returns the full counts plus
lists cut to ``max_items`` with a per-list hidden count. ``walk_repo`` is the only
function that touches the filesystem. A24 is the unlisted-package case.
"""

from __future__ import annotations

from pathlib import Path

import code_coverage as cov


# ---------------------------------------------------------------- owner_of
def test_owner_of_matches_on_a_segment_boundary() -> None:
    entries = {"F1": ["pkg/a"]}
    assert cov.owner_of("pkg/a/x.go", entries) == ["F1"]
    assert cov.owner_of("pkg/a", entries) == ["F1"]
    assert cov.owner_of("pkg/ab/x.go", entries) == []
    assert cov.owner_of("pkg/a2/x.go", entries) == []


def test_owner_of_returns_every_owner_sorted() -> None:
    entries = {"F2": ["pkg"], "F1": ["pkg/a"], "S9": ["pkg/a/x.go"]}
    assert cov.owner_of("pkg/a/x.go", entries) == ["F1", "F2", "S9"]


def test_owner_of_file_path_covers_only_itself() -> None:
    entries = {"S1": ["docs/specs/S1.md"]}
    assert cov.owner_of("docs/specs/S1.md", entries) == ["S1"]
    assert cov.owner_of("docs/specs/S2.md", entries) == []


def test_owner_of_dot_covers_every_relative_path() -> None:
    entries = {"F1": ["."]}
    assert cov.owner_of("a/b/c.go", entries) == ["F1"]
    assert cov.owner_of("main.go", entries) == ["F1"]
    assert cov.owner_of("../outside.go", entries) == []


def test_owner_of_ignores_absolute_and_dotdot_entries() -> None:
    entries = {"F1": ["/abs/path"], "F2": ["../up"], "F3": ["pkg/../../etc"]}
    assert cov.owner_of("pkg/a/x.go", entries) == []
    assert cov.owner_of("/abs/path/x.go", entries) == []


def test_owner_of_normalizes_entry_paths() -> None:
    entries = {"F1": ["./pkg/a/", "pkg//b"]}
    assert cov.owner_of("pkg/a/x.go", entries) == ["F1"]
    assert cov.owner_of("pkg/b/x.go", entries) == ["F1"]


def test_owner_of_empty_path_lists_and_no_entries() -> None:
    assert cov.owner_of("pkg/a/x.go", {}) == []
    assert cov.owner_of("pkg/a/x.go", {"F1": []}) == []


# ------------------------------------------------------- packages in a report
def test_segment_boundary_sibling_is_not_covered() -> None:
    files = ["venue/binance/a.go", "venue/binance2/b.go"]
    report = cov.coverage_report(files, {"F1": ["venue/binance"]})
    assert report["packages_total"] == 2
    assert report["packages_covered"] == 1
    assert report["uncovered_packages"] == ["venue/binance2"]


def test_nested_directory_covered_by_an_ancestor() -> None:
    files = ["pkg/a/b/c/x.go"]
    report = cov.coverage_report(files, {"F1": ["pkg/a"]})
    assert report["packages_total"] == 1
    assert report["uncovered_packages"] == []


def test_parent_of_a_claimed_package_is_covered() -> None:
    files = ["a/a.go", "a/sub/sub.go", "a/sub/deep/x.go"]
    report = cov.coverage_report(files, {"F1": ["a/sub/deep"]})
    assert report["uncovered_packages"] == []
    assert report["packages_total"] == 3 and report["packages_covered"] == 3


def test_file_path_entry_covers_its_package() -> None:
    report = cov.coverage_report(["pkg/a/x.go"], {"F1": ["pkg/a/x.go"]})
    assert report["packages_total"] == 1 and report["uncovered_packages"] == []


def test_test_file_only_directory_is_not_a_package() -> None:
    files = [
        "c/c_test.go",
        "d/test_app.py",
        "j/x.spec.ts",
        "k/y.test.js",
        "test_helpers.py",
    ]
    report = cov.coverage_report(files, {})
    assert report["packages_total"] == 0
    assert report["uncovered_packages"] == []


def test_skip_dirs_never_become_packages() -> None:
    files = [
        "tests/a.go", "test/b.go", "testdata/c.go", "examples/d.go",
        "scripts/e.go", "build/f.go", "dist/g.go", "target/h.go",
        "node_modules/i/j.go", "vendor/k/l.go", ".hidden/m.go",
        "src/app.py",
    ]
    report = cov.coverage_report(files, {})
    assert report["packages_total"] == 1
    assert report["uncovered_packages"] == ["src"]


def test_source_extensions_decide_packages() -> None:
    files = [
        "code/main.go", "code/app.py", "code/lib.rs", "code/a.ts",
        "code/b.tsx", "code/c.js", "code/D.java", "code/e.kt",
        "code/f.swift", "code/g.c", "code/h.cc", "code/i.cpp", "code/j.h",
        "words/readme.md", "words/data.txt", "words/Makefile",
    ]
    report = cov.coverage_report(files, {})
    assert report["packages_total"] == 1
    assert report["uncovered_packages"] == ["code"]


def test_repository_root_is_a_package() -> None:
    uncovered = cov.coverage_report(["main.go", "app.py"], {})
    assert uncovered["packages_total"] == 1
    assert uncovered["uncovered_packages"] == ["."]

    covered = cov.coverage_report(["main.go", "app.py"], {"F1": ["."]})
    assert covered["uncovered_packages"] == []
    assert covered["packages_covered"] == 1


def test_uncovered_packages_are_sorted() -> None:
    files = ["z/z.go", "a/a.go", "m/m.go"]
    assert cov.coverage_report(files, {})["uncovered_packages"] == ["a", "m", "z"]


# ------------------------------------------------------------------- specs
def test_spec_files_are_markdown_under_the_spec_dir() -> None:
    files = [
        "docs/PRD.md",
        "docs/specs/S1.md",
        "docs/specs/notes.txt",
        "docs/specs/sub/S2.md",
        "other/O.md",
    ]
    report = cov.coverage_report(files, {})
    assert report["specs_total"] == 2
    assert report["specs_covered"] == 0
    assert report["uncovered_specs"] == ["docs/specs/S1.md", "docs/specs/sub/S2.md"]


def test_spec_covered_by_its_own_path() -> None:
    files = ["docs/specs/S1.md", "docs/specs/S2.md"]
    entries = {"S1": ["docs/specs/S1.md"]}
    report = cov.coverage_report(files, entries)
    assert report["specs_total"] == 2 and report["specs_covered"] == 1
    assert report["uncovered_specs"] == ["docs/specs/S2.md"]


def test_spec_covered_by_a_directory_entry() -> None:
    files = ["docs/specs/S1.md", "docs/specs/sub/S2.md"]
    report = cov.coverage_report(files, {"S9": ["docs/specs"]})
    assert report["specs_total"] == 2 and report["uncovered_specs"] == []


def test_custom_spec_dirs() -> None:
    files = ["spec/A.md", "docs/specs/S1.md"]
    report = cov.coverage_report(files, {}, spec_dirs=("spec",))
    assert report["specs_total"] == 1
    assert report["uncovered_specs"] == ["spec/A.md"]


def test_a24_package_no_entry_lists_is_uncovered() -> None:
    files = ["pkg/known/a.go", "pkg/new/b.go"]
    report = cov.coverage_report(files, {"F1": ["pkg/known"]})
    assert report["packages_total"] == 2 and report["packages_covered"] == 1
    assert report["uncovered_packages"] == ["pkg/new"]
    lines = cov.format_report(report)
    assert "  uncovered package: pkg/new" in lines


# -------------------------------------------------------------- truncation
def test_max_items_cuts_the_list_not_the_counts() -> None:
    files = [f"p{i}/main.go" for i in range(30)]
    report = cov.coverage_report(files, {}, max_items=5)
    assert report["packages_total"] == 30 and report["packages_covered"] == 0
    assert len(report["uncovered_packages"]) == 5
    assert report["uncovered_packages"][0] == "p0"
    assert report["truncated"] == {"uncovered_packages": 25, "uncovered_specs": 0}


def test_truncated_specs_count_is_reported() -> None:
    files = [f"docs/specs/S{i}.md" for i in range(4)]
    report = cov.coverage_report(files, {}, max_items=2)
    assert report["specs_total"] == 4 and report["specs_covered"] == 0
    assert len(report["uncovered_specs"]) == 2
    assert report["truncated"] == {"uncovered_packages": 0, "uncovered_specs": 2}


def test_max_items_zero_still_reports_full_counts() -> None:
    files = ["a/a.go", "b/b.go", "docs/specs/S1.md"]
    report = cov.coverage_report(files, {}, max_items=0)
    assert report["uncovered_packages"] == [] and report["uncovered_specs"] == []
    assert report["packages_total"] == 2 and report["specs_total"] == 1
    assert report["truncated"] == {"uncovered_packages": 2, "uncovered_specs": 1}


# ------------------------------------------------------------ odd / empty
def test_empty_inputs_give_zeroed_report() -> None:
    report = cov.coverage_report([], {})
    assert report == {
        "packages_total": 0,
        "packages_covered": 0,
        "uncovered_packages": [],
        "specs_total": 0,
        "specs_covered": 0,
        "uncovered_specs": [],
        "truncated": {"uncovered_packages": 0, "uncovered_specs": 0},
    }
    assert set(report) == {
        "packages_total", "packages_covered", "uncovered_packages",
        "specs_total", "specs_covered", "uncovered_specs", "truncated",
    }


def test_entries_with_no_paths_cover_nothing() -> None:
    report = cov.coverage_report(["a/a.go"], {"F1": [], "S1": []})
    assert report["packages_covered"] == 0
    assert report["uncovered_packages"] == ["a"]


def test_report_is_deterministic_regardless_of_input_order() -> None:
    files = ["b/b.go", "a/a.go", "b/c.go"]
    one = cov.coverage_report(files, {"F2": ["a"], "F1": ["zzz"]})
    two = cov.coverage_report(list(reversed(files)), {"F1": ["zzz"], "F2": ["a"]})
    assert one == two
    assert one["uncovered_packages"] == ["b"]


def test_non_string_paths_are_ignored_not_fatal() -> None:
    report = cov.coverage_report(["ok/x.go", "bad/y.go"], {"F1": ["ok", 5, None]})
    assert report["packages_covered"] == 1
    assert report["uncovered_packages"] == ["bad"]


# ---------------------------------------------------------------- walk_repo
def test_walk_repo_is_sorted_and_skips_noise(tmp_path: Path) -> None:
    for rel in (
        "b/main.go",
        "a/x.go",
        "src/sub/deep.py",
        "README.md",
        "tests/t.go",
        "testdata/d.go",
        "vendor/v.go",
        "node_modules/n.js",
        ".git/config",
        ".hidden/h.go",
    ):
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x", encoding="utf-8")

    assert cov.walk_repo(tmp_path) == [
        "README.md",
        "a/x.go",
        "b/main.go",
        "src/sub/deep.py",
    ]
    assert cov.walk_repo(str(tmp_path)) == cov.walk_repo(tmp_path)


def test_walk_repo_missing_root_is_empty(tmp_path: Path) -> None:
    assert cov.walk_repo(tmp_path / "gone") == []


# ------------------------------------------------------------- format_report
def test_format_report_exact_lines() -> None:
    report = {
        "packages_total": 9,
        "packages_covered": 7,
        "uncovered_packages": ["pkg/a", "pkg/b"],
        "specs_total": 12,
        "specs_covered": 5,
        "uncovered_specs": ["docs/specs/S3.md"],
        "truncated": {"uncovered_packages": 0, "uncovered_specs": 0},
    }
    assert cov.format_report(report) == [
        "coverage: packages 7/9, specs 5/12",
        "  uncovered package: pkg/a",
        "  uncovered package: pkg/b",
        "  uncovered spec: docs/specs/S3.md",
    ]


def test_format_report_prints_one_more_line_per_truncated_list() -> None:
    report = {
        "packages_total": 9,
        "packages_covered": 7,
        "uncovered_packages": ["pkg/a", "pkg/b"],
        "specs_total": 12,
        "specs_covered": 5,
        "uncovered_specs": ["docs/specs/S3.md"],
        "truncated": {"uncovered_packages": 3, "uncovered_specs": 2},
    }
    assert cov.format_report(report) == [
        "coverage: packages 7/9, specs 5/12",
        "  uncovered package: pkg/a",
        "  uncovered package: pkg/b",
        "  … and 3 more",
        "  uncovered spec: docs/specs/S3.md",
        "  … and 2 more",
    ]


def test_format_report_clean_and_empty() -> None:
    clean = {
        "packages_total": 2, "packages_covered": 2, "uncovered_packages": [],
        "specs_total": 1, "specs_covered": 1, "uncovered_specs": [],
        "truncated": {"uncovered_packages": 0, "uncovered_specs": 0},
    }
    assert cov.format_report(clean) == ["coverage: packages 2/2, specs 1/1"]

    empty = {
        "packages_total": 0, "packages_covered": 0, "uncovered_packages": [],
        "specs_total": 0, "specs_covered": 0, "uncovered_specs": [],
        "truncated": {"uncovered_packages": 0, "uncovered_specs": 0},
    }
    assert cov.format_report(empty) == ["coverage: packages 0/0, specs 0/0"]
