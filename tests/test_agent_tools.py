"""Agent-tool tests: the five read-only handlers over the ``product`` toolset.

Every command is also a tool returning the same compact lines as the CLI: the
handler builds the CLI's argv, parses it with the CLI's own parser and runs
``cli.product_command``. Handlers never raise — argparse exits and handler
exceptions become error results (``{"ok": false, "error", "exit_code"}``).
"""

from __future__ import annotations

import importlib.util
import json
import sys
import types
from pathlib import Path

import pytest
import ruamel.yaml

import agent_tools
import cli
import store
from agent_tools import (
    TOOLSET,
    TOOLS,
    handle_product_list,
    handle_product_lint,
    handle_product_next,
    handle_product_show,
    handle_product_trace,
)
from _helpers import (  # `home`/`con` are fixtures imported for discovery
    add_criterion,
    add_feature,
    add_requirement,
    add_spec,
    con,
    home,
    link_feature_requirement,
    link_spec_requirement,
    make_project,
    run,
)

ROOT = Path(__file__).resolve().parent.parent
PLUGIN_YAML = ROOT / "plugin.yaml"
PLUGIN_NAME = "product_plugin_under_test"

TOOL_NAMES = ["product_show", "product_trace", "product_next", "product_lint", "product_list"]
TOOL_PROPERTIES = {
    "product_show": {"ref", "project"},
    "product_trace": {"ref", "depth", "project"},
    "product_next": {"limit", "project"},
    "product_lint": {"project"},
    "product_list": {"kind", "status", "limit", "project"},
}
TOOL_REQUIRED = {
    "product_show": ["ref"],
    "product_trace": ["ref"],
    "product_next": [],
    "product_lint": [],
    "product_list": [],
}


@pytest.fixture()
def seeded(home, con):
    """A tmp store built with tests/_helpers.py: one requirement wired to everything."""
    make_project("shop", "Shop")
    add_requirement(project="shop", title="Pay by card")
    add_criterion(project="shop", requirement=1)
    add_feature(project="shop", title="Stripe integration")
    add_spec(project="shop", title="Payments")
    link_feature_requirement(con, "shop", 1, 1)
    link_spec_requirement(con, "shop", 1, 1)
    return "shop"


def _cli_call(capsys, argv: list[str]) -> tuple[int, str, str]:
    """Run the verb through ``cli.product_command`` (via _helpers.run) and capture it."""
    rc = run(list(argv))
    captured = capsys.readouterr()
    return rc, captured.out, captured.err


# ------------------------------------------------------------------ table shape
def test_tool_table_shape() -> None:
    assert TOOLSET == "product"
    assert [name for name, *_ in TOOLS] == TOOL_NAMES
    for name, schema, handler, emoji in TOOLS:
        assert schema["name"] == name
        assert schema["parameters"]["type"] == "object"
        assert callable(handler)
        assert emoji


def test_schemas_are_well_formed() -> None:
    for name, schema, _handler, _emoji in TOOLS:
        assert set(schema) == {"name", "description", "parameters"}
        assert isinstance(schema["description"], str) and schema["description"].strip()
        params = schema["parameters"]
        assert isinstance(params["properties"], dict)
        assert set(params["properties"]) == TOOL_PROPERTIES[name]
        assert params.get("required", []) == TOOL_REQUIRED[name]
        assert set(params.get("required", [])) <= set(params["properties"])
        for prop in params["properties"].values():
            assert prop.get("type"), "every property needs a type"
            assert isinstance(prop.get("description"), str) and prop["description"].strip()


def test_provides_tools_matches_plugin_yaml() -> None:
    yaml = ruamel.yaml.YAML(typ="safe")
    with PLUGIN_YAML.open(encoding="utf-8") as fh:
        data = yaml.load(fh)
    assert data["provides_tools"] == TOOL_NAMES


# ------------------------------------------------------------- CLI parity (S4)
def test_show_matches_the_cli(seeded, capsys) -> None:
    rc, out, err = _cli_call(capsys, ["show", "R1"])
    assert rc == 0 and err == ""
    payload = json.loads(handle_product_show({"ref": "R1"}))
    assert payload["ok"] is True
    assert payload["output"] == out


def test_trace_matches_the_cli(seeded, capsys) -> None:
    rc, out, err = _cli_call(capsys, ["trace", "R1", "--depth", "2"])
    assert rc == 0 and err == ""
    payload = json.loads(handle_product_trace({"ref": "R1", "depth": 2}))
    assert payload["ok"] is True
    assert payload["output"] == out


def test_next_matches_the_cli(seeded, capsys) -> None:
    rc, out, err = _cli_call(capsys, ["next"])
    assert rc == 0 and err == ""
    payload = json.loads(handle_product_next({}))
    assert payload["ok"] is True
    assert payload["output"] == out


def test_list_matches_the_cli(seeded, capsys) -> None:
    rc, out, err = _cli_call(capsys, ["list", "R"])
    assert rc == 0 and err == ""
    payload = json.loads(handle_product_list({"kind": "R"}))
    assert payload["ok"] is True
    assert payload["output"] == out


def test_project_flag_selects_the_project(home, con, capsys) -> None:
    make_project("shop", "Shop")
    make_project("blog", "Blog")
    add_requirement(project="shop", title="Pay by card")
    add_requirement(project="blog", title="Write posts", priority="should")
    rc, out, _err = _cli_call(capsys, ["list", "--project", "blog"])
    assert rc == 0
    payload = json.loads(handle_product_list({"project": "blog"}))
    assert payload["ok"] is True
    assert payload["output"] == out
    assert "Write posts" in payload["output"]
    assert "Pay by card" not in payload["output"]


# ------------------------------------------------------------------- error results
def test_unknown_ref_is_an_error_result(seeded, capsys) -> None:
    rc, _out, err = _cli_call(capsys, ["show", "R999"])
    assert rc == 1 and err
    payload = json.loads(handle_product_show({"ref": "R999"}))
    assert payload["ok"] is False
    assert payload["error"] == err                    # the CLI's own message
    assert payload["exit_code"] == rc


def test_argparse_error_is_an_error_result(seeded) -> None:
    payload = json.loads(handle_product_trace({"ref": "R1", "depth": "x"}))
    assert payload["ok"] is False                     # an error result, not an exception
    assert "depth" in payload["error"]
    assert payload["exit_code"] == 2


def test_handlers_never_raise_when_product_command_raises(monkeypatch) -> None:
    def boom(args):
        raise RuntimeError("boom")

    monkeypatch.setattr(cli, "product_command", boom)
    results = [
        handle_product_show({"ref": "R1"}),
        handle_product_trace({"ref": "R1", "depth": 1}),
        handle_product_next({}),
        handle_product_lint({}),
        handle_product_list({}),
    ]
    for result in results:
        payload = json.loads(result)
        assert payload["ok"] is False
        assert "RuntimeError" in payload["error"]


# ------------------------------------------------------------- lint delegation
def test_lint_delegates_to_the_cli(monkeypatch) -> None:
    recorded = []

    def fake(args):                                   # record argv and return 0
        recorded.append([args.product_action, *(getattr(args, "rest", None) or [])])
        return 0

    monkeypatch.setattr(cli, "product_command", fake)
    payload = json.loads(handle_product_lint({}))
    assert payload["ok"] is True
    assert recorded == [["lint"]]


def test_lint_forwards_the_project_flag(monkeypatch) -> None:
    # The argv is observed at the runner boundary (independent of the lint parser).
    captured: list[list[str]] = []
    monkeypatch.setattr(
        agent_tools, "run_cli",
        lambda argv: captured.append(list(argv)) or json.dumps({"ok": True, "output": ""}),
    )
    handle_product_lint({})
    handle_product_lint({"project": "x"})
    assert captured == [["lint"], ["lint", "--project", "x"]]


# ----------------------------------------------------------------- registration
class _ToolCtx:
    """Minimal Hermes PluginContext: records tools and the CLI."""

    def __init__(self) -> None:
        self.tools: list[dict] = []
        self.cli: list[dict] = []

    def register_tool(self, **kwargs) -> None:
        self.tools.append(kwargs)

    def register_cli_command(self, **kwargs) -> None:
        self.cli.append(kwargs)


def _plugin():
    """Load the plugin package the way Hermes does (see tests/test_scaffold.py)."""
    if PLUGIN_NAME in sys.modules:
        return sys.modules[PLUGIN_NAME]
    spec = importlib.util.spec_from_file_location(
        PLUGIN_NAME, ROOT / "__init__.py", submodule_search_locations=[str(ROOT)]
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[PLUGIN_NAME] = module
    spec.loader.exec_module(module)
    return module


def test_register_registers_five_tools() -> None:
    plugin = _plugin()
    ctx = _ToolCtx()
    plugin.register(ctx)                           # must not raise
    assert [t["name"] for t in ctx.tools] == TOOL_NAMES
    # Identity against the package's own TOOLS: the plugin loads `agent_tools` as
    # its own submodule (relative import), a different module object than the
    # top-level `agent_tools` import above.
    for (name, schema, handler, emoji), registered in zip(plugin.agent_tools.TOOLS, ctx.tools):
        assert registered["toolset"] == "product"
        assert registered["schema"] is schema
        assert registered["handler"] is handler
        assert registered["description"] == schema["description"]
        assert registered["emoji"] == emoji


# ------------------------------------------- isolation from foreign top-level modules
IMPORT_NAMES = ("store", "graph", "model", "cli", "plan", "export", "docs_text", "scan",
                "agent_tools")


def _imposter(name: str) -> types.ModuleType:
    module = types.ModuleType(name)
    module.__file__ = f"/elsewhere/feature-graph/{name}.py"   # another plugin's module
    return module


@pytest.fixture()
def isolated(monkeypatch, tmp_path: Path):
    """The plugin loaded AS A PACKAGE with look-alike modules squatting on the
    top-level names another plugin also ships (same regression as
    tests/test_plugin_api_isolation.py)."""
    for name in IMPORT_NAMES:
        monkeypatch.setitem(sys.modules, name, _imposter(name))
    # the plugin root must NOT rescue us through sys.path either
    monkeypatch.setattr(sys, "path", [p for p in sys.path if p != str(ROOT)])
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))
    name = "product_plugin_isolation_under_test"
    spec = importlib.util.spec_from_file_location(
        name, ROOT / "__init__.py", submodule_search_locations=[str(ROOT)]
    )
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, name, module)
    spec.loader.exec_module(module)
    ctx = _ToolCtx()
    module.register(ctx)
    return module, ctx


def test_isolation_registers_with_imposter_modules(isolated) -> None:
    _module, ctx = isolated
    assert [t["name"] for t in ctx.tools] == TOOL_NAMES
    assert [c["name"] for c in ctx.cli] == ["product"]


def test_isolation_handler_runs_and_imposters_are_untouched(isolated) -> None:
    module, ctx = isolated
    pkg_store = sys.modules[f"{module.__name__}.store"]   # the package's own store
    pkg_store.create_project("shop", "Shop")
    handler = next(t["handler"] for t in ctx.tools if t["name"] == "product_next")
    payload = json.loads(handler({"limit": 5}))
    assert payload["ok"] is True
    assert "next (shop)" in payload["output"]
    for name in IMPORT_NAMES:
        assert sys.modules[name].__file__.startswith("/elsewhere/"), name
