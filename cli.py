"""The ``hermes product`` CLI.

Card HP-0 built the argparse tree with every verb of docs/specs/S4-tool-behavior.md
section 2 and made each one a loud stub. Card HP-1 implemented ``project add/list/use``.
Card HP-2 implements ``add``, ``show`` and ``list`` (STEP 1) and ``set`` (STEP 2):
entries are validated, every failure prints its message to stderr and returns 1.

Every remaining verb stays a loud stub: one stderr line
``NOT IMPLEMENTED: <verb path> (card HP-<n>)`` and exit code 2. The verb ->
implementing-card map lives in ``VERB_CARDS`` alone.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Optional

try:  # package submodule (Hermes plugin loader)
    from . import docs_text, graph, model, store
except ImportError:  # top-level module (test suite)
    import docs_text  # type: ignore[no-redef]
    import graph  # type: ignore[no-redef]
    import model  # type: ignore[no-redef]
    import store  # type: ignore[no-redef]

EXIT_OK = 0
EXIT_ERROR = 1
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

# Verbs this build actually implements (HP-1: the project verbs; HP-2: entities;
# HP-3: link/unlink and the feature graph; HP-4: DOC bodies).
IMPLEMENTED = {"project add", "project list", "project use", "add", "set", "show", "list",
               "link", "unlink", "graph", "doc read"}

SEP = "  "


def _add_tail(parser: argparse.ArgumentParser) -> None:
    """Accept and ignore anything after the verb (later cards add real options)."""
    parser.add_argument("rest", nargs=argparse.REMAINDER, help=argparse.SUPPRESS)


def _add_project_option(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--project", default=None, metavar="SLUG",
        help="Project slug (default: the project whose root contains the working directory)",
    )


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
    p_pa.add_argument("slug", help="Unique short key, lowercase, hyphenated")
    p_pa.add_argument("name", help="Display name")
    p_pa.add_argument("--root", default=None,
                      help="Directory of the code repository, used to pick the project"
                           " from a working directory")
    p_pl = psubs.add_parser("list", help="List projects")
    p_pu = psubs.add_parser("use", help="Pick a project")
    p_pu.add_argument("slug", help="Project slug")

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
                       help="prd|spec|context|research|design|api|runbook|other (DOC#)")
    p_add.add_argument("--body", default=None,
                       help="The document text, stored with the project (DOC#)")
    p_add.add_argument("--body-file", dest="body_file", default=None, metavar="PATH",
                       help="Copy this file in as the document body (DOC#)")
    p_add.add_argument("--body-ref", dest="body_ref", default=None, metavar="REF",
                       help="An external path or URL holding the body (DOC#)")

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
                       help="Replace a DOC body with this file (DOC#)")
    _add_project_option(p_set)
    p_link = subs.add_parser("link", help="Create a relation, or add a dependency edge")
    p_link.add_argument("ref", metavar="REF", help="R3, A21, F7, S2 or DOC1")
    p_link.add_argument("ref2", nargs="?", default=None, metavar="REF",
                        help="The other entry, for a cross-kind relation")
    p_link.add_argument("--needs", default=None, metavar="REF",
                        help="The entry this one depends on (same kind)")
    p_link.add_argument("--optional", action="store_true", default=False,
                        help="A feature dependency that does not block (F# only)")
    _add_project_option(p_link)
    p_unlink = subs.add_parser("unlink", help="Remove a relation, or remove a dependency edge")
    p_unlink.add_argument("ref", metavar="REF", help="R3, A21, F7, S2 or DOC1")
    p_unlink.add_argument("ref2", nargs="?", default=None, metavar="REF",
                          help="The other entry, for a cross-kind relation")
    p_unlink.add_argument("--needs", default=None, metavar="REF",
                          help="The dependency to remove (same kind)")
    p_unlink.add_argument("--optional", action="store_true", default=False,
                          help=argparse.SUPPRESS)
    _add_project_option(p_unlink)

    p_show = subs.add_parser("show", help="Full detail for one entry")
    p_show.add_argument("ref", metavar="REF", help="R3, A21, F7, S2 or F:slug")
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
            "set needs key=value pairs, or --body-file to replace a DOC# body"
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
    entry = store.get_entity(project, args.ref)
    kind, row = entry["kind"], entry["row"]
    for field in SHOW_ORDER[kind]:
        line = _show_line(kind, field, row.get(field))
        if line is not None:
            print(line)
    if kind == "doc":
        print(_doc_body_line(project, row))
        for item in entry["doc_links"]:
            print(_render_item(item))
        return EXIT_OK
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
    return EXIT_OK


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


_IMPLEMENTED_HANDLERS = {
    "project add": _project_add,
    "project list": _project_list,
    "project use": _project_use,
    "add": _cmd_add,
    "set": _cmd_set,
    "list": _cmd_list,
    "show": _cmd_show,
    "link": lambda args: _cmd_link(args, remove=False),
    "unlink": lambda args: _cmd_link(args, remove=True),
    "graph": _cmd_graph,
    "doc read": _cmd_doc_read,
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
        try:
            return handler(args)
        except (store.StoreError, model.ValidationError) as exc:
            print(exc, file=sys.stderr)
            return EXIT_ERROR
    card = VERB_CARDS.get(path)
    if card is None:
        print(f"NOT IMPLEMENTED: {path}", file=sys.stderr)
        return EXIT_NOT_IMPLEMENTED
    print(f"NOT IMPLEMENTED: {path} (card {card})", file=sys.stderr)
    return EXIT_NOT_IMPLEMENTED
