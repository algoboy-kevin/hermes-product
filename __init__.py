"""product — product knowledge for a project.

Registers four surfaces:

  * ``hermes product ...`` — the CLI (project add/list/use, add, set, link, unlink,
    show, list, trace, next, lint, graph, doc read, scan-prd, export, import);
  * five read-only tools in the ``product`` toolset, so an agent in a session can
    query the store directly: ``product_show``, ``product_trace``, ``product_next``,
    ``product_lint``, ``product_list``;
  * one read-only skill, ``product:using-product`` — when to call trace/next/lint
    before reading or grepping documents, and the rules the rows encode;
  * one short system-prompt section (``product``) that appears only when the
    session's cwd is inside a project's recorded root, and names that skill.

No hooks, no network, no credentials. The prompt section never creates or
migrates the store. ``register(ctx)`` must never raise.
"""

from __future__ import annotations
import logging
import sqlite3
from pathlib import Path
from urllib.parse import quote

_log = logging.getLogger(__name__)
CLI_NAME = "product"
SKILL_NAME = "using-product"
SKILL_DESCRIPTION = "Use when a repo has a product store (R#/F#/S#) or plan work."
PROMPT_SECTION_ID = "product"
PROMPT_SECTION_MAX_CHARS = 600
PROMPT_SECTION_BUDGET = 560


def _prompt_section(session_info) -> str:
    """One line of guidance when the session's cwd is inside a project's root.

    Must never create or migrate the store, and never raise: the renderer treats an
    empty string (or an exception) as "no section". The containment rule is
    ``store.resolve_project``'s: both sides resolved, ``is_relative_to``, nested
    roots pick the longest; two projects over the same root are ambiguous and stay
    silent.
    """
    try:
        from . import store
        cwd = str((session_info or {}).get("cwd") or "")
        if not cwd:
            return ""
        db_file = store.db_path()
        if not db_file.is_file():          # never create the db just to ask about it
            return ""
        here = Path(cwd).expanduser().resolve()
        # Read-only connection: the section must never create, migrate or write the
        # store (store.connect() would apply pending migrations at session start).
        con = sqlite3.connect(f"file:{quote(str(db_file.resolve()))}?mode=ro", uri=True)
        try:
            rows = con.execute(
                "SELECT slug, name, root FROM project WHERE root IS NOT NULL AND root != ''"
            ).fetchall()
        finally:
            con.close()
        matches: list[tuple[int, str, str]] = []   # (root length, slug, name)
        for slug, name, root in rows:
            root_path = Path(root).expanduser().resolve()
            if here.is_relative_to(root_path):
                matches.append((len(str(root_path)), str(slug), str(name)))
        if not matches:
            return ""
        longest = max(length for length, _slug, _name in matches)
        best = [match for match in matches if match[0] == longest]
        if len(best) != 1:                 # ambiguous roots: stay silent
            return ""
        _length, slug, name = best[0]
        text = (
            f'Product knowledge for "{slug}" ({name[:40]}) lives in the product'
            " store, not in docs. Before grepping or reading files run:"
            " hermes product trace <REF> (everything connected to an entry),"
            " hermes product next (what to work on),"
            " hermes product lint (what is inconsistent)."
            " Status lives in rows, never in documents; verified only from criteria."
            " Skill: product:using-product."
        )
        return text[:PROMPT_SECTION_BUDGET]
    except Exception:  # noqa: BLE001 - guidance must never break session start
        _log.debug("product: prompt section skipped", exc_info=True)
        return ""


def register(ctx) -> None:
    """Register the CLI, the read-only tools, the skill and the prompt section. Never raise."""
    try:
        from .cli import product_command, register_cli
    except Exception:  # pragma: no cover - defensive, import-time only
        _log.exception("product: could not import its modules; nothing registered")
        return

    try:
        ctx.register_cli_command(
            name=CLI_NAME,
            help="Product knowledge: requirements, features, specs and docs as linked entries",
            setup_fn=register_cli,
            handler_fn=product_command,
            description=(
                "Read and write a project's product knowledge (R#/A#/F#/S#/DOC# "
                "entries): create and update entries, link them, trace connections, "
                "pick the next work, lint the store and export/import plain text. "
                "Scaffold card HP-0: every verb is a stub."
            ),
        )
    except Exception:  # pragma: no cover - a failure must not kill plugin loading
        _log.exception("product: CLI command %s not registered", CLI_NAME)

    try:
        from .agent_tools import TOOLS, TOOLSET
        for name, schema, handler, emoji in TOOLS:
            try:
                ctx.register_tool(
                    name=name,
                    toolset=TOOLSET,
                    schema=schema,
                    handler=handler,
                    description=schema["description"],
                    emoji=emoji,
                )
            except Exception:  # pragma: no cover - a name clash must not kill the CLI
                _log.exception("product: tool %s not registered", name)
    except Exception:  # pragma: no cover - defensive, import-time only
        _log.exception("product: tools not registered")

    # Opt-in surfaces newer Hermes versions offer; older ones simply lack the method.
    register_skill = getattr(ctx, "register_skill", None)
    if callable(register_skill):
        try:
            register_skill(
                SKILL_NAME,
                Path(__file__).resolve().parent / "skills" / SKILL_NAME / "SKILL.md",
                SKILL_DESCRIPTION,
            )
        except Exception:  # pragma: no cover - diagnostics only, never fatal
            _log.exception("product: skill %s not registered", SKILL_NAME)

    register_section = getattr(ctx, "register_system_prompt_section", None)
    if callable(register_section):
        try:
            register_section(PROMPT_SECTION_ID, _prompt_section, max_chars=PROMPT_SECTION_MAX_CHARS)
        except Exception:  # pragma: no cover - diagnostics only, never fatal
            _log.exception("product: system prompt section not registered")
