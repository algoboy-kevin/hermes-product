"""CLI tests for `hermes product project add/list/use` (card HP-1).

Output is pinned exactly: add -> ``project <slug> created``; list -> one line
per project ``<slug>  <name>  <root or ->`` with ``*`` before the active one's
slug; use -> ``using <slug>``. StoreError goes to stderr with exit 1. Every
test runs against a tmp HERMES_HOME (monkeypatch.setenv).
"""
from __future__ import annotations

import argparse
from pathlib import Path

import pytest

import store
from cli import product_command, register_cli


@pytest.fixture(autouse=True)
def _hermes_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes-home"))


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="hermes product")
    register_cli(parser)
    return parser


def _run(argv: list) -> int:
    return product_command(_parser().parse_args(argv))


def test_add_creates_the_project_and_list_shows_it(capsys) -> None:
    rc = _run(["project", "add", "shop", "Shop"])
    captured = capsys.readouterr()
    assert rc == 0
    assert captured.out == "project shop created\n"
    rc = _run(["project", "list"])
    captured = capsys.readouterr()
    assert rc == 0
    assert captured.out == "shop  Shop  -\n"


def test_add_with_root_shows_the_absolute_root(tmp_path: Path, capsys) -> None:
    root = tmp_path / "code"
    assert _run(["project", "add", "shop", "Shop", "--root", str(root)]) == 0
    capsys.readouterr()
    assert _run(["project", "list"]) == 0
    assert capsys.readouterr().out == f"shop  Shop  {root.resolve()}\n"


def test_id_allocated_for_shop_is_r1_and_only_in_shop(capsys) -> None:
    assert _run(["project", "add", "shop", "Shop"]) == 0
    assert _run(["project", "add", "other", "Other"]) == 0
    capsys.readouterr()
    con = store.connect()
    try:
        assert store.next_id(con, "shop", "R") == 1
        con.execute("INSERT INTO requirement(project, id) VALUES ('shop', 1)")
        con.commit()
        rows = con.execute("SELECT project FROM requirement WHERE id = 1").fetchall()
        assert [row["project"] for row in rows] == ["shop"]     # only in shop
        assert store.next_id(con, "other", "R") == 1            # per-project counter
    finally:
        con.close()


def test_duplicate_slug_exits_1_and_project_count_unchanged(capsys) -> None:
    assert _run(["project", "add", "shop", "Shop"]) == 0
    capsys.readouterr()
    rc = _run(["project", "add", "shop", "Second"])
    captured = capsys.readouterr()
    assert rc == 1
    assert "already exists" in captured.err
    assert captured.out == ""
    assert len(store.list_projects()) == 1


def test_invalid_slug_exits_1(capsys) -> None:
    rc = _run(["project", "add", "Bad_Slug", "X"])
    captured = capsys.readouterr()
    assert rc == 1
    assert "^[a-z0-9][a-z0-9-]{0,39}$" in captured.err
    assert store.list_projects() == []


def test_use_marks_the_project_in_list(capsys) -> None:
    assert _run(["project", "add", "shop", "Shop"]) == 0
    assert _run(["project", "add", "other", "Other"]) == 0
    capsys.readouterr()
    rc = _run(["project", "use", "shop"])
    captured = capsys.readouterr()
    assert rc == 0
    assert captured.out == "using shop\n"
    assert _run(["project", "list"]) == 0
    assert capsys.readouterr().out == "other  Other  -\n*shop  Shop  -\n"


def test_use_missing_project_exits_1(capsys) -> None:
    assert _run(["project", "add", "shop", "Shop"]) == 0
    capsys.readouterr()
    rc = _run(["project", "use", "ghost"])
    captured = capsys.readouterr()
    assert rc == 1
    assert "project 'ghost' does not exist (known: shop)" in captured.err
