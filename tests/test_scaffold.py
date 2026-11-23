"""Scaffold tests for the `product` plugin (card HP-0).

What is pinned here, and only here:

* the manifest fields Hermes and the catalog read (plugin.yaml);
* ``register(ctx)`` touching the CLI command plus, since HP-9, the five tools,
  the skill and the prompt section — and never raising;
* every CLI verb path of the S4 command table being implemented (no verb is a
  loud stub any more since HP-8);
* the CLI verb list staying in sync with the S4 command table.

No test reads docs/: the expected verb names are transcribed into constants below
(a later card imports the product documents into the plugin's store).
"""

from __future__ import annotations

import argparse
import importlib.util
import sys
from pathlib import Path

import pytest
import ruamel.yaml

from cli import IMPLEMENTED, VERB_CARDS, product_command, register_cli

ROOT = Path(__file__).resolve().parent.parent
PLUGIN_YAML = ROOT / "plugin.yaml"
PLUGIN_NAME = "product_plugin_under_test"

# Verb path -> implementing card, as specified by the card HP-0 brief.
# Kept independent of cli.VERB_CARDS on purpose: the stub must print these.
EXPECTED_VERB_CARDS = {
    "project add": "HP-1",
    "project list": "HP-1",
    "project use": "HP-1",
    "project set": "HP-17",
    "add": "HP-2",
    "set": "HP-2",
    "show": "HP-2",
    "list": "HP-2",
    "link": "HP-3",
    "unlink": "HP-3",
    "graph": "HP-3",
    "doc read": "HP-4",
    "export": "HP-5",
    "import": "HP-5",
    "trace": "HP-6",
    "next": "HP-6",
    "lint": "HP-7",
    "scan-prd": "HP-8",
    # HP-10 and HP-12 add two verbs that are not in S4's table (S6 and S5 define them)
    "import-md": "HP-10",
    "sync": "HP-12",
    # HP-16: the one-time bridge from schema-v1 spec DOCs to schema-v2 S# bodies (DOC5 section 7)
    "fold-docs": "HP-16",
    # HP-18: evidence quotes are checked against a git revision (DOC5 section 2.6)
    "verify": "HP-18",
}

# The command table of docs/specs/S4-tool-behavior.md section 2, transcribed:
# "project add / list / use", "add", "set", "link / unlink", "show", "trace",
# "next", "lint", "graph", "doc read", "scan-prd", "export / import".
S4_VERB_PATHS = {
    "project add", "project list", "project use",
    "add", "set", "link", "unlink", "show",
    "trace", "next", "lint", "graph",
    "doc read", "scan-prd", "export", "import",
}


def _plugin():
    """Load the plugin package the way Hermes does: ``__init__.py`` with relative imports.

    ``conftest.py`` puts the repo root on ``sys.path`` for the top-level modules, but
    ``__init__.py`` says ``from .cli import ...``, so it is imported as a package
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


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="hermes product")
    register_cli(parser)
    return parser


# ------------------------------------------------------------------ a. the manifest
def test_plugin_yaml_manifest() -> None:
    yaml = ruamel.yaml.YAML(typ="safe")
    with PLUGIN_YAML.open(encoding="utf-8") as fh:
        data = yaml.load(fh)
    assert data["name"] == "product"
    assert data["version"]
    assert data["kind"]
    assert data["requires_hermes"]


# ------------------------------------------------------------------ b. register(ctx)
class _FakeCtx:
    """Records what ``register(ctx)`` asks of a Hermes PluginContext."""

    def __init__(self) -> None:
        self.tools: list[dict] = []
        self.cli: list[dict] = []
        self.skills: list[dict] = []
        self.sections: list[dict] = []

    def register_tool(self, **kwargs) -> None:
        self.tools.append(kwargs)

    def register_cli_command(self, **kwargs) -> None:
        self.cli.append(kwargs)

    def register_skill(self, *args, **kwargs) -> None:
        self.skills.append({"args": args, **kwargs})

    def register_system_prompt_section(self, *args, **kwargs) -> None:
        self.sections.append({"args": args, **kwargs})


def test_register_adds_the_cli_command_tools_skill_and_section() -> None:
    ctx = _FakeCtx()
    plugin = _plugin()
    plugin.register(ctx)                                           # must not raise

    assert len(ctx.cli) == 1
    assert ctx.cli[0]["name"] == "product"
    # The package loads its own `cli` submodule (relative import), a different
    # module object than the top-level `cli` import: compare against that one.
    assert ctx.cli[0]["setup_fn"] is plugin.cli.register_cli
    assert ctx.cli[0]["handler_fn"] is plugin.cli.product_command
    # HP-9 surface (was: "nothing else is registered yet"): five read-only
    # tools, one skill and one system-prompt section alongside the CLI.
    assert [t["name"] for t in ctx.tools] == [
        "product_show", "product_trace", "product_next", "product_lint", "product_list",
    ]
    assert all(t["toolset"] == "product" for t in ctx.tools)
    assert len(ctx.skills) == 1
    skill_name, skill_path, skill_desc = ctx.skills[0]["args"]
    assert skill_name == "using-product"
    assert Path(skill_path).is_file()
    assert skill_desc.startswith("Use when") and len(skill_desc) <= 60
    assert len(ctx.sections) == 1
    section_id, section_fn = ctx.sections[0]["args"]
    assert section_id == "product"
    assert callable(section_fn)
    assert ctx.sections[0]["max_chars"] == 600


def test_register_never_raises_when_the_ctx_raises() -> None:
    class _BoomCtx(_FakeCtx):
        def register_cli_command(self, **kwargs) -> None:
            raise RuntimeError("boom")

    _plugin().register(_BoomCtx())                                  # must not raise


# ---------------------------------------------------------- c. every verb is real
def test_every_verb_path_is_implemented() -> None:
    # HP-8 closes the last stub: no verb of the S4 table may still print
    # `NOT IMPLEMENTED` and exit 2.
    assert set(EXPECTED_VERB_CARDS) <= IMPLEMENTED


# ------------------------------------------------------------------ d. help output
def test_help_lists_every_top_level_verb() -> None:
    text = _build_parser().format_help()
    top_level = {path.split()[0] for path in EXPECTED_VERB_CARDS}
    for verb in top_level:
        assert verb in text


# ------------------------------------------------------------------ e. S4 coverage
def test_s4_command_table_is_fully_covered() -> None:
    # Verb names come from the constant in cli.py, not from docs/.
    assert S4_VERB_PATHS <= set(VERB_CARDS)
    assert set(VERB_CARDS) == set(EXPECTED_VERB_CARDS)


# ------------------------------------------- extra: the rest of the HP-0 cli contract
def test_no_verb_prints_help_and_returns_zero(capsys) -> None:
    args = _build_parser().parse_args([])
    rc = product_command(args)
    captured = capsys.readouterr()
    assert rc == 0
    assert "usage:" in captured.out


def test_extra_args_are_now_refused_by_a_real_verb() -> None:
    # Every verb parses its args strictly since HP-8: an unknown argument is an
    # argparse error (SystemExit 2), never silently ignored.
    with pytest.raises(SystemExit) as excinfo:
        _build_parser().parse_args(["scan-prd", "extra", "words"])
    assert excinfo.value.code == 2
