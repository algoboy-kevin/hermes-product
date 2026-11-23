"""Coverage lint tests (card HP-13): A24, `lint --coverage` and the uncovered_path info findings.

End to end through ``product lint`` on a tmp project root (never this repo, never
``docs/``). ``lint.check`` stays pure, so the info findings are also exercised on
a hand-built facts dict that never opens the store.
"""

from __future__ import annotations

import json
from pathlib import Path

import lint
import store
from _helpers import (  # `home`/`con` are fixtures imported for discovery
    add_feature, add_requirement, add_spec, cli, con, home, raw_update,
)


# --------------------------------------------------------------------- fixtures
def _touch(root: Path, *paths: str) -> None:
    for relative in paths:
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("x = 1\n", encoding="utf-8")


def _project(capsys, tmp_path: Path, *, root: bool = True) -> Path:
    """Create project ``shop`` through ``project add``; returns its root directory."""
    base = tmp_path / "repo"
    base.mkdir()
    argv = ["project", "add", "shop", "Shop"]
    if root:
        argv += ["--root", str(base)]
    rc, _out, err = cli(capsys, *argv)
    assert rc == 0, err
    return base


def _package(path: str) -> str:
    return f"info uncovered_path project  package {path}: no F#/S# paths entry covers it"


def _spec(path: str) -> str:
    return f"info uncovered_path project  spec {path}: no F#/S# paths entry covers it"


def _report(*, packages: list[str], specs: list[str], packages_total: int | None = None,
            specs_total: int | None = None, hidden_packages: int = 0,
            hidden_specs: int = 0) -> dict:
    packages_total = len(packages) if packages_total is None else packages_total
    specs_total = len(specs) if specs_total is None else specs_total
    return {
        "packages_total": packages_total,
        "packages_covered": packages_total - len(packages),
        "uncovered_packages": list(packages),
        "specs_total": specs_total,
        "specs_covered": specs_total - len(specs),
        "uncovered_specs": list(specs),
        "truncated": {"uncovered_packages": hidden_packages,
                      "uncovered_specs": hidden_specs},
    }


def _facts(**parts) -> dict:
    base = {
        "requirements": [],
        "criteria": [],
        "features": [],
        "specs": [],
        "docs": [],
        "deps": {"requirement": [], "feature": [], "spec": []},
        "history": [],
        "doc_problems": [],
        "doc_drift": [],
    }
    base.update(parts)
    return base


# ------------------------------------------------------------------------ A24
def test_a24_a_package_no_entry_lists_is_reported_uncovered(home, tmp_path, capsys) -> None:
    root = _project(capsys, tmp_path)
    _touch(root, "pkg/a/x.py", "pkg/b/y.py", "docs/specs/S9.md")
    add_feature(paths=["pkg/a"], infra=1)
    rc, out, err = cli(capsys, "lint", "--coverage")
    assert rc == 0, err
    lines = out.splitlines()
    assert _package("pkg/b") in lines
    assert _spec("docs/specs/S9.md") in lines
    assert not any("package pkg/a:" in line for line in lines)
    assert lines[0] == "lint (shop): 0 errors, 0 warnings"
    assert lines[-1] == "coverage: packages 1/2, specs 0/1"


def test_a_spec_paths_entry_covers_an_uncovered_package(home, tmp_path, capsys) -> None:
    root = _project(capsys, tmp_path)
    _touch(root, "pkg/a/x.py", "pkg/b/y.py")
    add_feature(paths=["pkg/a"])
    add_spec(paths=["pkg/b"])
    rc, out, err = cli(capsys, "lint", "--coverage")
    assert rc == 0, err
    assert not any("package pkg/b:" in line for line in out.splitlines())
    assert out.splitlines()[-1] == "coverage: packages 2/2, specs 0/0"


def test_a_file_claim_covers_only_that_file(home, tmp_path, capsys) -> None:
    root = _project(capsys, tmp_path)
    _touch(root, "pkg/a/x.py", "docs/specs/S1.md", "docs/specs/S2.md")
    add_feature(paths=["pkg/a"])
    add_spec(paths=["docs/specs/S1.md"])
    rc, out, err = cli(capsys, "lint", "--coverage")
    assert rc == 0, err
    lines = out.splitlines()
    assert _spec("docs/specs/S2.md") in lines
    assert _spec("docs/specs/S1.md") not in lines


def test_a_directory_claim_does_not_cover_a_sibling_with_the_same_name_prefix(
        home, tmp_path, capsys) -> None:
    root = _project(capsys, tmp_path)
    _touch(root, "pkg/a/x.py", "pkg/ab/y.py")
    add_feature(paths=["pkg/a"])
    rc, out, err = cli(capsys, "lint", "--coverage")
    assert rc == 0, err
    lines = out.splitlines()
    assert _package("pkg/ab") in lines
    assert not any("package pkg/a:" in line for line in lines)


# ------------------------------------------------------------- retired claims
def test_a_cut_feature_claim_is_ignored(home, tmp_path, capsys) -> None:
    root = _project(capsys, tmp_path)
    _touch(root, "pkg/a/x.py")
    add_feature(paths=["pkg/a"])
    store.update_entity("shop", "F1", {"status": "cut", "status_reason": "descoped"})
    rc, out, err = cli(capsys, "lint", "--coverage")
    assert rc == 0, err
    assert _package("pkg/a") in out.splitlines()
    assert out.splitlines()[-1] == "coverage: packages 0/1, specs 0/0"


def test_a_superseded_spec_claim_is_ignored(home, tmp_path, capsys) -> None:
    root = _project(capsys, tmp_path)
    _touch(root, "pkg/a/x.py")
    add_spec(paths=["pkg/a"])
    add_spec(title="Replacement", summary="The replacement spec")
    store.update_entity("shop", "S1", {"status": "superseded", "superseded_by": 2})
    rc, out, err = cli(capsys, "lint", "--coverage")
    assert rc == 0, err
    assert _package("pkg/a") in out.splitlines()


def test_a_feature_with_no_paths_contributes_no_claim(home, tmp_path, capsys) -> None:
    root = _project(capsys, tmp_path)
    _touch(root, "pkg/a/x.py")
    add_feature()
    add_spec()
    rc, out, err = cli(capsys, "lint", "--coverage")
    assert rc == 0, err
    assert _package("pkg/a") in out.splitlines()
    assert out.splitlines()[-1] == "coverage: packages 0/1, specs 0/0"


# ------------------------------------------------------------------ skip dirs
def test_skip_directories_are_never_reported(home, tmp_path, capsys) -> None:
    root = _project(capsys, tmp_path)
    _touch(root, "pkg/a/x.py", "tests/t/x.py", "vendor/v/y.py",
           "node_modules/n/z.js", "build/b/b.py", "dist/d/d.py")
    add_feature(paths=["pkg/a"])
    rc, out, err = cli(capsys, "lint", "--coverage")
    assert rc == 0, err
    lines = out.splitlines()
    for skipped in ("tests", "vendor", "node_modules", "build", "dist"):
        assert not any(f"package {skipped}" in line for line in lines), skipped
    assert lines[-1] == "coverage: packages 1/1, specs 0/0"


# ----------------------------------------------------------------- truncation
def test_truncation_lists_fifty_and_reports_the_hidden_count(home, tmp_path, capsys) -> None:
    root = _project(capsys, tmp_path)
    for number in range(60):
        _touch(root, f"pkg{number:02d}/x.py")
    rc, out, err = cli(capsys, "lint", "--coverage")
    assert rc == 0, err
    lines = out.splitlines()
    listed = [line for line in lines if line.startswith("info uncovered_path project  package ")]
    assert len(listed) == 50
    assert "info uncovered_path project  and 10 more uncovered packages" in lines
    assert lines[-1] == "coverage: packages 0/60, specs 0/0"
    rc, out, err = cli(capsys, "lint", "--coverage", "--json")
    assert rc == 0, err
    report = json.loads(out)["coverage"]
    assert report["packages_total"] == 60
    assert report["packages_covered"] == 0
    assert len(report["uncovered_packages"]) == 50
    assert report["truncated"]["uncovered_packages"] == 10


# ------------------------------------------------------ severity and exit code
def test_header_counts_exclude_info_findings(home, tmp_path, capsys) -> None:
    root = _project(capsys, tmp_path)
    _touch(root, "pkg/b/y.py")
    rc, out, err = cli(capsys, "lint", "--coverage")
    assert rc == 0, err
    lines = out.splitlines()
    assert lines[0] == "lint (shop): 0 errors, 0 warnings"
    assert _package("pkg/b") in lines


def test_exit_is_one_when_a_lint_error_also_exists(home, con, tmp_path, capsys) -> None:
    root = _project(capsys, tmp_path)
    _touch(root, "pkg/b/y.py")
    add_requirement()
    raw_update(con, "shop", "requirement", 1, status="in_progress")
    rc, out, err = cli(capsys, "lint", "--coverage")
    assert rc == 1, err
    lines = out.splitlines()
    assert lines[0] == "lint (shop): 1 errors, 2 warnings"
    assert "error no_criteria R1  in_progress with no criterion" in lines
    assert _package("pkg/b") in lines


def test_the_summary_line_is_the_exact_coverage_report_line(home, tmp_path, capsys) -> None:
    root = _project(capsys, tmp_path)
    _touch(root, "pkg/a/x.py", "pkg/b/y.py", "docs/specs/S9.md")
    add_feature(paths=["pkg/a"])
    add_spec(paths=["docs/specs/S9.md"])
    rc, out, err = cli(capsys, "lint", "--coverage")
    assert rc == 0, err
    assert out.splitlines()[-1] == "coverage: packages 1/2, specs 1/1"


def test_no_not_yet_checked_line_with_coverage(home, tmp_path, capsys) -> None:
    _touch(_project(capsys, tmp_path), "pkg/a/x.py")
    rc, out, err = cli(capsys, "lint", "--coverage")
    assert rc == 0, err
    assert "not yet checked" not in out


# --------------------------------------------------------------------- JSON
def test_json_with_coverage_carries_the_report_and_an_empty_not_yet(
        home, tmp_path, capsys) -> None:
    root = _project(capsys, tmp_path)
    _touch(root, "pkg/a/x.py", "pkg/b/y.py", "docs/specs/S9.md")
    add_feature(paths=["pkg/a"], infra=1)
    rc, out, err = cli(capsys, "lint", "--coverage", "--json")
    assert rc == 0, err
    payload = json.loads(out)
    assert payload["coverage"] == {
        "packages_total": 2,
        "packages_covered": 1,
        "uncovered_packages": ["pkg/b"],
        "specs_total": 1,
        "specs_covered": 0,
        "uncovered_specs": ["docs/specs/S9.md"],
        "truncated": {"uncovered_packages": 0, "uncovered_specs": 0},
    }
    assert payload["not_yet"] == []
    assert payload["errors"] == 0 and payload["warnings"] == 0
    assert {"flag": "uncovered_path", "severity": "info", "ref": "project",
            "message": "package pkg/b: no F#/S# paths entry covers it"} in payload["findings"]
    assert out == json.dumps(payload, sort_keys=True) + "\n"


def test_json_without_coverage_is_unchanged(home, capsys) -> None:
    store.create_project("shop", "Shop")
    rc, out, err = cli(capsys, "lint", "--json")
    assert rc == 0, err
    payload = json.loads(out)
    assert "coverage" not in payload
    assert payload["not_yet"] == ["uncovered_path"]
    assert out == json.dumps(payload, sort_keys=True) + "\n"


def test_text_without_coverage_is_unchanged(home, capsys) -> None:
    store.create_project("shop", "Shop")
    rc, out, err = cli(capsys, "lint")
    assert rc == 0, err
    assert out == "lint (shop): 0 errors, 0 warnings\nnot yet checked: uncovered_path\n"


# ----------------------------------------------------------------- no usable root
def test_a_project_without_a_root_exits_one_with_the_message(home, tmp_path, capsys) -> None:
    _project(capsys, tmp_path, root=False)
    rc, out, err = cli(capsys, "lint", "--coverage")
    assert rc == 1
    assert out == ""
    assert err.strip() == ("--coverage needs the project's root directory"
                           " (project add --root DIR); shop has root none")


def test_a_root_that_is_a_missing_directory_exits_one_with_the_same_message(
        home, tmp_path, capsys) -> None:
    base = _project(capsys, tmp_path)
    stored_root = store.get_project("shop")["root"]
    base.rename(tmp_path / "moved-away")
    rc, out, err = cli(capsys, "lint", "--coverage")
    assert rc == 1
    assert out == ""
    assert err.strip() == ("--coverage needs the project's root directory"
                           f" (project add --root DIR); shop has root {stored_root}")


# -------------------------------------------------------------- check is pure
def test_check_builds_the_infos_without_touching_the_store(monkeypatch) -> None:
    def _boom(*args, **kwargs):
        raise AssertionError("check must not open the store")

    monkeypatch.setattr(store, "connect", _boom)
    monkeypatch.setattr(store, "list_entities", _boom)
    found = lint.check(_facts(coverage=_report(
        packages=["pkg/b"], specs=["docs/specs/S9.md"], packages_total=2, specs_total=1,
        hidden_specs=3)))
    assert [(finding.flag, finding.severity, finding.ref, finding.message)
            for finding in found] == [
        ("uncovered_path", "info", "project",
         "and 3 more uncovered specs"),
        ("uncovered_path", "info", "project",
         "package pkg/b: no F#/S# paths entry covers it"),
        ("uncovered_path", "info", "project",
         "spec docs/specs/S9.md: no F#/S# paths entry covers it"),
    ]


def test_project_findings_sort_after_every_other_finding() -> None:
    requirement = {"id": 1, "status": "in_progress", "legacy": 0, "blocked": 0,
                   "links": {"features": [], "specs": []}}
    found = lint.check(_facts(
        requirements=[requirement],
        coverage=_report(packages=["pkg/c", "pkg/b"], specs=["docs/specs/S9.md"],
                         packages_total=2, specs_total=1, hidden_specs=1)))
    assert [finding.ref for finding in found[-4:]] == ["project"] * 4
    messages = [finding.message for finding in found if finding.ref == "project"]
    assert messages == sorted(messages)
    assert [finding.severity for finding in found if finding.ref == "project"] == ["info"] * 4
