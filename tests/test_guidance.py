"""Guidance surfaces (R14 / A23): the prompt section, the skill and ``register(ctx)``.

The plugin registers two guidance surfaces: a read-only skill
(``product:using-product``) and one short system-prompt section that appears only
when the session's cwd sits inside a project's recorded root (A23: nothing outside,
at most 600 characters inside). Nothing here touches the real store: every test
gets its own ``HERMES_HOME``, and the prompt section must never create or migrate
the store it only reads.
"""

from __future__ import annotations

import importlib.util
import re
import sqlite3
import sys
from pathlib import Path

import pytest

import cli
import store

ROOT = Path(__file__).resolve().parent.parent
SKILL_FILE = ROOT / "skills" / "using-product" / "SKILL.md"
CLI_FILE = ROOT / "cli.py"
PLUGIN_NAME = "product_plugin_under_test"


def _plugin():
    """Load the plugin package the way Hermes does: ``__init__.py`` with relative imports.

    ``conftest.py`` puts the repo root on ``sys.path`` for the top-level modules, but
    ``__init__.py`` says ``from . import ...``, so it is imported as a package
    (submodule_search_locations points back at the repo root).
    """
    if PLUGIN_NAME in sys.modules:
        return sys.modules[PLUGIN_NAME]
    spec = importlib.util.spec_from_file_location(
        PLUGIN_NAME, ROOT / "__init__.py", submodule_search_locations=[str(ROOT)]
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[PLUGIN_NAME] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Every test gets its own HERMES_HOME (same shape as tests/_helpers.py ``home``)."""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes-home"))
    return tmp_path / "hermes-home"


@pytest.fixture()
def plugin(home: Path):
    return _plugin()


def _project(slug: str, name: str, root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    store.create_project(slug, name, root=root)


# --------------------------------------------------------------- the prompt section
def test_a23_outside_a_project_adds_nothing_to_the_prompt(
    plugin, home: Path, tmp_path: Path
) -> None:
    _project("shop", "Shop", tmp_path / "repo-shop")
    assert plugin._prompt_section({"cwd": str(tmp_path / "repo-beta")}) == ""
    assert plugin._prompt_section({"cwd": str(tmp_path / "repo-shop-sibling")}) == ""
    assert plugin._prompt_section({"cwd": str(tmp_path)}) == ""


def test_a23_inside_a_project_stays_within_the_budget(
    plugin, home: Path, tmp_path: Path
) -> None:
    root = tmp_path / "repo-shop"
    _project("shop", "Shop", root)
    text = plugin._prompt_section({"cwd": str(root / "src" / "pay")})
    assert text != ""
    assert "shop" in text                                        # the slug
    assert "hermes product trace" in text
    assert "hermes product next" in text
    assert "hermes product lint" in text
    assert len(text) <= 560                                      # budget
    assert len(text) <= 600                                      # A23's hard cap


def test_prompt_section_matches_the_root_itself(plugin, home: Path, tmp_path: Path) -> None:
    root = tmp_path / "repo-shop"
    _project("shop", "Shop", root)
    text = plugin._prompt_section({"cwd": str(root)})
    assert "shop" in text


def test_prompt_section_matches_a_subdirectory(plugin, home: Path, tmp_path: Path) -> None:
    root = tmp_path / "repo-shop"
    _project("shop", "Shop", root)
    nested = root / "src" / "deep" / "pkg"
    nested.mkdir(parents=True)
    text = plugin._prompt_section({"cwd": str(nested)})
    assert "shop" in text
    assert "hermes product next" in text


def test_prompt_section_is_silent_without_a_cwd(plugin, home: Path) -> None:
    assert plugin._prompt_section({}) == ""
    assert plugin._prompt_section({"cwd": ""}) == ""
    assert plugin._prompt_section({"cwd": None}) == ""
    assert plugin._prompt_section(None) == ""


def test_prompt_section_never_creates_the_db(plugin, home: Path, tmp_path: Path) -> None:
    missing_db = store.db_path()
    assert not missing_db.exists()
    assert plugin._prompt_section({"cwd": str(tmp_path / "repo")}) == ""
    assert not missing_db.exists()          # nothing was created just to ask


def test_prompt_section_is_silent_without_a_project_table(
    plugin, home: Path, tmp_path: Path
) -> None:
    db = store.db_path()
    db.parent.mkdir(parents=True)
    con = sqlite3.connect(str(db))
    con.execute("CREATE TABLE other (x TEXT)")
    con.commit()
    con.close()
    assert plugin._prompt_section({"cwd": str(tmp_path)}) == ""


def test_prompt_section_is_silent_on_a_stale_schema(plugin, home: Path, tmp_path: Path) -> None:
    """An older ``project`` shape (no ``root`` column) is read as 'no match', not migrated."""
    db = store.db_path()
    db.parent.mkdir(parents=True)
    con = sqlite3.connect(str(db))
    con.execute("CREATE TABLE project (slug TEXT PRIMARY KEY, name TEXT)")
    con.execute("INSERT INTO project (slug, name) VALUES ('old', 'Old')")
    con.commit()
    con.close()
    assert plugin._prompt_section({"cwd": str(tmp_path)}) == ""


def test_prompt_section_picks_the_longest_root(plugin, home: Path, tmp_path: Path) -> None:
    outer = tmp_path / "repo"
    inner = outer / "packages" / "engine"
    _project("outer", "Outer", outer)
    _project("inner", "Inner", inner)
    text = plugin._prompt_section({"cwd": str(inner / "src")})
    assert "inner" in text
    assert "Outer" not in text


def test_prompt_section_is_silent_on_identical_roots(plugin, home: Path, tmp_path: Path) -> None:
    root = tmp_path / "repo"
    _project("shop", "Shop", root)
    _project("blog", "Blog", root)
    assert plugin._prompt_section({"cwd": str(root / "src")}) == ""


def test_prompt_section_ignores_projects_without_root(plugin, home: Path, tmp_path: Path) -> None:
    _project("ghost", "Ghost", tmp_path / "elsewhere")
    store.create_project("rootless", "Rootless")                # root stays NULL
    assert plugin._prompt_section({"cwd": str(tmp_path / "anywhere")}) == ""

    root = tmp_path / "repo-shop"
    _project("shop", "Shop", root)                              # a rooted one, not ambiguous
    text = plugin._prompt_section({"cwd": str(root)})
    assert "shop" in text


def test_prompt_section_budget_holds_with_a_long_name(plugin, home: Path, tmp_path: Path) -> None:
    root = tmp_path / "repo"
    _project("shop", "N" * 200, root)
    text = plugin._prompt_section({"cwd": str(root)})
    assert len(text) <= 560
    assert "N" * 40 in text                                     # the name is truncated to 40
    assert "N" * 41 not in text


def test_prompt_section_never_writes_the_db(plugin, home: Path, tmp_path: Path) -> None:
    """Read-only proof: the db file's bytes and mtime are unchanged by the call."""
    root = tmp_path / "repo"
    _project("shop", "Shop", root)
    db = store.db_path()
    before = (db.read_bytes(), db.stat().st_mtime_ns)
    assert plugin._prompt_section({"cwd": str(root / "src")}) != ""
    after = (db.read_bytes(), db.stat().st_mtime_ns)
    assert after == before


# --------------------------------------------------------------- register(ctx)
class _FakeCtx:
    """Records what ``register(ctx)`` asks of a Hermes PluginContext."""

    def __init__(self, *, skills: bool = True, sections: bool = True) -> None:
        self.tools: list[dict] = []
        self.cli: list[dict] = []
        self.skills: list[dict] = []
        self.sections: list[dict] = []
        if not skills:
            self.register_skill = None
        if not sections:
            self.register_system_prompt_section = None

    def register_tool(self, **kwargs) -> None:
        self.tools.append(kwargs)

    def register_cli_command(self, **kwargs) -> None:
        self.cli.append(kwargs)

    def register_skill(self, name, path, description="", frontmatter=None) -> None:
        self.skills.append({"name": name, "path": Path(path), "description": description})

    def register_system_prompt_section(self, id, content, **kwargs) -> None:
        self.sections.append({"id": id, "content": content, **kwargs})


def test_register_tolerates_a_ctx_without_the_new_surfaces(plugin) -> None:
    ctx = _FakeCtx(skills=False, sections=False)
    plugin.register(ctx)                                           # must not raise
    assert len(ctx.cli) == 1
    assert ctx.skills == []
    assert ctx.sections == []


def test_register_adds_the_skill_and_the_prompt_section(plugin) -> None:
    ctx = _FakeCtx()
    plugin.register(ctx)
    assert len(ctx.cli) == 1                                       # CLI unchanged

    assert len(ctx.skills) == 1
    skill = ctx.skills[0]
    assert skill["name"] == "using-product"                        # bare name, no ':'
    assert skill["path"].is_file()
    assert skill["path"].name == "SKILL.md"
    assert skill["description"].startswith("Use when")
    assert len(skill["description"]) <= 60

    assert len(ctx.sections) == 1
    section = ctx.sections[0]
    assert section["id"] == "product"
    assert section["max_chars"] == 600
    assert callable(section["content"])
    assert section["content"]({"cwd": ""}) == ""


# --------------------------------------------------------------- the skill file
def test_skill_file_is_registrable() -> None:
    assert SKILL_FILE.is_file()
    text = SKILL_FILE.read_text(encoding="utf-8")
    assert text.startswith("---\n")

    header = text.split("---\n")[1]
    name = re.search(r"^name:\s*(.+)$", header, re.MULTILINE)
    description = re.search(r'^description:\s*\"?(.+?)\"?$', header, re.MULTILINE)
    assert name and name.group(1).strip() == "using-product"
    assert description
    desc = description.group(1).strip()
    assert desc.startswith("Use when")
    assert len(desc) <= 60
    assert len(text.splitlines()) <= 70


def test_skill_names_only_real_flags() -> None:
    """No invented flags: every option the skill names must exist in cli.py."""
    text = SKILL_FILE.read_text(encoding="utf-8")
    cli_source = CLI_FILE.read_text(encoding="utf-8")
    for flag in ("--project", "--section", "--max-chars"):
        assert flag in text, f"skill should mention {flag}"
        assert flag in cli_source, f"cli.py does not define {flag}"
    mentioned = set(re.findall(r"(?<![\w-])--[a-z][a-z-]*", text))
    invented = {flag for flag in mentioned if flag not in cli_source}
    assert not invented, f"skill mentions flags that cli.py does not define: {sorted(invented)}"
