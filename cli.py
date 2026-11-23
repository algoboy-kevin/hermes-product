"""The ``hermes product`` CLI (scaffold, card HP-0).

``register_cli(subparser)`` builds the argparse tree with every verb of
docs/specs/S4-tool-behavior.md section 2; ``product_command(args)`` makes every
verb a loud stub: one stderr line ``NOT IMPLEMENTED: <verb path> (card HP-<n>)``
and exit code 2. With no verb it prints help and returns 0.

Every verb swallows trailing arguments (``argparse.REMAINDER``) so a later card
can add real options without breaking the scaffold tests; no option names are
invented here. The verb -> implementing-card map lives in ``VERB_CARDS`` alone.
"""

from __future__ import annotations

import argparse
import sys

EXIT_OK = 0
EXIT_NOT_IMPLEMENTED = 2

# The one verb-path -> implementing-card map. HP numbers come from the HP-0 brief.
VERB_CARDS: dict[str, str] = {
    "project add": "HP-1",
    "project list": "HP-1",
    "project use": "HP-1",
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
}

_TOP_LEVEL = tuple(dict.fromkeys(path.split()[0] for path in VERB_CARDS))


def _add_tail(parser: argparse.ArgumentParser) -> None:
    """Accept and ignore anything after the verb (later cards add real options)."""
    parser.add_argument("rest", nargs=argparse.REMAINDER, help=argparse.SUPPRESS)


def register_cli(subparser: argparse.ArgumentParser) -> None:
    """Build the argparse tree for every S4 verb. Help wording follows S4 section 2."""
    subs = subparser.add_subparsers(dest="product_action", metavar="{%s}" % ",".join(_TOP_LEVEL))

    p_project = subs.add_parser(
        "project",
        help="Create a project, list projects, pick one. Without --project, the "
             "project whose root contains the working directory is used",
    )
    psubs = p_project.add_subparsers(dest="project_action", metavar="{add,list,use}")
    p_pa = psubs.add_parser("add", help="Create a project")
    _add_tail(p_pa)
    p_pl = psubs.add_parser("list", help="List projects")
    _add_tail(p_pl)
    p_pu = psubs.add_parser("use", help="Pick a project")
    _add_tail(p_pu)

    p_add = subs.add_parser("add", help="Create an R#, A#, F#, S# or DOC#")
    _add_tail(p_add)
    p_set = subs.add_parser("set", help="Change a field or status (validated)")
    _add_tail(p_set)
    p_link = subs.add_parser("link", help="Create a relation, or add a dependency edge")
    _add_tail(p_link)
    p_unlink = subs.add_parser("unlink", help="Remove a relation, or remove a dependency edge")
    _add_tail(p_unlink)
    p_show = subs.add_parser("show", help="Full detail for one entry")
    _add_tail(p_show)
    p_list = subs.add_parser("list", help="One line per item by default")
    _add_tail(p_list)
    p_trace = subs.add_parser(
        "trace",
        help="For one entry, everything connected: requirement -> features, specs, "
             "criteria, docs, cards",
    )
    _add_tail(p_trace)
    p_next = subs.add_parser(
        "next",
        help="What to work on next: unblocked, highest priority, dependencies met",
    )
    _add_tail(p_next)
    p_lint = subs.add_parser("lint", help="All checks in S2")
    _add_tail(p_lint)
    p_graph = subs.add_parser(
        "graph",
        help="Export the feature graph as Mermaid; --critical prints the critical path",
    )
    _add_tail(p_graph)

    p_doc = subs.add_parser("doc", help="Work with a DOC body")
    dsubs = p_doc.add_subparsers(dest="doc_action", metavar="{read}")
    p_dr = dsubs.add_parser(
        "read",
        help="Read a DOC body, always bounded, reports truncation",
    )
    _add_tail(p_dr)

    p_scan = subs.add_parser(
        "scan-prd",
        help="Index bracketed references in the PRD and DOC bodies",
    )
    _add_tail(p_scan)
    p_export = subs.add_parser("export", help="Export the project as plain text (S3)")
    _add_tail(p_export)
    p_import = subs.add_parser("import", help="Import a plain-text export (S3)")
    _add_tail(p_import)

    subparser.set_defaults(func=product_command, _product_parser=subparser)


def _verb_path(args: argparse.Namespace) -> str | None:
    """Resolve the namespace to a verb path like ``project add`` or ``doc read``."""
    action = getattr(args, "product_action", None)
    if action is None:
        return None
    if action == "project":
        sub = getattr(args, "project_action", None)
        return f"project {sub}" if sub else None
    if action == "doc":
        sub = getattr(args, "doc_action", None)
        return f"doc {sub}" if sub else None
    return action


def product_command(args: argparse.Namespace) -> int:
    """Every verb is a loud stub: stderr line naming the verb path and card, exit 2."""
    path = _verb_path(args)
    if path is None:
        parser = getattr(args, "_product_parser", None)
        if isinstance(parser, argparse.ArgumentParser):
            parser.print_help()
        else:
            print("usage: hermes product {%s}" % ",".join(_TOP_LEVEL))
        return EXIT_OK
    card = VERB_CARDS.get(path)
    if card is None:
        print(f"NOT IMPLEMENTED: {path}", file=sys.stderr)
        return EXIT_NOT_IMPLEMENTED
    print(f"NOT IMPLEMENTED: {path} (card {card})", file=sys.stderr)
    return EXIT_NOT_IMPLEMENTED
