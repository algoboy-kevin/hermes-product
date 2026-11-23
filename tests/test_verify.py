"""`verify`: passed criteria must cite a quote that really exists in the file at a revision.

Pure helpers first (grammar, quote location across wrapped lines, verdicts), then the verb
against a real throwaway git repository: committed text passes, text that exists only in
the working tree does not (a revision is the point), free-text evidence is counted not
failed, `--strict` fails it, and the exit code is 1 on any miss.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

import store
import verify
from _helpers import add_criterion, add_requirement, cli, home, make_project  # noqa: F401

QUOTE = "the tracker emits exactly one row per second"
DOC = ("# Progress\n"
       "\n"
       "Run 7 result: The tracker emits exactly one\n"
       "row per second under load.\n"
       "\n" + "filler\n" * 30 + "Another claim: parity holds on all 31 symbols in the grid.\n")


# --------------------------------------------------------------------------- pure
def test_parse_evidence() -> None:
    assert verify.parse_evidence('"a quote" -- docs/P.md:12') == ("a quote", "docs/P.md", 12)
    assert verify.parse_evidence('  "x y" --  a/b.go:3  ') == ("x y", "a/b.go", 3)
    assert verify.parse_evidence("test_pay_by_card") is None
    assert verify.parse_evidence('"quote" -- path-without-line.md') is None
    assert verify.parse_evidence(None) is None


def test_locate_quote_spans_wrapped_lines_and_ignores_case_and_space() -> None:
    assert verify.locate_quote(DOC, QUOTE) == (3, 4)
    assert verify.locate_quote(DOC, "THE   tracker   EMITS exactly one ROW per second") == (3, 4)
    assert verify.locate_quote(DOC, "parity holds on all 31 symbols") == (36, 36)
    assert verify.locate_quote(DOC, "parity holds on all 32 symbols") is None
    assert verify.locate_quote(DOC, "   ") is None


def test_judge_verdicts() -> None:
    cited = f'"{QUOTE}" -- docs/P.md:3'
    assert verify.judge(cited, DOC) == ("ok", "docs/P.md:3")
    assert verify.judge(f'"{QUOTE}" -- docs/P.md:35', DOC)[0] == "drift"
    assert verify.judge(f'"{QUOTE}" -- docs/P.md:5', DOC)[0] == "ok"            # within slack
    assert verify.judge('"an invented result nobody wrote" -- docs/P.md:3', DOC)[0] == "fail"
    assert verify.judge(cited, None)[0] == "fail"
    short = verify.judge('"too short" -- docs/P.md:3', DOC)
    assert short[0] == "fail" and "at least 25" in short[1]
    assert verify.judge("test_pay_by_card", DOC)[0] == "free"


# ----------------------------------------------------------------- through a git repo
def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args], cwd=root,
                   check=True, capture_output=True)


@pytest.fixture()
def repo(home: Path, tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    (root / "docs").mkdir(parents=True)
    (root / "docs" / "P.md").write_text(DOC, encoding="utf-8")
    _git(root, "init", "-q")
    _git(root, "add", ".")
    _git(root, "commit", "-q", "-m", "evidence")
    store.create_project("shop", "Shop", root=root)
    add_requirement("shop")
    return root


def _pass(capsys, evidence: str) -> str:
    n = len(store.list_entities("shop", "criterion")) + 1
    assert cli(capsys, "add", "A", "--req", "R1", "--text", f"Given check {n} then ok")[0] == 0
    rc, _out, err = cli(capsys, "set", f"A{n}", "status=passed", f"evidence={evidence}")
    assert rc == 0, err
    return f"A{n}"


def test_verify_ok_fail_free_and_exit_codes(repo, capsys) -> None:
    good = _pass(capsys, f'"{QUOTE}" -- docs/P.md:3')
    bad = _pass(capsys, '"an invented result that nobody ever wrote down" -- docs/P.md:3')
    free = _pass(capsys, "test_pay_by_card")
    gone = _pass(capsys, f'"{QUOTE}" -- docs/missing.md:3')
    rc, out, err = cli(capsys, "verify")
    assert rc == 1, err
    lines = out.splitlines()
    assert any(l.startswith(f"{bad}  fail  quote not found in docs/P.md") for l in lines)
    assert any(l.startswith(f"{gone}  fail  docs/missing.md cannot be read at the chosen revision")
               for l in lines)
    assert any(l.startswith(f"{free}  free  ") for l in lines)
    assert not any(l.startswith(f"{good}  ") for l in lines)
    assert lines[-1] == "verify (shop)@HEAD: 4 passed criteria, 1 ok, 0 drift, 2 fail, 1 free text"


def test_only_free_text_passes_unless_strict(repo, capsys) -> None:
    _pass(capsys, f'"{QUOTE}" -- docs/P.md:3')
    _pass(capsys, "test_pay_by_card")
    assert cli(capsys, "verify")[0] == 0
    rc, out, _err = cli(capsys, "verify", "--strict")
    assert rc == 1 and "1 free text" in out


def test_uncommitted_text_is_not_evidence_at_head(repo, capsys) -> None:
    path = repo / "docs" / "P.md"
    path.write_text(path.read_text(encoding="utf-8") + "\nBrand new uncommitted result line one.\n",
                    encoding="utf-8")
    _pass(capsys, '"Brand new uncommitted result line one." -- docs/P.md:40')
    rc, out, _err = cli(capsys, "verify")
    assert rc == 1 and "quote not found" in out
    # the working tree does see it, and says so in its header
    rc, out, _err = cli(capsys, "verify", "--rev", "HEAD")
    assert rc == 1
    _git(repo, "commit", "-q", "-am", "now committed")
    rc, out, _err = cli(capsys, "verify")
    assert rc == 0 and "1 ok" in out


def test_drift_is_reported_but_passes(repo, capsys) -> None:
    _pass(capsys, f'"{QUOTE}" -- docs/P.md:30')
    rc, out, _err = cli(capsys, "verify")
    assert rc == 0
    assert "drift  docs/P.md: found at line 3, cited 30" in out


def test_non_git_root_reads_the_working_tree_and_rev_is_refused(home, tmp_path, capsys) -> None:
    root = tmp_path / "plain"
    (root / "docs").mkdir(parents=True)
    (root / "docs" / "P.md").write_text(DOC, encoding="utf-8")
    store.create_project("shop", "Shop", root=root)
    add_requirement("shop")
    _pass(capsys, f'"{QUOTE}" -- docs/P.md:3')
    rc, out, err = cli(capsys, "verify")
    assert rc == 0, err
    assert "verify (shop) (working tree): 1 passed criteria, 1 ok" in out
    rc, _out, err = cli(capsys, "verify", "--rev", "HEAD")
    assert rc == 1 and "needs a git work tree" in err


def test_needs_a_root(home, capsys) -> None:
    make_project()
    rc, _out, err = cli(capsys, "verify")
    assert rc == 1 and "project set shop root=DIR" in err


def test_json_output(repo, capsys) -> None:
    _pass(capsys, f'"{QUOTE}" -- docs/P.md:3')
    _pass(capsys, "free words")
    rc, out, _err = cli(capsys, "verify", "--json")
    payload = json.loads(out)
    assert rc == 0 and payload["ok"] is True and payload["rev"] == "HEAD"
    assert payload["counts"] == {"ok": 1, "free": 1}
    assert [row["verdict"] for row in payload["rows"]] == ["ok", "free"]


def test_not_run_and_waived_are_not_verified(repo, capsys) -> None:
    cli(capsys, "add", "A", "--req", "R1", "--text", "Given an untouched check then nothing")
    assert cli(capsys, "verify")[1].splitlines()[-1].startswith("verify (shop)@HEAD: 0 passed criteria")
