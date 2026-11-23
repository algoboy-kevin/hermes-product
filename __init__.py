"""product — product knowledge for a project (scaffold, card HP-0).

Registers one surface for now:

  * ``hermes product ...`` — the CLI (project add/list/use, add, set, link, unlink,
    show, list, trace, next, lint, graph, doc read, scan-prd, export, import);
    every verb is a loud stub until its implementing card lands.

Later cards add the tools, the skill and the system-prompt section, and the store.
No hooks, no network, no credentials. ``register(ctx)`` must never raise.
"""

from __future__ import annotations

import logging

_log = logging.getLogger(__name__)

CLI_NAME = "product"


def register(ctx) -> None:
    """Register the CLI. Nothing else yet. Never raise."""
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
