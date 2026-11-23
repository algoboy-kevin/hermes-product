"""The ``hermes product`` CLI.

Card HP-0 built the argparse tree with every verb of docs/specs/S4-tool-behavior.md
section 2 and made each one a loud stub. Card HP-1 implemented ``project add/list/use``.
Card HP-2 implements ``add``, ``show`` and ``list`` (STEP 1) and ``set`` (STEP 2):
entries are validated, every failure prints its message to stderr and returns 1.

Card HP-7 implements ``lint`` and card HP-8 ``scan-prd``: every verb of the S4
table is real now. A verb path missing from ``IMPLEMENTED`` still prints one
stderr line ``NOT IMPLEMENTED: <verb path> (card HP-<n>)`` and exits 2, and the
verb -> implementing-card map lives in ``VERB_CARDS`` alone.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Optional

try:  # package submodule (Hermes plugin loader)
    from . import adopt
    from . import (code_coverage, docs_text, dryrun, export, external, fold, graph, importmd,
                   lint, model, plan, scan, store, verify)
except ImportError:  # top-level module (test suite)
    import adopt  # type: ignore[no-redef]
    import code_coverage  # type: ignore[no-redef]
    import docs_text  # type: ignore[no-redef]
    import dryrun  # type: ignore[no-redef]
    import export  # type: ignore[no-redef]
    import external  # type: ignore[no-redef]
    import fold  # type: ignore[no-redef]
    import graph  # type: ignore[no-redef]
    import importmd  # type: ignore[no-redef]
    import lint  # type: ignore[no-redef]
    import model  # type: ignore[no-redef]
    import plan  # type: ignore[no-redef]
    import scan  # type: ignore[no-redef]
    import store  # type: ignore[no-redef]
    import verify  # type: ignore[no-redef]

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_NOT_IMPLEMENTED = 2
EXIT_CONFLICT = 3

# The one verb-path -> implementing-card map. HP numbers come from the HP-0 brief.
VERB_CARDS: dict[str, str] = {
    "project add": "HP-1",
    "project list": "HP-1",
    "project use": "HP-1",
    "project set": "HP-17",
    "verify": "HP-18",
    "adopt": "HP-19",
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
    "import-md": "HP-10",
    "sync": "HP-12",
    "fold-docs": "HP-16",
}

_TOP_LEVEL = tuple(dict.fromkeys(path.split()[0] for path in VERB_CARDS))

# Verbs this build actually implements (HP-1: the project verbs; HP-2: entities;
# HP-3: link/unlink and the feature graph; HP-4: DOC bodies; HP-5: export/import;
# HP-6: trace and next; HP-7: lint; HP-8: scan-prd; HP-10: import-md; HP-12: sync;
# HP-16: fold-docs).
IMPLEMENTED = {"project add", "project list", "project use", "project set", "add", "set", "show", "list",
               "link", "unlink", "graph", "doc read", "export", "import", "trace", "next",
               "lint", "scan-prd", "import-md", "sync", "fold-docs", "verify", "adopt"}

SEP = "  "


def _add_tail(parser: argparse.ArgumentParser) -> None:
    """Accept and ignore anything after the verb (later cards add real options)."""
    parser.add_argument("rest", nargs=argparse.REMAINDER, help=argparse.SUPPRESS)


def _add_project_option(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--project", default=None, metavar="SLUG",
        help="Project slug (default: the project whose root contains the working directory)",
    )


def _add_write_options(parser: argparse.ArgumentParser, *, dry_run: bool = True) -> None:
    """``--dry-run`` (the real verb against a throwaway copy) and ``--json`` for a writing verb."""
    if dry_run:
        parser.add_argument("--dry-run", dest="write_dry_run", action="store_true", default=False,
                            help="Run the real verb on a throwaway copy of the store: same"
                                 " checks and messages, nothing written")
    parser.add_argument("--json", dest="json_out", action="store_true", default=False,
                        help="Print one JSON object (ok, exit, ref, action, lines) instead of text")


def register_cli(subparser: argparse.ArgumentParser) -> None:
    """Build the argparse tree for every S4 verb. Help wording follows S4 section 2."""
    subs = subparser.add_subparsers(dest="product_action", metavar="{%s}" % ",".join(_TOP_LEVEL))

    p_project = subs.add_parser(
        "project",
        help="Create a project, list projects, pick one. Without --project, the "
             "project whose root contains the working directory is used",
    )
    psubs = p_project.add_subparsers(dest="project_action", metavar="{add,list,use,set}")
    p_pa = psubs.add_parser("add", help="Create a project")
    p_pa.add_argument("slug", help="Unique short key, lowercase, hyphenated")
    p_pa.add_argument("name", help="Display name")
    p_pa.add_argument("--root", default=None,
                      help="Directory of the code repository, used to pick the project"
                           " from a working directory")
    p_pl = psubs.add_parser("list", help="List projects")
    p_pu = psubs.add_parser("use", help="Pick a project")
    p_pu.add_argument("slug", help="Project slug")
    p_ps = psubs.add_parser("set", help="Change a project's name or root")
    p_ps.add_argument("slug", help="Project slug (the key; it never changes)")
    p_ps.add_argument("pairs", nargs="+", metavar="key=value",
                      help="name=... and/or root=... (root=- clears it)")

    # ---- add <R|A|F|S|DOC> ...: one parser, the flags of every kind, checked per kind.
    p_add = subs.add_parser("add", help="Create an R#, A#, F#, S# or DOC#")
    p_add.add_argument("kind", metavar="{R,A,F,S,DOC}", help="Entity type to create")
    _add_project_option(p_add)
    p_add.add_argument("--title", default=None, help="Title (3-80 characters)")
    p_add.add_argument("--statement", default=None, help="One testable sentence (R#)")
    p_add.add_argument("--priority", default=None, help="must|should|could (R#)")
    p_add.add_argument("--rationale", default=None, help="Why it exists (R#, required at approved)")
    p_add.add_argument("--phase", default=None, help="Target release (R#, F#)")
    p_add.add_argument("--notes", default=None, help="Short note")
    p_add.add_argument("--hypothesis-ref", dest="hypothesis_ref", default=None,
                       help="The claim this requirement tests (R#)")
    p_add.add_argument("--legacy", action="store_true", default=None,
                       help="Imported from an existing system (R#)")
    p_add.add_argument("--prd-ref", dest="prd_ref", default=None, help="PRD anchor (R#)")
    p_add.add_argument("--req", default=None, help="Parent requirement, e.g. R3 (A#)")
    p_add.add_argument("--text", default=None, help="The one check, Given/When/Then (A#)")
    p_add.add_argument("--layer", default=None, help="foundation|core|growth (F#)")
    p_add.add_argument("--slug", default=None, help="Unique short key (F#)")
    p_add.add_argument("--description", default=None, help="What it is and its boundary (F#)")
    p_add.add_argument("--infra", action="store_true", default=None,
                       help="Plumbing with no requirement of its own (F#)")
    p_add.add_argument("--paths", default=None, help="Code paths, comma separated (F#, S#)")
    p_add.add_argument("--summary", default=None, help="What the spec decides (S#)")
    p_add.add_argument("--kind", dest="doc_kind", default=None, metavar="KIND",
                       help="prd|spec|context|research|design|api|runbook|report|other (DOC#)")
    p_add.add_argument("--body", default=None,
                       help="The full text that defines the entry (R#, F#, S#), or the"
                            " document text (DOC#); never A#")
    p_add.add_argument("--body-file", dest="body_file", default=None, metavar="PATH",
                       help="Copy this file in as the body (R#, F#, S#, DOC#)")
    p_add.add_argument("--body-ref", dest="body_ref", default=None, metavar="REF",
                       help="An external path or URL holding the body (DOC# only)")
    p_add.add_argument("--if-missing", dest="if_missing", action="store_true", default=False,
                       help="Do nothing (print `<REF> exists`) when the project already has an"
                            " entry with the same key: title (R#, S#, DOC#), slug or title (F#),"
                            " requirement + text (A#)")
    _add_write_options(p_add)

    p_set = subs.add_parser("set", help="Change a field or status (validated)")
    p_set.add_argument("ref", metavar="REF", help="R3, A21, F7, S2 or F:slug")
    p_set.add_argument("pairs", nargs="*", metavar="key=value",
                       help="field=value pairs (quote values that contain spaces)")
    p_set.add_argument("--reason", default=None,
                       help="Why the change happened; required for a statement change"
                            " once the requirement is approved")
    p_set.add_argument("--override-reason", dest="override_reason", default=None,
                       help="Record the reason a feature starts in_progress while a"
                            " mandatory dependency is still planned")
    p_set.add_argument("--body-file", dest="body_file", default=None, metavar="PATH",
                       help="Replace the body with this file (R#, F#, S#, DOC#)")
    _add_write_options(p_set)
    _add_project_option(p_set)
    p_link = subs.add_parser("link", help="Create a relation, or add a dependency edge")
    p_link.add_argument("ref", metavar="REF", help="R3, A21, F7, S2 or DOC1")
    p_link.add_argument("ref2", nargs="?", default=None, metavar="REF",
                        help="The other entry, for a cross-kind relation")
    p_link.add_argument("--needs", default=None, metavar="REF",
                        help="The entry this one depends on (same kind)")
    p_link.add_argument("--optional", action="store_true", default=False,
                        help="A feature dependency that does not block (F# only)")
    _add_write_options(p_link)
    _add_project_option(p_link)
    p_unlink = subs.add_parser("unlink", help="Remove a relation, or remove a dependency edge")
    p_unlink.add_argument("ref", metavar="REF", help="R3, A21, F7, S2 or DOC1")
    p_unlink.add_argument("ref2", nargs="?", default=None, metavar="REF",
                          help="The other entry, for a cross-kind relation")
    p_unlink.add_argument("--needs", default=None, metavar="REF",
                          help="The dependency to remove (same kind)")
    p_unlink.add_argument("--optional", action="store_true", default=False,
                          help=argparse.SUPPRESS)
    _add_write_options(p_unlink)
    _add_project_option(p_unlink)

    p_show = subs.add_parser("show", help="Full detail for one entry")
    p_show.add_argument("ref", metavar="REF", help="R3, A21, F7, S2 or F:slug")
    p_show.add_argument("--body", dest="show_body", action="store_true", default=False,
                        help="Also print the body text (R#, F#, S#, DOC#), bounded")
    p_show.add_argument("--section", default=None, metavar="HEADING",
                        help="With --body: only this section (matched on the heading text)")
    p_show.add_argument("--max-chars", dest="max_chars", type=int, default=4000, metavar="N",
                        help="With --body: at most N characters (1..100000, default 4000)")
    _add_project_option(p_show)

    p_list = subs.add_parser("list", help="One line per item by default")
    p_list.add_argument("kind", nargs="?", default=None, metavar="{R,A,F,S}",
                        help="Only this kind (default: R, then F, then S, then A)")
    p_list.add_argument("--status", default=None, help="Only entries in this state")
    p_list.add_argument("--limit", type=int, default=None, help="At most N lines")
    _add_project_option(p_list)

    p_trace = subs.add_parser(
        "trace",
        help="For one entry, everything connected: requirement -> features, specs, "
             "criteria, docs, cards",
    )
    p_trace.add_argument("ref", metavar="REF", help="R3, A21, F7, S2 or DOC1")
    p_trace.add_argument("--depth", type=int, default=1, metavar="N",
                         help="With 2: expand each linked R/F/S one more hop (default 1)")
    _add_project_option(p_trace)
    p_next = subs.add_parser(
        "next",
        help="What to work on next: unblocked, highest priority, dependencies met",
    )
    p_next.add_argument("--limit", type=int, default=10, metavar="N",
                        help="At most N lines per list (default 10)")
    _add_project_option(p_next)
    p_lint = subs.add_parser("lint", help="All checks in S2")
    p_lint.add_argument("--json", dest="json", action="store_true", default=False,
                        help="Print one JSON object instead of text")
    p_lint.add_argument("--coverage", action="store_true", default=False,
                        help="Also report packages and specs no F#/S# paths cover")
    _add_project_option(p_lint)
    p_graph = subs.add_parser(
        "graph",
        help="Export the feature graph as Mermaid; --critical prints the critical path",
    )
    p_graph.add_argument("--critical", action="store_true", default=False,
                         help="Print the longest chain of mandatory dependencies")
    _add_project_option(p_graph)

    p_doc = subs.add_parser("doc", help="Work with a DOC body")
    dsubs = p_doc.add_subparsers(dest="doc_action", metavar="{read}")
    p_dr = dsubs.add_parser(
        "read",
        help="Read a DOC body, always bounded, reports truncation",
    )
    p_dr.add_argument("ref", metavar="REF", help="DOC1")
    p_dr.add_argument("--section", default=None, metavar="HEADING",
                      help="Only this section (matched on the heading text)")
    p_dr.add_argument("--max-chars", dest="max_chars", type=int, default=4000, metavar="N",
                      help="At most N characters (1..100000, default 4000)")
    _add_project_option(p_dr)

    p_scan = subs.add_parser(
        "scan-prd",
        help="Index bracketed references in the PRD and DOC bodies",
    )
    _add_project_option(p_scan)
    p_export = subs.add_parser("export", help="Export the project as plain text (S3)")
    p_export.add_argument("--out", required=True, metavar="DIR",
                          help="Write the export here (required: there is no default)")
    _add_project_option(p_export)
    p_import = subs.add_parser("import", help="Import a plain-text export (S3)")
    p_import.add_argument("dir", metavar="DIR", help="The export directory to import")
    p_import.add_argument("--as", dest="as_slug", default=None, metavar="SLUG",
                          help="Create the project under this slug instead of the"
                               " one in project.yaml")
    _add_write_options(p_import)

    p_adopt = subs.add_parser(
        "adopt",
        help="Bring a repository's Markdown into the project: a spec file becomes an S# with"
             " its text as the body, other files become DOCs; a plan unless --apply",
    )
    p_adopt.add_argument("dir", metavar="DIR", help="The repository (or docs) directory to scan")
    p_adopt.add_argument("--apply", action="store_true", default=False,
                         help="Write the proposed rows (each file is its own transaction)")
    p_adopt.add_argument("--update", action="store_true", default=False,
                         help="With --apply: also refresh the body of rows whose file changed")
    p_adopt.add_argument("--json", dest="adopt_json", action="store_true", default=False,
                         help="Print one JSON object (counts and one entry per file)")
    _add_project_option(p_adopt)

    p_verify = subs.add_parser(
        "verify",
        help="Check that every passed criterion's evidence quote really occurs in the cited"
             " file at a git revision (exit 1 on a miss)",
    )
    p_verify.add_argument("--rev", default=None, metavar="REV",
                          help="Read cited files from this git revision (default HEAD in a git"
                               " work tree, else the working tree)")
    p_verify.add_argument("--repo", default=None, metavar="DIR",
                          help="Directory the evidence paths resolve against (default: the"
                               " project's root)")
    p_verify.add_argument("--min-quote", dest="min_quote", type=int,
                          default=verify.DEFAULT_MIN_QUOTE, metavar="N",
                          help="Shortest acceptable quote in characters (default %d)"
                               % verify.DEFAULT_MIN_QUOTE)
    p_verify.add_argument("--strict", action="store_true", default=False,
                          help="Free-text evidence (no quote and path:line) also fails")
    p_verify.add_argument("--json", dest="verify_json", action="store_true", default=False,
                          help="Print one JSON object instead of lines")
    _add_project_option(p_verify)

    p_fold = subs.add_parser(
        "fold-docs",
        help="Move spec DOC text onto the S# it defines (schema v2 bodies); a plan"
             " unless --apply",
    )
    p_fold.add_argument("--apply", dest="apply", action="store_true", default=False,
                        help="Do it: copy the text into the S# body, then delete the"
                             " DOC row, links and body file (one transaction)")
    _add_write_options(p_fold, dry_run=False)
    _add_project_option(p_fold)

    p_import_md = subs.add_parser(
        "import-md",
        help="Import the S6 markdown documents of a docs directory into an existing"
             " project (S6): entries, links and DOC bodies, ids preserved",
    )
    p_import_md.add_argument("dir", metavar="DIR", help="The docs directory to import")
    p_import_md.add_argument("--dry-run", dest="dry_run", action="store_true",
                             default=False,
                             help="Parse and cross-check only; write nothing")
    _add_project_option(p_import_md)

    p_sync = subs.add_parser(
        "sync",
        help="Read kanban boards (read-only) and propose external links plus forward"
             " execution status moves (S5); nothing is written without --apply",
    )
    p_sync.add_argument("--board", dest="board", action="append", required=True,
                        metavar="SLUG", help="A kanban board to read (repeatable)")
    p_sync.add_argument("--map", dest="map", action="append", default=[],
                        metavar="COLUMN=STATUS",
                        help="Map a board column to approved|in_progress|implemented"
                             " (or none); repeatable")
    p_sync.add_argument("--apply", dest="apply", action="store_true", default=False,
                        help="Write the link rows and the forward status moves")
    _add_project_option(p_sync)

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


# --------------------------------------------------------------------- compact output
_NEWLINES = re.compile(r"[\r\n\t]")


def _flat(value: Any) -> str:
    """One line, always: any newline or tab inside a text becomes a single space."""
    return _NEWLINES.sub(" ", str(value))


def _column(value: Any) -> str:
    """A column value, ``-`` when absent."""
    return "-" if value is None or value == "" else _flat(value)


def _ids(values: list) -> str:
    return ",".join(str(value) for value in values) or "-"


def _clip(text: Any, width: int = 80) -> str:
    flat = _flat(text or "")
    return flat if len(flat) <= width else flat[: width - 1] + "\u2026"


def _line_requirement(item: dict) -> str:
    links = item["links"]
    return SEP.join([
        item["ref"], _column(item["status"]), _column(item["priority"]),
        _column(item["phase"]), _flat(item["title"] or ""),
        f"F:{_ids(links['features'])}",
        f"S:{_ids(links['specs'])}",
        f"A:{links['criteria_passed']}/{links['criteria_total']}",
    ])


def _line_criterion(item: dict) -> str:
    level = item.get("evidence_level")
    return SEP.join([
        item["ref"], _column(item["status"]),
        model.format_ref("requirement", item["requirement_id"]),
        _column(None if level in (None, "none") else level),
        _clip(item.get("text")),
    ])


def _line_feature(item: dict) -> str:
    links = item["links"]
    return SEP.join([
        item["ref"], _column(item["status"]), _column(item["layer"]),
        _column(item["phase"]), _flat(item["title"] or ""),
        f"needs F:{_ids(links['needs'])}",
        f"R:{_ids(links['requirements'])}",
        f"S:{_ids(links['specs'])}",
    ])


def _line_spec(item: dict) -> str:
    links = item["links"]
    return SEP.join([
        item["ref"], _column(item["status"]), _flat(item["title"] or ""),
        f"R:{_ids(links['requirements'])}",
        f"F:{_ids(links['features'])}",
    ])


def _line_doc(item: dict) -> str:
    refs = [model.format_ref(kind, value) for kind, value in item.get("doc_links") or []]
    return SEP.join([
        item["ref"], _column(item.get("doc_kind")), _column(item.get("status")),
        _flat(item.get("title") or ""), f"links {_ids(refs)}",
    ])


LINE_BUILDERS = {
    "requirement": _line_requirement,
    "criterion": _line_criterion,
    "feature": _line_feature,
    "spec": _line_spec,
    "doc": _line_doc,
}


# ------------------------------------------------------------------- detail (show)
# S1 field order per kind.
SHOW_ORDER: dict[str, tuple[str, ...]] = {
    "requirement": ("id", "title", "statement", "rationale", "priority", "phase", "status",
                    "status_reason", "superseded_by", "blocked", "blocked_reason",
                    "hypothesis_ref", "legacy", "prd_ref", "notes", "updated_at"),
    "criterion": ("id", "requirement_id", "text", "status", "evidence", "evidence_level",
                  "notes", "updated_at"),
    "feature": ("id", "slug", "title", "layer", "status", "status_reason", "phase",
                "description", "infra", "paths", "blocked", "blocked_reason", "notes",
                "updated_at"),
    "spec": ("id", "title", "summary", "status", "status_reason", "superseded_by", "paths",
             "blocked", "blocked_reason", "notes", "updated_at"),
    "doc": ("id", "title", "kind", "summary", "status", "superseded_by", "notes",
            "updated_at"),
}

_FLAG_FIELDS = {"blocked", "legacy", "infra"}


def _show_value(kind: str, field: str, value: Any) -> str:
    if field == "id":
        return model.format_ref(kind, value)
    if field == "requirement_id":
        return model.format_ref("requirement", value)
    if field == "superseded_by":
        return model.format_ref(kind if kind in ("requirement", "spec", "doc") else "requirement",
                                value)
    if field == "paths":
        try:
            items = json.loads(value)
        except (TypeError, ValueError):
            items = [str(value)]
        return ", ".join(str(item) for item in items)
    return _flat(value)


def _show_line(kind: str, field: str, value: Any) -> Optional[str]:
    """One ``key: value`` line, or None for an empty field."""
    if field in _FLAG_FIELDS:
        return f"{field}: 1" if value else None
    if field == "evidence_level" and value in (None, "none"):
        return None
    if value is None or value == "":
        return None
    return f"{field}: {_show_value(kind, field, value)}"


# ----------------------------------------------------------------------- handlers
def _project(args: argparse.Namespace) -> str:
    return store.resolve_project(getattr(args, "project", None))


def _project_add(args: argparse.Namespace) -> int:
    """`project add <slug> <name> [--root PATH]` -> `project <slug> created`."""
    created = store.create_project(args.slug, args.name, root=args.root)
    print(f"project {created['slug']} created")
    return EXIT_OK


def _project_set(args: argparse.Namespace) -> int:
    """`project set <slug> name=... root=...` -> `project <slug> updated: name, root`."""
    changes: dict[str, str] = {}
    for pair in args.pairs:
        if "=" not in pair:
            raise model.ValidationError(f"expected key=value, got {pair!r}")
        key, value = pair.split("=", 1)
        changes[key.strip()] = value
    result = store.update_project(args.slug, changes)
    print(f"project {result['slug']} updated: {', '.join(result['changed'])}")
    return EXIT_OK


def _project_list(args: argparse.Namespace) -> int:
    """`project list` -> `<slug>  <name>  <root or ->`, `*` before the active one."""
    active = store.get_active_project()
    for project in store.list_projects():
        mark = "*" if project["slug"] == active else ""
        print(f"{mark}{project['slug']}  {project['name']}  {project['root'] or '-'}")
    return EXIT_OK


def _project_use(args: argparse.Namespace) -> int:
    """`project use <slug>` -> `using <slug>`."""
    store.set_active_project(args.slug)
    print(f"using {args.slug}")
    return EXIT_OK


#: `add` flag -> stored field. The per-kind allowed list is model.ADD_FIELDS.
_ADD_FLAG_FIELDS = {
    "title": "title",
    "statement": "statement",
    "priority": "priority",
    "rationale": "rationale",
    "phase": "phase",
    "notes": "notes",
    "hypothesis_ref": "hypothesis_ref",
    "legacy": "legacy",
    "prd_ref": "prd_ref",
    "req": "requirement_id",
    "text": "text",
    "layer": "layer",
    "slug": "slug",
    "description": "description",
    "infra": "infra",
    "paths": "paths",
    "summary": "summary",
}


def _cmd_add(args: argparse.Namespace) -> int:
    """`add <KIND> ...` -> `<REF> created`; DOC# takes --kind and a body option."""
    project = _project(args)
    kind = model.kind_for(args.kind)
    fields: dict[str, Any] = {}
    if kind == "doc":
        for name in ("title", "summary", "notes", "body_file", "body", "body_ref"):
            value = getattr(args, name, None)
            if value is not None:
                fields[name] = value
        fields["kind"] = args.doc_kind
        if args.if_missing:
            found = store.find_existing(project, "doc", fields)
            if found is not None:
                print(f"{model.format_ref('doc', found)} exists")
                return EXIT_OK
        created = store.create_entity(project, "doc", fields)
        print(f"{created['ref']} created")
        return EXIT_OK
    for flag, field in _ADD_FLAG_FIELDS.items():
        value = getattr(args, flag, None)
        if value is None:
            continue
        if field not in model.ADD_FIELDS[kind]:
            raise model.ValidationError(
                f"--{flag.replace('_', '-')} is not a field of add {model.PREFIX[kind]}"
            )
        fields[field] = value
    for name in ("body_file", "body", "body_ref"):     # refused by the store where unsupported
        value = getattr(args, name, None)
        if value is not None:
            fields[name] = value
    if args.if_missing:
        found = store.find_existing(project, kind, fields)
        if found is not None:
            print(f"{model.format_ref(kind, found)} exists")
            return EXIT_OK
    created = store.create_entity(project, kind, fields)
    print(f"{created['ref']} created")
    return EXIT_OK


def _render_item(item: dict) -> str:
    return LINE_BUILDERS[item["kind"]](item)


def _link_tokens(kind: str, links: dict) -> list[str]:
    """The link columns of one entry, as the tokens `list` uses; empty ones dropped."""
    tokens: list[str] = []
    if kind == "requirement":
        if links.get("features"):
            tokens.append(f"F:{_ids(links['features'])}")
        if links.get("specs"):
            tokens.append(f"S:{_ids(links['specs'])}")
    elif kind == "feature":
        if links.get("needs"):
            tokens.append(f"needs F:{_ids(links['needs'])}")
        if links.get("requirements"):
            tokens.append(f"R:{_ids(links['requirements'])}")
        if links.get("specs"):
            tokens.append(f"S:{_ids(links['specs'])}")
    elif kind == "spec":
        if links.get("requirements"):
            tokens.append(f"R:{_ids(links['requirements'])}")
        if links.get("features"):
            tokens.append(f"F:{_ids(links['features'])}")
    return tokens


def _cmd_list(args: argparse.Namespace) -> int:
    """`list [R|A|F|S|DOC] [--status S] [--limit N]` -> one compact line per item."""
    if args.kind is not None and args.kind not in ("R", "A", "F", "S", "DOC"):
        raise model.ValidationError(
            f"list accepts R, A, F, S or DOC, got {args.kind!r}"
        )
    project = _project(args)
    items = store.list_entities(project, args.kind, status=args.status, limit=args.limit)
    for item in items:
        print(_render_item(item))
    return EXIT_OK


def _cmd_set(args: argparse.Namespace) -> int:
    """`set <REF> key=value ...` -> `R3 approved -> in_progress`, else `R3 updated: a, b`."""
    project = _project(args)
    if not args.pairs and args.body_file is None:
        raise model.ValidationError(
            "set needs key=value pairs, or --body-file to replace a body"
        )
    changes: dict[str, str] = {}
    for pair in args.pairs:
        if "=" not in pair:
            raise model.ValidationError(
                f"expected key=value, got {pair!r} (the value may be quoted by the shell)"
            )
        key, value = pair.split("=", 1)
        key = key.strip()
        if not key:
            raise model.ValidationError(f"expected key=value, got {pair!r}: empty key")
        changes[key] = value
    result = store.update_entity(project, args.ref, changes, reason=args.reason,
                                 override_reason=args.override_reason,
                                 body_source=args.body_file)
    ref = result["ref"]
    if result["status_change"] is not None:
        old, new = result["status_change"]
        print(f"{ref} {old} -> {new}")
    else:
        print(f"{ref} updated: {', '.join(result['changed'])}")
    return EXIT_OK


def _cmd_show(args: argparse.Namespace) -> int:
    """`show <REF>` -> S1 fields, the link line, child A-lines, docs, then `history:`.

    A DOC# is different: fields, then one body line (never the text), then each
    linked entry as its compact line.
    """
    project = _project(args)
    entry = store.get_entity(project, args.ref, with_body=bool(args.show_body))
    kind, row = entry["kind"], entry["row"]
    if args.show_body and kind == "criterion":
        raise model.ValidationError(
            f"{entry['ref']} has no body (a criterion is one sentence plus evidence)"
        )
    if not args.show_body and (args.section is not None or args.max_chars != 4000):
        raise model.ValidationError("--section and --max-chars only apply with --body")
    for field in SHOW_ORDER[kind]:
        line = _show_line(kind, field, row.get(field))
        if line is not None:
            print(line)
    if kind == "doc":
        print(_doc_body_line(project, row))
        for item in entry["doc_links"]:
            print(_render_item(item))
        if args.show_body:
            _print_body(entry, _doc_text(project, row), args)
        return EXIT_OK
    if kind in model.BODY_KINDS and row.get("body_chars"):
        print(f"body: {row['body_chars']} chars (show {entry['ref']} --body)")
    tokens = _link_tokens(kind, entry["links"])
    if tokens:
        print(f"links: {SEP.join(tokens)}")
    for child in entry["children"]:
        item = dict(child)
        item["kind"] = "criterion"
        item["ref"] = model.format_ref("criterion", child["id"])
        item["links"] = {}
        print(_line_criterion(item))
    if entry["docs"]:
        print("docs:")
        for doc in entry["docs"]:
            print(SEP.join([f"DOC{doc['id']}", _column(doc.get("kind")),
                            _flat(doc.get("summary") or "")]))
    print("history:")
    for event in entry["history"]:
        line = f"{event['at']}{SEP}{event['change']}"
        if event["reason"]:
            line += f"{SEP}[{event['reason']}]"
        print(line)
    if args.show_body:
        _print_body(entry, row.get("body") or "", args)
    return EXIT_OK


def _print_body(entry: dict, text: str, args: argparse.Namespace) -> None:
    """The ``show --body`` tail: bounded text (optionally one section) + a truncation trailer."""
    limit = args.max_chars
    if not 1 <= limit <= 100000:
        raise model.ValidationError(f"--max-chars must be 1..100000, got {limit}")
    if not text:
        print("--- body: none ---")
        return
    if args.section is not None:
        found = docs_text.section(text, args.section)
        if found is None:
            names = [name for _level, name, _start, _end in docs_text.headings(text)][:20]
            listed = ", ".join(names) if names else "no headings"
            raise model.ValidationError(
                f"no section {args.section!r} in {entry['ref']} (headings: {listed})"
            )
        text = found
    piece, total, truncated = docs_text.bounded(text, limit)
    print("--- body ---")
    sys.stdout.write(piece)
    if not piece.endswith("\n"):
        sys.stdout.write("\n")
    if truncated:
        print(f"[truncated: showed {len(piece)} of {total} chars]")


def _doc_body_line(project: str, row: dict) -> str:
    """One line saying where a DOC's body lives — never the body text."""
    if row.get("body_file"):
        path = store.doc_body_path(project, row)
        if path.is_file():
            chars = len(path.read_text(encoding="utf-8"))
            return f"body: {chars} chars (doc read DOC{row['id']})"
        return f"body: missing ({row['body_file']})"
    if row.get("body_ref"):
        return f"body_ref: {_flat(row['body_ref'])}"
    return "body: none"


def _doc_text(project: str, row: dict) -> str:
    """A DOC's text: its stored body file, or a LOCAL body_ref read at call time."""
    ref = model.format_ref("doc", row["id"])
    if row.get("body_file"):
        path = store.doc_body_path(project, row)
        if not path.is_file():
            raise model.ValidationError(f"{ref} body file is missing: {path}")
        return path.read_text(encoding="utf-8")
    body_ref = str(row.get("body_ref") or "").strip()
    if not body_ref:
        raise model.ValidationError(f"{ref} has no body")
    if body_ref.startswith(("http://", "https://")):
        raise model.ValidationError("body_ref is a URL; fetch it yourself")
    path = Path(body_ref).expanduser()
    if not path.is_absolute():
        project_row = store.get_project(project) or {}
        base = Path(project_row["root"]) if project_row.get("root") else Path.cwd()
        path = base / path
    if not path.is_file():
        raise model.ValidationError(f"{ref} body_ref file does not exist: {path}")
    return path.read_text(encoding="utf-8")


def _cmd_doc_read(args: argparse.Namespace) -> int:
    """`doc read DOC1 [--section H] [--max-chars N]` -> bounded text + one trailer."""
    project = _project(args)
    limit = args.max_chars
    if not 1 <= limit <= 100000:
        raise model.ValidationError(f"--max-chars must be 1..100000, got {limit}")
    entry = store.get_entity(project, args.ref)
    if entry["kind"] != "doc":
        raise model.ValidationError(
            f"{entry['ref']} is not a DOC#; doc read reads a document body"
        )
    text = _doc_text(project, entry["row"])
    if args.section is not None:
        found = docs_text.section(text, args.section)
        if found is None:
            names = [name for _level, name, _start, _end in docs_text.headings(text)][:20]
            listed = ", ".join(names) if names else "no headings"
            raise model.ValidationError(
                f"no section {args.section!r} in {entry['ref']} (headings: {listed})"
            )
        text = found
    piece, total, truncated = docs_text.bounded(text, limit)
    sys.stdout.write(piece)
    if truncated:
        if not piece.endswith("\n"):
            sys.stdout.write("\n")
        print(f"[truncated: showed {len(piece)} of {total} chars]")
    return EXIT_OK


def _cmd_link(args: argparse.Namespace, *, remove: bool) -> int:
    """`link`/`unlink <REF> [<REF>|--needs <REF>] [--optional]` -> one result line."""
    project = _project(args)
    result = store.link_entries(project, args.ref, args.ref2, needs=args.needs,
                                optional=args.optional, remove=remove)
    print(result["message"])
    return EXIT_OK


def _cmd_graph(args: argparse.Namespace) -> int:
    """`graph` -> the Mermaid graph; `graph --critical` -> the longest chain."""
    project = _project(args)
    data = store.feature_graph(project)
    if args.critical:
        # edges are (feature, needs); the path prints in BUILD order (needs first)
        path = graph.critical_path(data["nodes"], [(needs, feature) for feature, needs in data["mandatory"]])
        if not path:
            print("no features")
        else:
            chain = " -> ".join(model.format_ref("feature", value) for value in path)
            print(f"critical path ({len(path)}): {chain}")
    else:
        print(graph.mermaid(data["nodes"], data["edges"]))
    return EXIT_OK


def _cmd_export(args: argparse.Namespace) -> int:
    """`export --out DIR [--project SLUG]` -> one summary line (S3 / A21)."""
    project = _project(args)
    counts = export.export_project(project, args.out)
    print(f"exported {project} to {args.out}:"
          f" {counts['requirements']} requirements, {counts['criteria']} criteria,"
          f" {counts['features']} features, {counts['specs']} specs, {counts['docs']} docs")
    return EXIT_OK


def _cmd_import(args: argparse.Namespace) -> int:
    """`import DIR [--as SLUG]` -> `imported <slug>: ...`; conflicts exit 3 (S3 / A22)."""
    result = export.import_export(args.dir, as_slug=args.as_slug)
    print(f"imported {result['slug']}: {result['inserted']} inserted,"
          f" {result['skipped']} skipped, {result['conflicts']} conflicts")
    for line in result["conflict_lines"]:
        print(line)
    return EXIT_CONFLICT if result["conflicts"] else EXIT_OK


def _cmd_adopt(args: argparse.Namespace) -> int:
    """`adopt DIR [--apply] [--update]` -> a diff-like plan, or the applied counts (HP-19)."""
    project = _project(args)
    found = adopt.plan(project, args.dir)
    counts = adopt.apply(project, found, update=args.update) if args.apply else None
    if args.update and not args.apply:
        raise model.ValidationError("--update needs --apply")
    code = EXIT_ERROR if counts and counts["failed"] else EXIT_OK
    if args.adopt_json:
        rows = [{key: row.get(key) for key in ("path", "target", "doc_kind", "action", "ref",
                                               "applied", "title", "summary", "thin", "chars",
                                               "error")} for row in found["rows"]]
        print(json.dumps({"ok": code == 0, "project": project, "root": found["root"],
                          "applied": bool(args.apply), "counts": counts, "rows": rows,
                          "skipped": found["skipped"]}, ensure_ascii=False))
        return code
    for line in adopt.render(project, found, counts=counts, update=args.update):
        print(line)
    return code


def _cmd_verify(args: argparse.Namespace) -> int:
    """`verify [--rev REV] [--strict]` -> one line per criterion that is not ok, then a total."""
    project = _project(args)
    result = verify.verify_project(project, rev=args.rev, repo=args.repo,
                                   min_quote=args.min_quote)
    lines, code = verify.render(project, result, strict=args.strict)
    if args.verify_json:
        counts: dict[str, int] = {}
        for row in result["rows"]:
            counts[row["verdict"]] = counts.get(row["verdict"], 0) + 1
        print(json.dumps({"ok": code == 0, "project": project, "rev": result["rev"],
                          "root": result["root"], "counts": counts, "rows": result["rows"]},
                         ensure_ascii=False))
        return code
    for line in lines:
        print(line)
    return code


def _cmd_fold_docs(args: argparse.Namespace) -> int:
    """`fold-docs [--apply]` -> the plan (one line per spec) or the applied total."""
    project = _project(args)
    found = fold.plan(project)
    applied = fold.apply(project) if args.apply and found["items"] else None
    for line in fold.render(project, found, applied=applied):
        print(line)
    return EXIT_OK


def _cmd_import_md(args: argparse.Namespace) -> int:
    """`import-md DIR [--dry-run]` -> the counts line; cross-check problems exit 1 (A25)."""
    project = args.project if args.project is not None else store.resolve_project()
    result = importmd.import_markdown(args.dir, project, dry_run=args.dry_run)
    counts = (f"{result['requirements']} requirements, {result['criteria']} criteria,"
              f" {result['features']} features, {result['specs']} specs,"
              f" {result['docs']} docs")
    if result["problems"]:
        for problem in result["problems"]:
            print(problem)
        return EXIT_ERROR
    if args.dry_run:
        print(f"import-md {project} (dry run): {counts}")
        return EXIT_OK
    print(f"import-md {project}: {counts} ({result['inserted']} inserted,"
          f" {result['skipped']} skipped, {result['conflicts']} conflicts)")
    for line in result["conflict_lines"]:
        print(line)
    for note in result["notes"]:
        print(note)
    return EXIT_CONFLICT if result["conflicts"] else EXIT_OK


def _cmd_sync(args: argparse.Namespace) -> int:
    """`sync --board SLUG ... [--map C=S] [--apply]` -> proposals; refusals exit 3 (S5)."""
    project = _project(args)
    result = external.sync(project, args.board, overrides=args.map, apply=args.apply)
    print(f"sync ({project}): {result['boards']} boards, {result['cards']} cards read,"
          f" {result['with_lines']} with product lines")
    for line in result["lines"]:
        print(line)
    if not args.apply:
        print("proposal only: re-run with --apply to write")
    return EXIT_CONFLICT if result["conflicts"] else EXIT_OK


# ------------------------------------------------------------------ trace (HP-6)
def _trace_compact(item: dict) -> str:
    """A section entry's compact line; DOC lines drop the links column."""
    if item["kind"] == "doc":
        return SEP.join([
            item["ref"], _column(item.get("doc_kind")), _column(item.get("status")),
            _flat(item.get("title") or ""),
        ])
    return LINE_BUILDERS[item["kind"]](item)


def _card_line(row: dict) -> str:
    """`<system>:<external_id>  <role|->  <url|->`."""
    return SEP.join([f"{row['system']}:{row['external_id']}",
                     _column(row.get("role")), _column(row.get("url"))])


def _section(name: str, lines: list[str]) -> list[str]:
    """`<name>:` then each line indented two spaces; nothing when empty."""
    return [] if not lines else [f"{name}:"] + ["  " + line for line in lines]


def _trace_data(con, project: str) -> dict:
    """Everything trace reads, gathered once: items, dependency edges, doc links."""
    items = {(item["kind"], item["id"]): item
             for item in store.list_entities(project, con=con)}
    deps = {kind: store.dependency_pairs(con, project, kind)
            for kind in ("requirement", "feature", "spec")}
    docs_by_entity: dict[tuple, list] = {}
    for item in items.values():
        if item["kind"] == "doc":
            for target in item.get("doc_links") or []:
                key = (target[0], target[1])
                if key in items:
                    docs_by_entity.setdefault(key, []).append(item)
    return {"con": con, "project": project, "items": items,
            "deps": deps, "docs": docs_by_entity}


def _needs(ctx: dict, kind: str, entity_id: int) -> list[int]:
    return [needs_id for row_id, needs_id, _opt in ctx["deps"][kind]
            if row_id == entity_id]


def _needed_by(ctx: dict, kind: str, entity_id: int) -> list[int]:
    return [row_id for row_id, needs_id, _opt in ctx["deps"][kind]
            if needs_id == entity_id]


def _entries(ctx: dict, name: str, entries: list[dict], depth: int,
             seen: set) -> list[str]:
    """One section of entry lines; at depth 2 each linked R/F/S nests one hop."""
    out = _section(name, [_trace_compact(entry) for entry in entries])
    if depth != 2:
        return out
    for entry in entries:
        key = (entry["kind"], entry["id"])
        if entry["kind"] in ("requirement", "feature", "spec") and key not in seen:
            seen.add(key)
            out += ["    " + line for line in _trace_block(ctx, entry, 1, seen)]
    return out


def _cards(ctx: dict, item: dict) -> list[str]:
    rows = store.external_links_of(ctx["con"], ctx["project"], item["kind"], item["id"])
    return _section("cards", [_card_line(row) for row in rows])


def _trace_block(ctx: dict, item: dict, depth: int, seen: set) -> list[str]:
    """One entry's derived lines and sections (no compact line)."""
    kind, items = item["kind"], ctx["items"]
    out: list[str] = []
    if kind == "requirement":
        criteria = [items[("criterion", cid)] for cid in item["links"]["criteria"]]
        done, total = plan.requirement_progress(c["status"] for c in criteria)
        out.append(f"progress: {done}/{total} criteria")
        span = plan.evidence_span(c.get("evidence_level") for c in criteria
                                  if c["status"] == "passed")
        out.append("evidence: none" if span is None
                   else f"evidence: strongest {span[0]}, weakest {span[1]}")
        out += _entries(ctx, "features",
                        [items[("feature", fid)] for fid in item["links"]["features"]],
                        depth, seen)
        out += _entries(ctx, "specs",
                        [items[("spec", sid)] for sid in item["links"]["specs"]],
                        depth, seen)
        out += _entries(ctx, "criteria", criteria, depth, seen)
        out += _entries(ctx, "docs",
                        ctx["docs"].get(("requirement", item["id"]), []), depth, seen)
        out += _entries(ctx, "needs",
                        [items[("requirement", rid)]
                         for rid in _needs(ctx, "requirement", item["id"])], depth, seen)
        out += _entries(ctx, "needed by",
                        [items[("requirement", rid)]
                         for rid in _needed_by(ctx, "requirement", item["id"])],
                        depth, seen)
        out += _cards(ctx, item)
    elif kind == "feature":
        reqs = [items[("requirement", rid)] for rid in item["links"]["requirements"]]
        done, total = plan.feature_progress(r["status"] for r in reqs)
        out.append(f"progress: {done}/{total} requirements done")
        out += _entries(ctx, "requirements", reqs, depth, seen)
        out += _entries(ctx, "specs",
                        [items[("spec", sid)] for sid in item["links"]["specs"]],
                        depth, seen)
        out += _entries(ctx, "needs",
                        [items[("feature", fid)]
                         for fid in _needs(ctx, "feature", item["id"])], depth, seen)
        out += _entries(ctx, "needed by",
                        [items[("feature", fid)]
                         for fid in _needed_by(ctx, "feature", item["id"])],
                        depth, seen)
        out += _entries(ctx, "docs",
                        ctx["docs"].get(("feature", item["id"]), []), depth, seen)
        out += _cards(ctx, item)
    elif kind == "spec":
        out += _entries(ctx, "requirements",
                        [items[("requirement", rid)]
                         for rid in item["links"]["requirements"]], depth, seen)
        out += _entries(ctx, "features",
                        [items[("feature", fid)] for fid in item["links"]["features"]],
                        depth, seen)
        out += _entries(ctx, "needs",
                        [items[("spec", sid)]
                         for sid in _needs(ctx, "spec", item["id"])], depth, seen)
        out += _entries(ctx, "needed by",
                        [items[("spec", sid)]
                         for sid in _needed_by(ctx, "spec", item["id"])], depth, seen)
        out += _entries(ctx, "docs",
                        ctx["docs"].get(("spec", item["id"]), []), depth, seen)
        out += _cards(ctx, item)
    elif kind == "criterion":
        parent = items[("requirement", item["requirement_id"])]
        out += _entries(ctx, "requirement", [parent], depth, seen)
        out += _entries(ctx, f"via {parent['ref']}",
                        [items[("feature", fid)] for fid in parent["links"]["features"]],
                        depth, seen)
        if parent.get("prd_ref"):
            out.append(f"prd: {_flat(parent['prd_ref'])}")
        docs = ctx["docs"].get(("criterion", item["id"]), []) + \
            ctx["docs"].get(("requirement", item["requirement_id"]), [])
        out += _entries(ctx, "docs", docs, depth, seen)
    elif kind == "doc":
        linked = [items[(target[0], target[1])] for target in item.get("doc_links") or []
                  if (target[0], target[1]) in items]
        out += _entries(ctx, "links", linked, depth, seen)
    return out


def _cmd_trace(args: argparse.Namespace) -> int:
    """`trace <REF> [--depth 1|2]` -> the connected graph of one entry (HP-6)."""
    if args.depth not in (1, 2):
        raise model.ValidationError("depth must be 1 or 2")
    project = _project(args)
    con = store.connect()
    try:
        kind, entity_id = store.resolve_entity(project, args.ref, con=con)
        ctx = _trace_data(con, project)
        item = ctx["items"][(kind, entity_id)]
        seen = {(kind, entity_id)}
        lines = [LINE_BUILDERS[kind](item)]          # the entry's own line: as in list
        lines += _trace_block(ctx, item, args.depth, seen)
    finally:
        con.close()
    print("\n".join(lines))
    return EXIT_OK


def _cmd_next(args: argparse.Namespace) -> int:
    """`next [--limit N]` -> the ready queue and what blocks the rest (HP-6)."""
    project = _project(args)
    con = store.connect()
    try:
        items = list(store.list_entities(project, "feature", con=con))
        requirements = {item["id"]: item
                        for item in store.list_entities(project, "requirement", con=con)}
        status_by_id = {item["id"]: item["status"] for item in items}
        edges = store.dependency_pairs(con, project, "feature")
        mandatory = {item["id"]: [(needs_id, optional)
                                  for row_id, needs_id, optional in edges
                                  if row_id == item["id"]] for item in items}
        needs_map = {item["id"]: [needs_id for row_id, needs_id, _optional in edges
                                 if row_id == item["id"]] for item in items}
        req_priority = {item["id"]: [(requirements[rid]["priority"],
                                      requirements[rid]["status"])
                                     for rid in item["links"]["requirements"]
                                     if rid in requirements] for item in items}
        priorities = plan.effective_priority(status_by_id, req_priority, needs_map)
        for item in items:
            item["priority"] = priorities[item["id"]]
        candidates = [item for item in items if item["status"] in ("planned", "in_progress")]
        needed = {needs_id for _row_id, needs_id, _optional in edges}
        unplanned = [item for item in candidates if plan.is_unplanned(item, needed)]
        hidden = {item["id"] for item in unplanned}
        ready, blocked = plan.ready_and_blocked(
            [item for item in items if item["id"] not in hidden], mandatory, status_by_id)
    finally:
        con.close()
    tally = f"{len(ready)} ready, {len(blocked)} blocked"
    if unplanned:
        tally += f", {len(unplanned)} unplanned"
    print(f"next ({project}): {tally}")
    if ready:
        for item in ready[: args.limit]:
            print(_line_feature(item))
        if len(ready) > args.limit:
            print(f"  \u2026 and {len(ready) - args.limit} more")
    else:
        print("nothing ready")
    if blocked:
        print("blocked:")
        for item, reason in blocked[: args.limit]:
            print(f"{_line_feature(item)}{SEP}{reason}")
        if len(blocked) > args.limit:
            print(f"  \u2026 and {len(blocked) - args.limit} more")
    if unplanned:
        shown = ", ".join(item["ref"] for item in unplanned[: args.limit])
        more = f" \u2026 and {len(unplanned) - args.limit} more" if len(unplanned) > args.limit else ""
        print(f"unplanned (no requirement, spec or paths; link one, set paths, mark infra or"
              f" cut): {shown}{more}")
    return EXIT_OK


def _cmd_lint(args: argparse.Namespace) -> int:
    """`lint [--json] [--coverage] [--project SLUG]` -> the S2 section 3 report.

    ``--coverage`` is opt-in: it adds the uncovered_path infos and the report line
    (and, with ``--json``, the report dict and an empty ``not_yet``). Without it
    the output is exactly the HP-7 one. Exit 1 on any Error; info never counts.
    """
    project = _project(args)
    report: Optional[dict] = None
    if getattr(args, "coverage", False):
        con = store.connect()
        try:
            facts = lint.load_facts(con, project, coverage=True)
        finally:
            con.close()
        findings = lint.check(facts)
        report = facts.get("coverage")
    else:
        findings = lint.run(project)
    errors = sum(1 for finding in findings if finding.severity == "error")
    warnings = sum(1 for finding in findings if finding.severity == "warning")
    if args.json:
        payload = {
            "project": project,
            "errors": errors,
            "warnings": warnings,
            "findings": [{"flag": finding.flag, "severity": finding.severity,
                          "ref": finding.ref, "message": finding.message}
                         for finding in findings],
            "not_yet": [] if report is not None else list(lint.NOT_YET),
        }
        if report is not None:
            payload["coverage"] = report
        print(json.dumps(payload, sort_keys=True))
    else:
        print(f"lint ({project}): {errors} errors, {warnings} warnings")
        for finding in findings:
            print(f"{finding.severity} {finding.flag} {finding.ref}  {finding.message}")
        if report is not None:
            print(code_coverage.format_report(report)[0])
        else:
            print(f"not yet checked: {', '.join(lint.NOT_YET)}")
    return EXIT_ERROR if errors else EXIT_OK


def _cmd_scan_prd(args: argparse.Namespace) -> int:
    """`scan-prd [--project SLUG]` -> counts, then one line per broken reference (A18)."""
    project = _project(args)
    result = lint.scan_project(project)
    problems = result["problems"]
    print(f"scan-prd ({project}): {result['documents']} documents,"
          f" {result['references']} references, {len(problems)} problems")
    for doc_ref, problem in problems:
        print(scan.format_problem(doc_ref, problem))
    return EXIT_ERROR if problems else EXIT_OK


_IMPLEMENTED_HANDLERS = {
    "project add": _project_add,
    "project list": _project_list,
    "project use": _project_use,
    "project set": _project_set,
    "add": _cmd_add,
    "set": _cmd_set,
    "list": _cmd_list,
    "show": _cmd_show,
    "link": lambda args: _cmd_link(args, remove=False),
    "unlink": lambda args: _cmd_link(args, remove=True),
    "graph": _cmd_graph,
    "doc read": _cmd_doc_read,
    "export": _cmd_export,
    "import": _cmd_import,
    "trace": _cmd_trace,
    "next": _cmd_next,
    "lint": _cmd_lint,
    "scan-prd": _cmd_scan_prd,
    "import-md": _cmd_import_md,
    "sync": _cmd_sync,
    "fold-docs": _cmd_fold_docs,
    "verify": _cmd_verify,
    "adopt": _cmd_adopt,
}


def product_command(args: argparse.Namespace) -> int:
    """Implemented verbs run; every other verb is a loud stub (stderr line, exit 2)."""
    path = _verb_path(args)
    if path is None:
        parser = getattr(args, "_product_parser", None)
        if isinstance(parser, argparse.ArgumentParser):
            parser.print_help()
        else:
            print("usage: hermes product {%s}" % ",".join(_TOP_LEVEL))
        return EXIT_OK
    handler = _IMPLEMENTED_HANDLERS.get(path)
    if handler is not None:
        errors = (store.StoreError, model.ValidationError)
        dry = bool(getattr(args, "write_dry_run", False))
        as_json = bool(getattr(args, "json_out", False))
        try:
            if dry or as_json:
                return dryrun.run_with_options(handler, args, dry_run=dry, as_json=as_json,
                                               errors=errors, exit_error=EXIT_ERROR)
            return handler(args)
        except errors as exc:
            print(exc, file=sys.stderr)
            return EXIT_ERROR
        except dryrun._Refused as exc:
            print(exc, file=sys.stderr)
            return EXIT_ERROR
    card = VERB_CARDS.get(path)
    if card is None:
        print(f"NOT IMPLEMENTED: {path}", file=sys.stderr)
        return EXIT_NOT_IMPLEMENTED
    print(f"NOT IMPLEMENTED: {path} (card {card})", file=sys.stderr)
    return EXIT_NOT_IMPLEMENTED
