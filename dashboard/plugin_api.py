"""product — read-only data routes for the desktop page (card HP-14a).

Mounted by the plugin system at ``/api/plugins/product/``. Everything the page
needs (DOC2 / A26-A33) is served from the Hermes store:

  GET  /info                              what this serves, db path, schema
  GET  /projects                          the switcher's projects (+ active flag)
  POST /projects                          the New project dialog — the ONE write
  GET  /project/{slug}/prd                the PRD document as markdown
  GET  /project/{slug}/states             workflow order per kind (DOC2 behaviour 5)
  GET  /project/{slug}/entries?kind=...   cards by state, with counts
  GET  /project/{slug}/entry/{ref}        one entry: fields, links, docs, history, body_chars
  GET  /project/{slug}/entry/{ref}/body   its body text, bounded (?section=H&max_chars=N)
  GET  /project/{slug}/graph              features, dependency edges, critical path

Read-only by construction: every GET opens ``product.db`` with ``mode=ro`` plus
``PRAGMA query_only = 1`` (and never creates it), and reuses the store's read
helpers (``list_projects``, ``list_entities``, ``get_entity``, ``history_of``,
``feature_graph``, ``db_path``) so no query is re-implemented. The only write is
``POST /projects`` (A33), which calls ``store.create_project``. A request can
never edit an entry: the page shows the CLI command instead.

Errors are compact JSON ``{"error": "<message>"}`` with 404 for an unknown
project or entry and 400 for bad input — never a traceback.
"""

from __future__ import annotations

import contextlib
import functools
import importlib
import importlib.util
import json
import re
import sqlite3
import sys
from pathlib import Path
from typing import Any, Iterator, Optional

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel

_PLUGIN_ROOT = Path(__file__).resolve().parent.parent
_PKG = "hermes_product_pkg"


def _plugin_package():
    """The plugin root as a PRIVATE package.

    The Hermes server runs every plugin in one interpreter, and other plugins (feature-graph)
    ship top-level ``store.py`` / ``graph.py`` too: a plain ``import store`` returns whichever
    loaded first. Importing through a uniquely named package never touches the bare names.
    """
    package = sys.modules.get(_PKG)
    if package is None:
        spec = importlib.util.spec_from_file_location(
            _PKG, _PLUGIN_ROOT / "__init__.py", submodule_search_locations=[str(_PLUGIN_ROOT)]
        )
        package = importlib.util.module_from_spec(spec)
        sys.modules[_PKG] = package
        try:
            spec.loader.exec_module(package)
        except Exception:
            sys.modules.pop(_PKG, None)
            raise
    return package


_plugin_package()
graph_model = importlib.import_module(f"{_PKG}.graph")
model = importlib.import_module(f"{_PKG}.model")
store = importlib.import_module(f"{_PKG}.store")
docs_text = importlib.import_module(f"{_PKG}.docs_text")

router = APIRouter()


def _apply_pending_migrations() -> None:
    """Bring the store to the schema this build expects, once, at import.

    Every reader below opens the db read-only, so nothing else would ever apply a pending
    migration — restarting the desktop app would leave the page reading yesterday's columns.
    A schema upgrade is the one write this module performs; a newer store (or a locked file)
    is left alone and the page still reads what it can.
    """
    if not Path(store.db_path()).exists():
        return                                          # a read never creates the store
    try:
        store.connect().close()
    except Exception:                                   # pragma: no cover - best effort
        import logging
        logging.getLogger("product.plugin_api").debug(
            "could not apply pending migrations", exc_info=True)


_apply_pending_migrations()

PLUGIN = "product"
VERSION = "0.1.0"

KINDS = ("requirement", "criterion", "feature", "spec", "doc")

#: DOC2 behaviour 5: the order the UI lists states in. Read from model.STATUSES,
#: except the criterion order, which is fixed: a failure is what you look at first.
CRITERION_ORDER = ("failed", "not_run", "passed", "waived")


def _workflow(kind: str) -> tuple[str, ...]:
    if kind == "criterion":
        rest = tuple(state for state in model.STATUSES[kind] if state not in CRITERION_ORDER)
        return CRITERION_ORDER + rest
    return tuple(model.STATUSES[kind])


WORKFLOW: dict[str, tuple[str, ...]] = {kind: _workflow(kind) for kind in KINDS}

#: the field whose text is the entry's one-line summary
SUMMARY_FIELD = {"requirement": "statement", "criterion": "text", "feature": "description",
                 "spec": "summary", "doc": "summary"}

#: how a doc's linked entries are counted
DOC_LINK_KINDS = ("requirement", "criterion", "feature", "spec")

#: S1 field order per kind for the detail panel (internal body_file/hash never shown)
FIELD_ORDER: dict[str, tuple[str, ...]] = {
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
    "doc": ("id", "title", "kind", "summary", "body_ref", "status", "superseded_by", "notes",
            "pinned", "is_prd", "updated_at"),
}

_FLAG_FIELDS = {"blocked", "legacy", "infra", "pinned", "is_prd"}
_SUMMARY_MAX = 160
_FLAT_RE = re.compile(r"\s+")


# ------------------------------------------------------------------ plumbing
def _error(status: int, message: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": message})


def _guarded(handler):
    """Turn an unexpected store failure into compact JSON instead of a traceback."""

    @functools.wraps(handler)
    def wrapper(*args, **kwargs):
        try:
            return handler(*args, **kwargs)
        except sqlite3.Error as exc:
            return _error(500, f"store unreadable: {exc}")
        except store.StoreError as exc:
            return _error(404, str(exc))
        except model.ValidationError as exc:
            return _error(400, str(exc))

    return wrapper


def _open_ro() -> Optional[sqlite3.Connection]:
    """A read-only connection to the store, or None when the db does not exist yet.

    ``mode=ro`` never creates the db. It does, however, make SQLite create its
    ``-shm``/``-wal`` bookkeeping files for a WAL-mode db, so a store with no WAL
    is opened ``immutable=1`` instead: with no ``-wal`` file every committed page
    is in the main db and nothing has to be created to read it.
    """
    path = store.db_path()
    if not path.is_file():
        return None
    has_wal = Path(f"{path}-wal").exists() or Path(f"{path}-shm").exists()
    uris = [f"file:{path}?mode=ro"] if has_wal else [
        f"file:{path}?mode=ro&immutable=1", f"file:{path}?mode=ro",
    ]
    failure: Optional[sqlite3.Error] = None
    for uri in uris:
        try:
            con = sqlite3.connect(uri, uri=True)
        except sqlite3.Error as exc:
            failure = exc
            continue
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA query_only = 1")
        return con
    raise failure if failure is not None else sqlite3.OperationalError("unreadable store")


@contextlib.contextmanager
def _reading() -> Iterator[Optional[sqlite3.Connection]]:
    con = _open_ro()
    try:
        yield con
    finally:
        if con is not None:
            con.close()


def _known_project(con: sqlite3.Connection, slug: str) -> Optional[dict]:
    return next((row for row in store.list_projects(con) if row["slug"] == slug), None)


def _active_slug(con: sqlite3.Connection) -> Optional[str]:
    try:
        row = con.execute("SELECT value FROM meta WHERE key = 'active_project'").fetchone()
    except sqlite3.Error:
        return None
    return row["value"] if row is not None else None


def _flat(value: Any) -> str:
    return _FLAT_RE.sub(" ", str(value or "")).strip()


def _summary(value: Any) -> str:
    """One line, at most 160 characters, with an ellipsis when it was cut."""
    text = _flat(value)
    if len(text) <= _SUMMARY_MAX:
        return text
    return text[:_SUMMARY_MAX - 1] + "\u2026"


def _json_paths(value: Any) -> list[str]:
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        parsed = [part for part in str(value).split(",") if part.strip()]
    if not isinstance(parsed, list):
        parsed = [str(parsed)]
    return [str(item) for item in parsed]


# ------------------------------------------------------------------------ /info
@router.get("/info")
@_guarded
def info() -> dict:
    path = store.db_path()
    schema = 0
    with _reading() as con:
        if con is not None:
            schema = int(con.execute("PRAGMA user_version").fetchone()[0])
    return {"plugin": PLUGIN, "version": VERSION, "db": str(path), "schema": schema}


# -------------------------------------------------------------------- /projects
@router.get("/projects")
@_guarded
def projects_list() -> dict:
    with _reading() as con:
        rows = store.list_projects(con) if con is not None else []
        active = _active_slug(con) if con is not None else None
    known = {row["slug"] for row in rows}
    active = active if active in known else None
    return {"projects": [
        {"slug": row["slug"], "name": row["name"], "root": row["root"],
         "active": row["slug"] == active}
        for row in rows
    ]}


class NewProject(BaseModel):
    """The New project dialog: slug and name required, root optional (A26)."""

    slug: Optional[str] = None
    name: Optional[str] = None
    root: Optional[str] = None


@router.post("/projects", status_code=201)
@_guarded
def projects_create(spec: NewProject):
    slug = str(spec.slug or "").strip()
    name = str(spec.name or "").strip()
    if not slug:
        return _error(400, "slug is required")
    if not name:
        return _error(400, "name is required")
    try:
        # the page's New project initialises: the project arrives with its PRD
        created = store.create_project(slug, name, root=spec.root or None, with_prd=True)
    except store.StoreError as exc:                    # bad or duplicate slug
        return _error(400, str(exc))
    return {"slug": created["slug"], "name": created["name"], "root": created["root"]}


# ------------------------------------------------------------------------- /prd
def _doc_markdown(project: dict, row: dict) -> str:
    """A DOC body in full: its stored file, or a LOCAL body_ref; a URL or none -> ''."""
    if row.get("body_file"):
        try:
            return store.doc_body_path(project["slug"], row).read_text(encoding="utf-8")
        except OSError:
            return ""
    body_ref = str(row.get("body_ref") or "").strip()
    if not body_ref or body_ref.startswith(("http://", "https://")):
        return ""
    path = Path(body_ref).expanduser()
    if not path.is_absolute():
        base = Path(project["root"]) if project.get("root") else Path.cwd()
        path = base / path
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


@router.get("/project/{slug}/prd")
@_guarded
def project_prd(slug: str):
    with _reading() as con:
        if con is None:
            return _error(404, "no such project")
        project = _known_project(con, slug)
        if project is None:
            return _error(404, "no such project")
        docs = [row for row in store.list_entities(slug, "doc", con=con)
                if row.get("doc_kind") == "prd"]
    if not docs:
        return {"doc": None, "markdown": "",
                "hint": 'hermes product add DOC --kind prd --title "<title>"'
                        ' --summary "<summary>"'}
    row = docs[0]
    return {"doc": row["ref"], "title": row["title"],
            "markdown": _doc_markdown(project, row)}


# ---------------------------------------------------------------------- /states
@router.get("/project/{slug}/states")
@_guarded
def project_states(slug: str):
    with _reading() as con:
        if con is None or _known_project(con, slug) is None:
            return _error(404, "no such project")
    return {
        "requirement": list(WORKFLOW["requirement"]),
        "feature": list(WORKFLOW["feature"]),
        "spec": list(WORKFLOW["spec"]),
        "criterion": list(WORKFLOW["criterion"]),
        "doc": list(WORKFLOW["doc"]),
    }


# --------------------------------------------------------------------- /entries
def _link_counts(kind: str, row: dict) -> dict:
    links = row.get("links") or {}
    if kind == "requirement":
        return {"features": len(links.get("features") or []),
                "specs": len(links.get("specs") or []),
                "criteria": len(links.get("criteria") or [])}
    if kind == "feature":
        return {"needs": len(links.get("needs") or []),
                "requirements": len(links.get("requirements") or []),
                "specs": len(links.get("specs") or [])}
    if kind == "spec":
        return {"requirements": len(links.get("requirements") or []),
                "features": len(links.get("features") or [])}
    if kind == "doc":
        counts = {name: 0 for name in DOC_LINK_KINDS}
        for link_kind, _link_id in row.get("doc_links") or []:
            if link_kind in counts:
                counts[link_kind] += 1
        return counts
    return {}


def _entry_card(kind: str, row: dict) -> dict:
    """One card of the page: id, title, status, one-line summary, link counts."""
    item = {
        "ref": row["ref"],
        "id": row["id"],
        "title": row.get("title") or "",
        "status": row.get("status"),
        "summary": _summary(row.get(SUMMARY_FIELD[kind])),
        "links": _link_counts(kind, row),
    }
    if kind == "requirement":
        item["priority"] = row.get("priority")
        item["phase"] = row.get("phase")
    elif kind == "feature":
        item["layer"] = row.get("layer")
        item["needs"] = list((row.get("links") or {}).get("needs") or [])
    elif kind == "criterion":
        item["requirement"] = (model.format_ref("requirement", row["requirement_id"])
                               if row.get("requirement_id") else None)
    elif kind == "doc":
        item["kind"] = row.get("doc_kind")
        # the two flags the card draws: a pin top-right, a PRD badge beside the state
        item["pinned"] = bool(int(row.get("pinned") or 0))
        item["is_prd"] = bool(int(row.get("is_prd") or 0))
    return item


@router.get("/project/{slug}/entries")
@_guarded
def project_entries(
    slug: str,
    kind: Optional[str] = Query(default=None, description="requirement|criterion|feature|spec|doc"),
    status: Optional[str] = Query(default=None, description="Only entries in this state"),
):
    if kind is None:
        return _error(400, f"kind is required: {'|'.join(KINDS)}")
    if kind not in KINDS:
        return _error(400, f"unknown kind {kind!r}: expected {'|'.join(KINDS)}")
    if status is not None and status not in WORKFLOW[kind]:
        return _error(400, f"{status!r} is not a state of {kind}"
                           f" ({'|'.join(WORKFLOW[kind])})")
    with _reading() as con:
        if con is None or _known_project(con, slug) is None:
            return _error(404, "no such project")
        rows = store.list_entities(slug, kind, con=con)

    # counts always cover every entry of the kind, whatever the filter says
    counts: dict[str, int] = {"all": len(rows)}
    for state in WORKFLOW[kind]:
        counts[state] = 0
    for row in rows:
        state = row.get("status")
        counts[state] = counts.get(state, 0) + 1

    order = {state: index for index, state in enumerate(WORKFLOW[kind])}
    selected = [row for row in rows if status is None or row.get("status") == status]

    def _rank(row: dict) -> tuple:
        # docs: pinned first, then the PRD, then the workflow order (the same rule the page
        # applies, so the CLI and the page agree on what "first" means)
        flags = 0 if int(row.get("is_prd") or 0) else (1 if int(row.get("pinned") or 0) else 2)
        return (flags, order.get(row.get("status"), len(order)), row["id"])

    selected.sort(key=_rank)
    return {"kind": kind, "counts": counts,
            "entries": [_entry_card(kind, row) for row in selected]}


# ----------------------------------------------------------------------- /entry
def _detail_fields(kind: str, row: dict) -> dict:
    """Every non-empty field in S1 order; flags shown as booleans, paths as a list."""
    fields: dict[str, Any] = {}
    for name in FIELD_ORDER[kind]:
        value = row.get(name)
        if name in _FLAG_FIELDS:
            if int(value or 0):
                fields[name] = True
            continue
        if value is None or value == "":
            continue
        if name == "evidence_level" and value == "none":
            continue
        if name == "paths":
            fields[name] = _json_paths(value)
            continue
        fields[name] = value
    return fields


def _doc_link_lists(entries: list[dict]) -> dict:
    grouped: dict[str, list[int]] = {name: [] for name in DOC_LINK_KINDS}
    for item in entries:
        link_kind = item.get("kind")
        if link_kind in grouped:
            grouped[link_kind].append(int(item["id"]))
    return {name: sorted(values) for name, values in grouped.items()}


@router.get("/project/{slug}/entry/{ref}")
@_guarded
def project_entry(slug: str, ref: str):
    with _reading() as con:
        project = _known_project(con, slug) if con is not None else None
        if project is None:
            return _error(404, "no such project")
        try:
            entry = store.get_entity(slug, ref, con=con)
        except model.ValidationError as exc:
            return _error(400, str(exc))
        except store.StoreError as exc:
            return _error(404, str(exc))
        kind = entry["kind"]
        body_chars = (len(_doc_markdown(project, entry["row"])) if kind == "doc"
                      else int(entry["row"].get("body_chars") or 0))
        links = _doc_link_lists(entry.get("doc_links") or []) if kind == "doc" else entry["links"]
        docs = [{"ref": model.format_ref("doc", doc["id"]), "kind": doc["kind"],
                 "title": doc["title"], "summary": doc["summary"], "status": doc["status"]}
                for doc in entry["docs"]]
        history = store.history_of(con, slug, kind, entry["id"], limit=20)
        fields = _detail_fields(kind, entry["row"])
    return {
        "ref": entry["ref"],
        "kind": kind,
        "fields": fields,
        "links": links,
        "docs": docs,
        "history": history,
        "body_chars": body_chars,
        "command": f"hermes product set {entry['ref']} key=value",
    }


#: the page's own cap for one body read; the CLI's `show --body` allows up to 100000
BODY_PAGE_MAX = 100000


@router.get("/project/{slug}/entry/{ref}/body")
@_guarded
def project_entry_body(slug: str, ref: str, section: Optional[str] = None,
                       max_chars: int = Query(20000, ge=1, le=BODY_PAGE_MAX)):
    """One entry's body text, bounded, optionally one Markdown section (read-only)."""
    with _reading() as con:
        project = _known_project(con, slug) if con is not None else None
        if project is None:
            return _error(404, "no such project")
        try:
            entry = store.get_entity(slug, ref, with_body=True, con=con)
        except model.ValidationError as exc:
            return _error(400, str(exc))
        except store.StoreError as exc:
            return _error(404, str(exc))
        kind = entry["kind"]
        if kind == "criterion":
            return _error(400, f"{entry['ref']} has no body")
        text = (_doc_markdown(project, entry["row"]) if kind == "doc"
                else (entry["row"].get("body") or ""))
    if section is not None:
        found = docs_text.section(text, section)
        if found is None:
            names = [name for _lvl, name, _a, _b in docs_text.headings(text)][:20]
            return _error(400, f"no section {section!r} in {entry['ref']}"
                               f" (headings: {', '.join(names) or 'none'})")
        text = found
    piece, total, truncated = docs_text.bounded(text, max_chars)
    return {"ref": entry["ref"], "section": section, "text": piece, "total": total,
            "truncated": truncated,
            "command": f"hermes product show {entry['ref']} --body"
                       + (f" --section {section!r}" if section else "")
                       + (f" --max-chars {BODY_PAGE_MAX}" if truncated else "")}


# ----------------------------------------------------------------------- /graph
@router.post("/project/{slug}/entry/{ref}/pin")
def pin_entry(slug: str, ref: str, pinned: bool = Query(default=True)) -> dict:
    """Pin or unpin one entry.

    The page's only write: ``pinned`` is a flag, not content, and it goes through
    ``store.update_entity`` like every other change, so validation and ``updated_at`` apply.
    """
    try:
        store.update_entity(slug, ref, {"pinned": bool(pinned)})
    except Exception as exc:  # noqa: BLE001 - a bad ref or flag is a 400, not a traceback
        return _error(400, str(exc))
    return {"ref": ref, "pinned": bool(pinned)}


@router.get("/project/{slug}/graph")
@_guarded
def project_graph(slug: str):
    with _reading() as con:
        if con is None or _known_project(con, slug) is None:
            return _error(404, "no such project")
        data = store.feature_graph(slug, con=con)
        layers = {
            row["id"]: {"status": row["status"], "layer": row["layer"]}
            for row in con.execute(
                "SELECT id, status, layer FROM feature WHERE project = ? ORDER BY id", (slug,)
            )
        }
    nodes = [
        {
            "id": feature_id,
            "ref": model.format_ref("feature", feature_id),
            "title": data["nodes"][feature_id],
            "status": layers.get(feature_id, {}).get("status"),
            "layer": layers.get(feature_id, {}).get("layer"),
        }
        for feature_id in sorted(data["nodes"])
    ]
    # an edge is (feature, needs); the page draws the dependency -> dependent
    edges = [
        {"from": model.format_ref("feature", needs_id),
         "to": model.format_ref("feature", feature_id),
         "optional": bool(optional)}
        for feature_id, needs_id, optional in data["edges"]
    ]
    path = graph_model.critical_path(
        data["nodes"], [(needs_id, feature_id) for feature_id, needs_id in data["mandatory"]]
    )
    return {"nodes": nodes, "edges": edges,
            "critical": [model.format_ref("feature", feature_id) for feature_id in path]}
