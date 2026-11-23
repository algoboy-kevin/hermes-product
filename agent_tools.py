"""Agent-facing tools over the product store: ``product_show`` / ``product_trace``
/ ``product_next`` / ``product_lint`` / ``product_list`` (card HP-9).

Every command is also a tool and returns the SAME compact lines as the CLI: one
generic runner builds the CLI's argv, parses it with the parser ``cli.register_cli``
builds and runs ``cli.product_command`` with stdout/stderr captured. The result
follows the feature-graph convention: ``{"ok": true, "output": <stdout>}`` on
exit 0, ``{"ok": false, "error": <stderr or stdout>, "exit_code": N}`` otherwise.
Handlers never raise: argparse's SystemExit and any handler exception become
error results. Unknown or stubbed verbs surface the CLI's own message untouched.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
from typing import Any

try:  # package submodule (Hermes plugin loader)
    from . import cli, store
except ImportError:  # top-level module (test suite)
    import cli  # type: ignore[no-redef]
    import store  # type: ignore[no-redef]

TOOLSET = "product"

_REF_PROP = {
    "type": "string",
    "description": "Entry ref: R3, A21, F7, S2, DOC1 or F:slug.",
}

_PROJECT_PROP = {
    "type": "string",
    "description": (
        "Project slug (default: the project whose root contains the working "
        "directory; `hermes product project list` shows them)."
    ),
}

PRODUCT_SHOW_SCHEMA = {
    "name": "product_show",
    "description": (
        "Full detail for one entry (fields, links, child criteria, docs, history) "
        "— the same lines as `hermes product show`. Use product_trace for "
        "everything connected to the entry, product_list for one line per entry."
    ),
    "parameters": {
        "type": "object",
        "properties": {"ref": _REF_PROP, "project": _PROJECT_PROP},
        "required": ["ref"],
    },
}

PRODUCT_TRACE_SCHEMA = {
    "name": "product_trace",
    "description": (
        "For one entry, everything connected: requirement -> features, specs, "
        "criteria, docs, cards (feature/spec -> requirements, needs, docs) — the "
        "same lines as `hermes product trace`. Call it BEFORE reading or grepping "
        "documents for what an entry touches."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "ref": _REF_PROP,
            "depth": {
                "type": "integer",
                "description": "With 2: expand each linked R/F/S one more hop (default 1).",
            },
            "project": _PROJECT_PROP,
        },
        "required": ["ref"],
    },
}

PRODUCT_NEXT_SCHEMA = {
    "name": "product_next",
    "description": (
        "What to work on next: unblocked, highest priority, dependencies met — "
        "plus what blocks the rest — the same lines as `hermes product next`. "
        "Call it before picking work."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "limit": {"type": "integer", "description": "At most N lines per list (default 10)."},
            "project": _PROJECT_PROP,
        },
        "required": [],
    },
}

PRODUCT_LINT_SCHEMA = {
    "name": "product_lint",
    "description": (
        "All checks in S2: what is inconsistent between rows, links and documents "
        "— the same lines as `hermes product lint`. Call it before trusting "
        "consistency."
    ),
    "parameters": {
        "type": "object",
        "properties": {"project": _PROJECT_PROP},
        "required": [],
    },
}

PRODUCT_LIST_SCHEMA = {
    "name": "product_list",
    "description": (
        "One compact line per entry (ref, status, priority/layer, phase, title, "
        "links, criteria progress) — the same lines as `hermes product list`. "
        "Use product_show for one entry's full detail."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "kind": {
                "type": "string",
                "enum": ["R", "A", "F", "S", "DOC"],
                "description": "Only this kind (default: R, then F, then S, then A).",
            },
            "status": {"type": "string", "description": "Only entries in this state."},
            "limit": {"type": "integer", "description": "At most N lines."},
            "project": _PROJECT_PROP,
        },
        "required": [],
    },
}


# ------------------------------------------------------------------ generic runner
def _ok(output: str) -> str:
    return json.dumps({"ok": True, "output": output}, indent=2)


def _fail(message: str, exit_code: int) -> str:
    return json.dumps({"ok": False, "error": message, "exit_code": exit_code}, indent=2)


def _exit_result(exc: SystemExit, out: io.StringIO, err: io.StringIO) -> str:
    code = exc.code if isinstance(exc.code, int) else 1
    text = err.getvalue() or out.getvalue()
    return _ok(text) if code == 0 else _fail(text, code)


def run_cli(argv: list[str]) -> str:
    """Parse *argv* with the CLI's own parser and run ``cli.product_command``.

    The single generic runner behind every tool. Never raises.
    """
    out, err = io.StringIO(), io.StringIO()
    try:
        parser = argparse.ArgumentParser(prog="hermes product")
        cli.register_cli(parser)
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                args = parser.parse_args(argv)
            except SystemExit as exc:               # argparse errors and --help
                return _exit_result(exc, out, err)
            code = cli.product_command(args)
    except SystemExit as exc:                       # a verb exiting is an error result
        return _exit_result(exc, out, err)
    except Exception as exc:  # noqa: BLE001 - a tool call must never raise
        return _fail(f"{type(exc).__name__}: {exc}", 1)
    if code == 0:
        return _ok(out.getvalue())
    return _fail(err.getvalue() or out.getvalue(), code)


# ------------------------------------------------------------- thin per-tool wrappers
def _project_flag(args: dict[str, Any]) -> list[str]:
    project = args.get("project")
    return ["--project", str(project)] if project else []


def _argv_for_show(args: dict[str, Any]) -> list[str]:
    return ["show", str(args.get("ref") or "")] + _project_flag(args)


def _argv_for_trace(args: dict[str, Any]) -> list[str]:
    argv = ["trace", str(args.get("ref") or "")]
    if args.get("depth") is not None:
        argv += ["--depth", str(args["depth"])]
    return argv + _project_flag(args)


def _argv_for_next(args: dict[str, Any]) -> list[str]:
    argv = ["next"]
    if args.get("limit") is not None:
        argv += ["--limit", str(args["limit"])]
    return argv + _project_flag(args)


def _argv_for_lint(args: dict[str, Any]) -> list[str]:
    return ["lint"] + _project_flag(args)


def _argv_for_list(args: dict[str, Any]) -> list[str]:
    argv = ["list"]
    if args.get("kind"):
        argv.append(str(args["kind"]))
    if args.get("status"):
        argv += ["--status", str(args["status"])]
    if args.get("limit") is not None:
        argv += ["--limit", str(args["limit"])]
    return argv + _project_flag(args)


def handle_product_show(args: dict[str, Any]) -> str:
    return run_cli(_argv_for_show(args))


def handle_product_trace(args: dict[str, Any]) -> str:
    return run_cli(_argv_for_trace(args))


def handle_product_next(args: dict[str, Any]) -> str:
    return run_cli(_argv_for_next(args))


def handle_product_lint(args: dict[str, Any]) -> str:
    return run_cli(_argv_for_lint(args))


def handle_product_list(args: dict[str, Any]) -> str:
    return run_cli(_argv_for_list(args))


TOOLS = (
    ("product_show", PRODUCT_SHOW_SCHEMA, handle_product_show, "📖"),
    ("product_trace", PRODUCT_TRACE_SCHEMA, handle_product_trace, "🧵"),
    ("product_next", PRODUCT_NEXT_SCHEMA, handle_product_next, "🧭"),
    ("product_lint", PRODUCT_LINT_SCHEMA, handle_product_lint, "🧹"),
    ("product_list", PRODUCT_LIST_SCHEMA, handle_product_list, "📃"),
)
