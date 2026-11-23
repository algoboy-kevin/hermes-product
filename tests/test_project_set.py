"""`project set`: name and root can be changed without touching the database by hand."""
from __future__ import annotations

from pathlib import Path

import store
from _helpers import cli, home, make_project  # noqa: F401  (fixture)


def test_set_name_and_root(home: Path, capsys, tmp_path: Path) -> None:
    make_project("demo", "Wrong name")
    repo = tmp_path / "repo"
    repo.mkdir()
    rc, out, err = cli(capsys, "project", "set", "demo", "name=Right name", f"root={repo}")
    assert rc == 0, err
    assert out == "project demo updated: name, root\n"
    project = store.get_project("demo")
    assert project["name"] == "Right name" and project["root"] == str(repo.resolve())


def test_root_can_be_cleared(home: Path, capsys, tmp_path: Path) -> None:
    store.create_project("demo", "Demo", root=tmp_path)
    for clear in ("-", "none", ""):
        store.update_project("demo", {"root": str(tmp_path)})
        rc, _out, err = cli(capsys, "project", "set", "demo", f"root={clear}")
        assert rc == 0, err
        assert store.get_project("demo")["root"] is None


def test_refusals_change_nothing(home: Path, capsys) -> None:
    make_project("demo", "Demo")
    for argv, message in ((["slug=other"], "unknown field 'slug'"),
                          (["name=  "], "name must not be empty"),
                          (["nonsense"], "expected key=value")):
        rc, _out, err = cli(capsys, "project", "set", "demo", *argv)
        assert rc != 0 and message in err
    rc, _out, err = cli(capsys, "project", "set", "ghost", "name=X")
    assert rc != 0 and "does not exist" in err
    assert store.get_project("demo")["name"] == "Demo"
    assert store.get_project("ghost") is None


def test_slug_stays_the_key_for_every_row(home: Path, capsys) -> None:
    make_project("demo", "Demo")
    cli(capsys, "add", "S", "--title", "A spec here", "--summary", "What this spec decides, in full.",
        "--project", "demo")
    cli(capsys, "project", "set", "demo", "name=Renamed")
    assert store.get_entity("demo", "S1")["row"]["title"] == "A spec here"
