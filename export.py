"""Text export and import for the ``product`` plugin (card HP-5, spec S3).

The export is the copy that is committed to git: YAML rows plus one markdown
file per stored DOC body, byte-identical across runs of an unchanged store
(A21) and lossless back into an empty store (A22).

Format decisions this module owns (the brief's wording in parentheses):

* ``project.yaml``: slug, name, root (omitted when NULL), and ``counters`` —
  the ``R/A/F/S/DOC`` -> next id to allocate mapping, so never-reused ids
  survive a round trip.
* Entity files hold a list of rows ordered by id, keys in schema column order,
  ``None`` values omitted, 0/1 flags (blocked, legacy, infra) as true/false and
  ``paths`` as real YAML lists.
* ``docs.yaml`` entries name their body file under ``body`` and omit the stored
  ``body_file`` column; a body_ref doc exports ref+hash only (never the
  external file). The body markdown lives at ``docs/DOC<id>-<slug>.md`` with
  slug = lowercase title, non-alphanumerics -> '-', trimmed, max 40 chars, and
  is written byte-exact (read/written as bytes: no newline translation).
* Multi-line strings use YAML literal block style; a value literal style cannot
  represent (trailing whitespace, control characters, ...) falls back to a
  quoted scalar. Every dumped file is parsed back and compared before it is
  written; on mismatch everything is re-emitted double-quoted, so a value can
  never be silently mangled. ruamel.yaml does the emitting (deterministic).
* ``links.yaml``: one key per link table, each a sorted list of rows (flow
  style), ``optional`` as a bool; ``external_link`` rows are mappings of their
  columns. ``history.yaml``: oldest first by row id. No export timestamp is
  written anywhere.
* The export is built in memory and only then written. A non-empty directory
  without ``project.yaml`` is refused untouched; an earlier export is
  overwritten and its stale ``docs/*.md`` bodies deleted.

Import (``import DIR [--as SLUG]``) loads everything with its original ids
into a new project (counters and history verbatim), or merges into an existing
one: absent id -> insert, equal row -> skip, different row -> CONFLICT (never
overwritten). Links insert when absent and both ends exist, history rows
dedupe on (entity_type, entity_id, at, change), counters become
max(existing, imported). The whole import is one transaction and body files
move into place only after the commit, so a failure leaves db and disk
untouched. Validation errors name the file and entry and exit 1; conflicts
exit 3 after committing everything else.
"""
from __future__ import annotations

import copy
import io
import json
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional, Union

from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedSeq
from ruamel.yaml.error import YAMLError
from ruamel.yaml.scalarstring import DoubleQuotedScalarString, PreservedScalarString

try:  # package submodule (Hermes plugin loader)
    from . import model, store
except ImportError:  # top-level module (test suite)
    import model  # type: ignore[no-redef]
    import store  # type: ignore[no-redef]

# ------------------------------------------------------------------ export layout
#: entity file per kind, in the S3 layout order
ENTITY_FILES = (
    ("requirement", "requirements.yaml"),
    ("criterion", "criteria.yaml"),
    ("feature", "features.yaml"),
    ("spec", "specs.yaml"),
    ("doc", "docs.yaml"),
)

#: keys of one exported row: schema column order (project excluded; a doc's
#: stored body_file column is replaced by `body`, the exported markdown name)
EXPORT_COLUMNS: dict[str, tuple[str, ...]] = {
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
    "doc": ("id", "title", "kind", "summary", "body", "body_ref", "body_hash", "status",
            "superseded_by", "notes", "updated_at"),
}

#: 0/1 columns exported as true/false
FLAG_COLUMNS = {"blocked", "legacy", "infra"}

#: link tables and their columns (the order rows are sorted and written in)
LINK_TABLES = (
    ("feature_requirement", ("feature_id", "requirement_id")),
    ("spec_requirement", ("spec_id", "requirement_id")),
    ("feature_spec", ("feature_id", "spec_id")),
    ("feature_dependency", ("feature_id", "needs_id", "optional")),
    ("requirement_dependency", ("requirement_id", "needs_id")),
    ("spec_dependency", ("spec_id", "needs_id")),
    ("doc_link", ("doc_id", "entity_type", "entity_id")),
)
EXTERNAL_COLUMNS = ("entity_type", "entity_id", "system", "external_id", "url", "role")

#: the link-table name -> end (kind, column) map used for dangling-link checks
_LINK_ENDS = {
    "feature_requirement": (("feature", "feature_id"), ("requirement", "requirement_id")),
    "spec_requirement": (("spec", "spec_id"), ("requirement", "requirement_id")),
    "feature_spec": (("feature", "feature_id"), ("spec", "spec_id")),
    "feature_dependency": (("feature", "feature_id"), ("feature", "needs_id")),
    "requirement_dependency": (("requirement", "requirement_id"), ("requirement", "needs_id")),
    "spec_dependency": (("spec", "spec_id"), ("spec", "needs_id")),
    "doc_link": (("doc", "doc_id"), (None, "entity_type")),
}

_HISTORY_COLUMNS = ("entity_type", "entity_id", "change", "reason", "at")


def doc_slugify(title: Any) -> str:
    """``DOC<id>-<slug>.md`` slug: lowercase, non-alphanumerics -> '-', trimmed,
    max 40 chars."""
    chars = [char if char.isalnum() else "-" for char in str(title or "").lower()]
    return "".join(chars).strip("-")[:40]


# ---------------------------------------------------------------- YAML emitting
def _literal_ok(value: str) -> bool:
    """Can YAML literal block style carry ``value`` byte-exactly? Conservative:
    no trailing whitespace, no tabs, no leading whitespace on the first line."""
    if "\n" not in value or "\r" in value or "\t" in value:
        return False
    if value[:1] == " ":
        return False
    return all(line == line.rstrip(" ") for line in value.split("\n"))


def _transform(node: Any, scalar: Callable[[str], Any]) -> Any:
    """Rebuild ``node`` with every string value (never a key) passed through
    ``scalar``; flow-style sequences keep their style."""
    if isinstance(node, dict):
        return type(node)((key, _transform(value, scalar)) for key, value in node.items())
    if isinstance(node, list):
        rebuilt = type(node)(_transform(value, scalar) for value in node)
        if isinstance(node, CommentedSeq):
            rebuilt.fa.set_flow_style()
        return rebuilt
    return scalar(node) if isinstance(node, str) else node


def _emit(structure: Any) -> str:
    buffer = io.StringIO()
    yaml = YAML()
    yaml.width = 10 ** 6                      # never fold: deterministic bytes
    yaml.dump(structure, buffer)
    return buffer.getvalue()


def _yaml_bytes(structure: Any) -> bytes:
    """Deterministic YAML text that parses back to exactly ``structure``."""
    plain = copy.deepcopy(structure)
    for scalar in (lambda text: PreservedScalarString(text) if _literal_ok(text) else text,
                   lambda text: DoubleQuotedScalarString(text)):
        try:
            text = _emit(_transform(plain, scalar))
            if YAML(typ="safe").load(text) == structure:
                return text.encode("utf-8")
        except YAMLError:
            continue
    raise store.StoreError("export cannot represent a value losslessly")   # pragma: no cover


# ------------------------------------------------------------------ export writing
def _paths_list(value: Any) -> list:
    try:
        items = json.loads(value)
    except (TypeError, ValueError):
        items = [str(value)]
    return list(items) if isinstance(items, list) else [str(value)]


def export_row(kind: str, row: dict, *, body_name: Optional[str] = None) -> dict:
    """One stored row as its exported mapping: schema column order, None values
    omitted, flags as bools, paths as a real list."""
    out: dict[str, Any] = {}
    for field in EXPORT_COLUMNS[kind]:
        if kind == "doc" and field == "body":
            if body_name is not None:
                out["body"] = body_name
            continue
        value = row.get(field)
        if value is None:
            continue
        if field in FLAG_COLUMNS:
            out[field] = bool(int(value))
        elif field == "paths":
            out[field] = _paths_list(value)
        else:
            out[field] = value
    return out


def history_row(row: dict) -> dict:
    out: dict[str, Any] = {}
    for field in _HISTORY_COLUMNS:
        value = row.get(field)
        if value is not None:
            out[field] = value
    return out


def _flow_row(values: list) -> CommentedSeq:
    row = CommentedSeq(values)
    row.fa.set_flow_style()
    return row


def build_export(project: str, con: Optional[Any] = None) -> tuple[dict, dict]:
    """Every export file in memory: ``(relative path -> bytes, output counts)``."""
    own = con is None
    con = con or store.connect()
    try:
        if not store.project_exists(project, con):
            raise store.StoreError(f"project {project!r} does not exist")
        info = store.get_project(project, con)
        counters = {kind: 1 for kind in store.ID_KINDS}
        for row in con.execute("SELECT kind, next_id FROM id_counter WHERE project = ?",
                               (project,)):
            counters[row["kind"]] = int(row["next_id"])
        project_doc: dict[str, Any] = {"slug": project, "name": info["name"]}
        if info["root"] is not None:
            project_doc["root"] = info["root"]
        project_doc["counters"] = dict(counters)

        files: dict[str, bytes] = {}
        counts: dict[str, int] = {}
        for kind, filename in ENTITY_FILES:
            rows = [dict(row) for row in con.execute(
                f"SELECT * FROM {store.ENTITY_TABLE[kind]} WHERE project = ? ORDER BY id",
                (project,))]
            counts[kind] = len(rows)
            structures = []
            for row in rows:
                body_name = None
                if kind == "doc" and row.get("body_file"):
                    body_name = f"DOC{row['id']}-{doc_slugify(row.get('title'))}.md"
                    source = store.doc_body_path(project, row)
                    if not source.is_file():
                        raise store.StoreError(
                            f"DOC{row['id']} body file is missing: {source}")
                    files[f"docs/{body_name}"] = source.read_bytes()
                structures.append(export_row(kind, row, body_name=body_name))
            files[filename] = _yaml_bytes(structures)

        links: dict[str, Any] = {}
        for table, columns in LINK_TABLES:
            rows = con.execute(
                f"SELECT {', '.join(columns)} FROM {table} WHERE project = ?"
                f" ORDER BY {', '.join(columns)}", (project,))
            links[table] = [
                _flow_row([bool(value) if column == "optional" else value
                           for column, value in zip(columns, tuple(row))])
                for row in rows
            ]
        ext_rows = con.execute(
            f"SELECT {', '.join(EXTERNAL_COLUMNS)} FROM external_link WHERE project = ?"
            f" ORDER BY {', '.join(EXTERNAL_COLUMNS)}", (project,))
        links["external_link"] = [
            {column: value for column, value in zip(EXTERNAL_COLUMNS, tuple(row))
             if value is not None}
            for row in ext_rows
        ]
        files["links.yaml"] = _yaml_bytes(links)

        history = [history_row(dict(row)) for row in con.execute(
            "SELECT entity_type, entity_id, change, reason, at FROM history"
            " WHERE project = ? ORDER BY id", (project,))]
        files["history.yaml"] = _yaml_bytes(history)

        files["project.yaml"] = _yaml_bytes(project_doc)
        counts_out = {"requirements": counts["requirement"], "criteria": counts["criterion"],
                      "features": counts["feature"], "specs": counts["spec"],
                      "docs": counts["doc"]}
        return files, counts_out
    finally:
        if own:
            con.close()


def export_project(project: str, out_dir: Union[str, Path]) -> dict:
    """Write the export to ``out_dir`` (built in memory first; written only when
    nothing failed) and return the counts for the output line."""
    files, counts = build_export(project)
    out = Path(out_dir).expanduser()
    if out.exists():
        if any(out.iterdir()) and not (out / "project.yaml").exists():
            raise store.StoreError(
                f"export directory {out} is not empty and has no project.yaml;"
                " refusing to write")
    out.mkdir(parents=True, exist_ok=True)
    (out / "docs").mkdir(exist_ok=True)
    for name, payload in sorted(files.items()):
        (out / name).write_bytes(payload)
    wanted = {Path(name).name for name in files if name.startswith("docs/")}
    for path in (out / "docs").iterdir():
        if path.is_file() and path.suffix == ".md" and path.name not in wanted:
            path.unlink()
    return counts


# ------------------------------------------------------------------ import parsing
def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _load_yaml(path: Path, required: bool) -> Any:
    if not path.exists():
        if required:
            raise store.StoreError(f"{path.name}: missing (an export directory holds it)")
        return None
    try:
        with path.open(encoding="utf-8") as handle:
            return YAML(typ="safe").load(handle)
    except YAMLError as exc:
        raise store.StoreError(f"{path.name}: malformed YAML: {exc}") from exc
    except OSError as exc:
        raise store.StoreError(f"{path.name}: {exc}") from exc


def _entry_label(kind: str, raw: Any, index: int) -> str:
    if isinstance(raw, dict) and isinstance(raw.get("id"), int) and not isinstance(raw.get("id"), bool):
        return store.ref_for(kind, raw["id"])
    return f"entry #{index + 1}"


def _as_int(value: Any, where: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise store.StoreError(f"{where}: must be a positive integer, got {value!r}")
    return value


def _normalize_entity(kind: str, raw: Any, where: str) -> dict:
    """One imported row as a storage column dict (explicit Nones: absent columns
    become NULL, never a schema default — that is what keeps A22 byte-exact)."""
    if not isinstance(raw, dict):
        raise store.StoreError(f"{where}: entry must be a mapping, got {type(raw).__name__}")
    known = set(EXPORT_COLUMNS[kind])
    values: dict[str, Any] = {field: None for field in EXPORT_COLUMNS[kind]}
    if kind == "doc":
        values.pop("body")
        values["body_file"] = None
    for field, value in raw.items():
        if field not in known:
            raise store.StoreError(f"{where}: unknown field {field!r}")
        if value is None:
            continue
        if field == "id":
            values["id"] = _as_int(value, f"{where}: id")
        elif field == "requirement_id":
            values["requirement_id"] = _as_int(value, f"{where}: requirement_id")
        elif field == "superseded_by":
            values["superseded_by"] = _as_int(value, f"{where}: superseded_by")
        elif field in FLAG_COLUMNS:
            if value in (True, 0, 1):
                values[field] = int(value)
            else:
                raise store.StoreError(f"{where}: {field} must be true or false, got {value!r}")
        elif field == "paths":
            if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
                raise store.StoreError(f"{where}: paths must be a list of strings, got {value!r}")
            values["paths"] = json.dumps(list(value))
        elif field in ("body", "body_hash", "body_ref"):
            if not isinstance(value, str):
                raise store.StoreError(f"{where}: {field} must be text, got {value!r}")
            values[field] = value
        else:
            rule = model.FIELD_RULES[kind].get(field)
            if rule is not None and rule["type"] == model._ENUM:
                if value not in rule["values"]:
                    raise store.StoreError(
                        f"{where}: {field} must be one of {'|'.join(rule['values'])},"
                        f" got {value!r}")
                values[field] = value
            elif isinstance(value, str):
                values[field] = value
            else:
                raise store.StoreError(f"{where}: {field} must be text, got {value!r}")
    if values.get("id") is None:
        raise store.StoreError(f"{where}: id is required")
    if kind == "criterion" and values.get("requirement_id") is None:
        raise store.StoreError(f"{where}: requirement_id is required")
    if kind == "doc" and "body" in raw and "body_ref" in raw:
        raise store.StoreError(f"{where}: pass either body or body_ref, not both")
    return values


def _normalize_link_row(table: str, raw: Any, where: str) -> Any:
    if table == "external_link":
        if not isinstance(raw, dict):
            raise store.StoreError(f"{where}: entry must be a mapping")
        row = {}
        for field, value in raw.items():
            if field not in EXTERNAL_COLUMNS:
                raise store.StoreError(f"{where}: unknown column {field!r}")
            row[field] = value
        for field in ("entity_type", "entity_id", "system", "external_id"):
            if row.get(field) is None:
                raise store.StoreError(f"{where}: {field} is required")
        row["entity_id"] = _as_int(row["entity_id"], f"{where}: entity_id")
        return row
    if not isinstance(raw, (list, tuple)):
        raise store.StoreError(f"{where}: entry must be a list of column values")
    columns = dict(LINK_TABLES)[table] if table in dict(LINK_TABLES) else ()
    if table == "doc_link":
        columns = ("doc_id", "entity_type", "entity_id")
    if len(raw) != len(columns):
        raise store.StoreError(f"{where}: expects {len(columns)} values, got {len(raw)}")
    out = []
    for column, value in zip(columns, raw):
        if column == "optional":
            if value not in (True, 0, 1, False):
                raise store.StoreError(f"{where}: optional must be true or false, got {value!r}")
            out.append(int(value))
        elif column == "entity_type":
            if value not in ("requirement", "criterion", "feature", "spec", "doc"):
                raise store.StoreError(f"{where}: bad entity_type {value!r}")
            out.append(value)
        else:
            out.append(_as_int(value, f"{where}: {column}"))
    return out


def load_export(dir_path: Union[str, Path]) -> dict:
    """Parse and validate an export directory; every error names file and entry."""
    directory = Path(dir_path).expanduser()
    if not directory.is_dir():
        raise store.StoreError(f"{directory}: not a directory")
    project_doc = _load_yaml(directory / "project.yaml", required=True)
    if not isinstance(project_doc, dict):
        raise store.StoreError("project.yaml: must be a mapping")
    slug = project_doc.get("slug")
    if not isinstance(slug, str) or not re.match(store.SLUG_RULE, slug):
        raise store.StoreError(f"project.yaml: invalid slug {slug!r}: must match {store.SLUG_RULE}")
    name = project_doc.get("name")
    if not isinstance(name, str):
        raise store.StoreError("project.yaml: name is required")
    root = project_doc.get("root")
    if root is not None and not isinstance(root, str):
        raise store.StoreError("project.yaml: root must be text")
    counters = {kind: 1 for kind in store.ID_KINDS}
    raw_counters = project_doc.get("counters") or {}
    if not isinstance(raw_counters, dict):
        raise store.StoreError("project.yaml: counters must be a mapping")
    for kind, value in raw_counters.items():
        if kind not in counters:
            raise store.StoreError(f"project.yaml: unknown counter kind {kind!r}")
        counters[kind] = _as_int(value, f"project.yaml: counters.{kind}")

    entities: dict[str, list] = {}
    bodies: dict[int, tuple] = {}
    for kind, filename in ENTITY_FILES:
        raw_rows = _load_yaml(directory / filename, required=False)
        if raw_rows is None:
            raw_rows = []
        if not isinstance(raw_rows, list):
            raise store.StoreError(f"{filename}: must be a list of rows")
        rows = []
        for index, raw in enumerate(raw_rows):
            where = f"{filename} {_entry_label(kind, raw, index)}"
            values = _normalize_entity(kind, raw, where)
            if kind == "doc":
                body_name = raw.get("body")
                if body_name is not None:
                    if (Path(str(body_name)).name != str(body_name)
                            or str(body_name).startswith(".")):
                        raise store.StoreError(
                            f"{where}: body must name a file inside docs/, got {body_name!r}")
                    payload = (directory / "docs" / str(body_name))
                    if not payload.is_file():
                        raise store.StoreError(
                            f"{where}: body file docs/{body_name} is missing")
                    bodies[values["id"]] = (str(body_name), payload.read_bytes())
                elif values.get("body_ref") is not None:
                    bodies.pop(values["id"], None)
            rows.append(values)
        entities[kind] = rows

    links: dict[str, list] = {}
    raw_links = _load_yaml(directory / "links.yaml", required=False) or {}
    if not isinstance(raw_links, dict):
        raise store.StoreError("links.yaml: must be a mapping of link tables")
    known_tables = [table for table, _cols in LINK_TABLES] + ["external_link"]
    for table, raw_rows in raw_links.items():
        if table not in known_tables:
            raise store.StoreError(f"links.yaml: unknown link table {table!r}")
        if raw_rows is None:
            raw_rows = []
        if not isinstance(raw_rows, list):
            raise store.StoreError(f"links.yaml {table}: must be a list of rows")
        links[table] = [
            _normalize_link_row(table, raw, f"links.yaml {table} row #{i + 1}")
            for i, raw in enumerate(raw_rows)
        ]

    raw_history = _load_yaml(directory / "history.yaml", required=False) or []
    if not isinstance(raw_history, list):
        raise store.StoreError("history.yaml: must be a list of rows")
    history = []
    for index, raw in enumerate(raw_history):
        where = f"history.yaml entry #{index + 1}"
        if not isinstance(raw, dict):
            raise store.StoreError(f"{where}: entry must be a mapping")
        for field in raw:
            if field not in _HISTORY_COLUMNS:
                raise store.StoreError(f"{where}: unknown field {field!r}")
        row = {field: raw.get(field) for field in _HISTORY_COLUMNS}
        if row["entity_type"] not in ("requirement", "criterion", "feature", "spec", "doc"):
            raise store.StoreError(f"{where}: bad entity_type {row['entity_type']!r}")
        if row["entity_id"] is not None:
            row["entity_id"] = _as_int(row["entity_id"], f"{where}: entity_id")
        history.append(row)

    # two prd docs are refused inside the file itself (the merged view is checked later)
    prd_ids = [row["id"] for row in entities["doc"] if row.get("kind") == "prd"]
    if len(prd_ids) > 1:
        raise store.StoreError(
            f"docs.yaml DOC{prd_ids[1]}: a second prd document (DOC{prd_ids[0]} is already prd)")

    return {"slug": slug, "name": name, "root": root, "counters": counters,
            "entities": entities, "links": links, "external_links":
                links.get("external_link", []), "history": history, "bodies": bodies}


# ----------------------------------------------------------------- import applying
def _after_entity_rows() -> None:
    """Seam between the entity INSERTs and the link/history INSERTs (tests force
    a failure here to prove the import is atomic)."""
    return None


def _check_ends(data: dict, present: dict) -> None:
    """Every link row's ends must exist in the import or the store (dangling
    links are validation errors)."""
    for table, columns in LINK_TABLES:
        for row in data["links"].get(table, []):
            values = dict(zip(columns, row))
            where = f"links.yaml {table} row {list(row)}"
            for kind, column in _LINK_ENDS[table]:
                if kind is None:                      # doc_link entity side: dynamic
                    kind = values[column]
                    column = "entity_id"
                if values[column] not in present.get(kind, set()):
                    raise store.StoreError(
                        f"{where}: {store.ref_for(kind, values[column])} does not exist")
    for row in data["external_links"]:
        where = f"links.yaml external_link row {row}"
        kind = row["entity_type"]
        if kind not in ("requirement", "criterion", "feature", "spec"):
            raise store.StoreError(f"{where}: bad entity_type {kind!r}")
        if row["entity_id"] not in present.get(kind, set()):
            raise store.StoreError(
                f"{where}: {store.ref_for(kind, row['entity_id'])} does not exist")


def _first_difference(kind: str, stored: dict, incoming: dict) -> Optional[str]:
    for field in EXPORT_COLUMNS[kind]:
        # An incoming row with NO updated_at (import-md rows come from markdown, which
        # carries no timestamps) says nothing about it: a store that was edited since
        # (set stamps updated_at) must not conflict on that alone.
        if field == "updated_at" and incoming.get(field) is None:
            continue
        if stored.get(field) != incoming.get(field):
            return field
    return None


def import_export(dir_path: Union[str, Path], as_slug: Optional[str] = None) -> dict:
    """Import an export directory; returns {slug, inserted, skipped, conflicts,
    conflict_lines}. Validation errors raise StoreError (exit 1); conflicts are
    reported after the non-conflicting rows are committed (exit 3)."""
    data = load_export(dir_path)
    slug = str(as_slug) if as_slug else data["slug"]
    if not re.match(store.SLUG_RULE, slug):
        raise store.StoreError(f"invalid project slug {slug!r}: must match {store.SLUG_RULE}")
    con = store.connect()
    try:
        exists = store.project_exists(slug, con)
        present: dict[str, set] = {kind: set() for kind in model.KINDS}
        stored_ids: dict[str, set] = {kind: set() for kind in model.KINDS}
        stored_rows: dict[tuple, dict] = {}
        if exists:
            for kind in model.KINDS:
                for row in con.execute(
                        f"SELECT * FROM {store.ENTITY_TABLE[kind]} WHERE project = ?",
                        (slug,)):
                    stored_rows[(kind, row["id"])] = dict(row)
                    stored_ids[kind].add(row["id"])
                    present[kind].add(row["id"])
        for kind in model.KINDS:
            for row in data["entities"][kind]:
                present[kind].add(row["id"])
        _check_ends(data, present)

        # the merged doc view may hold only one prd
        doc_kinds = {doc_id: "prd" for doc_id in present["doc"]}
        for (kind_name, entity_id), row in stored_rows.items():
            if kind_name == "doc":
                doc_kinds[entity_id] = row.get("kind")
        for row in data["entities"]["doc"]:
            doc_kinds[row["id"]] = row.get("kind")
        prd_ids = sorted(doc_id for doc_id, kind in doc_kinds.items() if kind == "prd")
        if len(prd_ids) > 1:
            raise store.StoreError(
                f"docs.yaml DOC{prd_ids[1]}: a second prd document (DOC{prd_ids[0]} is"
                " already prd)")

        plan: dict[str, list] = {}
        conflict_lines: list[str] = []
        inserted = skipped = conflicts = 0
        bodies_to_stage: list = []
        for kind, _filename in ENTITY_FILES:
            inserts = []
            for row in data["entities"][kind]:
                body_name, payload = ((None, None) if kind != "doc"
                                      else data["bodies"].get(row["id"], (None, None)))
                comparison = export_row(kind, row, body_name=body_name)
                if not exists or row["id"] not in stored_ids[kind]:
                    inserts.append(row)
                    inserted += 1
                    if body_name is not None:
                        bodies_to_stage.append((row["id"], payload))
                    continue
                stored = dict(stored_rows[(kind, row["id"])])
                stored_comparison = export_row(kind, stored, body_name=body_name)
                field = _first_difference(kind, stored_comparison, comparison)
                if field is None and body_name is not None:
                    current = store.doc_body_path(slug, stored)
                    if not current.is_file() or current.read_bytes() != payload:
                        field = "body"
                if field is None:
                    skipped += 1
                else:
                    conflicts += 1
                    conflict_lines.append(
                        f"conflict {store.ref_for(kind, row['id'])}: {field} differs")
            plan[kind] = inserts

        # body files: written to temp names, moved into place only after the commit
        docs_dir = store.docs_dir(slug)
        made_dir = not docs_dir.exists()
        staged: list = []
        try:
            if bodies_to_stage:
                docs_dir.mkdir(parents=True, exist_ok=True)
                for doc_id, payload in bodies_to_stage:
                    handle, temp_name = tempfile.mkstemp(
                        dir=docs_dir, prefix=f".DOC{doc_id}.", suffix=".tmp")
                    with os.fdopen(handle, "wb") as stream:
                        stream.write(payload)
                    staged.append((Path(temp_name), docs_dir / f"DOC{doc_id}.md"))

            now = _now()
            with con:                                   # ONE transaction
                # self-referencing rows (superseded_by) insert in id order, so
                # foreign keys are only enforced at COMMIT
                con.execute("PRAGMA defer_foreign_keys = ON")
                if not exists:
                    con.execute(
                        "INSERT INTO project(slug, name, root, created_at, updated_at)"
                        " VALUES (?,?,?,?,?)", (slug, data["name"], data["root"], now, now))
                    for kind in store.ID_KINDS:
                        con.execute(
                            "INSERT INTO id_counter(project, kind, next_id) VALUES (?,?,?)",
                            (slug, kind, data["counters"][kind]))
                for kind, _filename in ENTITY_FILES:
                    for row in plan[kind]:
                        columns = ["project"]
                        values: list = [slug]
                        for field in EXPORT_COLUMNS[kind]:
                            if kind == "doc" and field == "body":
                                continue
                            columns.append(field)
                            values.append(row.get(field))
                        if kind == "doc":
                            body_name = data["bodies"].get(row["id"], (None, None))[0]
                            index = columns.index("body_ref")
                            columns.insert(index, "body_file")
                            values.insert(index,
                                          f"docs/DOC{row['id']}.md" if body_name else None)
                        placeholders = ", ".join("?" for _ in columns)
                        con.execute(
                            f"INSERT INTO {store.ENTITY_TABLE[kind]}"
                            f"({', '.join(columns)}) VALUES ({placeholders})",
                            tuple(values))
                _after_entity_rows()

                if exists:
                    for kind in store.ID_KINDS:
                        con.execute(
                            "INSERT INTO id_counter(project, kind, next_id) VALUES (?,?,?)"
                            " ON CONFLICT(project, kind) DO UPDATE"
                            " SET next_id = max(id_counter.next_id, excluded.next_id)",
                            (slug, kind, data["counters"][kind]))
                for table, columns in LINK_TABLES:
                    for row in data["links"].get(table, []):
                        where = " AND ".join(f"{column} = ?" for column in columns)
                        exists_row = con.execute(
                            f"SELECT 1 FROM {table} WHERE project = ? AND {where}",
                            (slug, *row)).fetchone()
                        if exists_row is None:
                            con.execute(
                                f"INSERT INTO {table}(project, {', '.join(columns)})"
                                f" VALUES (?{', ?' * len(columns)})",
                                (slug, *row))
                for row in data["external_links"]:
                    exists_row = con.execute(
                        "SELECT 1 FROM external_link WHERE project = ? AND entity_type = ?"
                        " AND entity_id = ? AND system = ? AND external_id = ?",
                        (slug, row.get("entity_type"), row["entity_id"],
                         row.get("system"), row.get("external_id"))).fetchone()
                    if exists_row is None:
                        con.execute(
                            "INSERT INTO external_link(project, entity_type, entity_id,"
                            " system, external_id, url, role) VALUES (?,?,?,?,?,?,?)",
                            (slug, row.get("entity_type"), row["entity_id"],
                             row.get("system"), row.get("external_id"),
                             row.get("url"), row.get("role")))
                seen = set()
                if exists:
                    seen = {tuple(row) for row in con.execute(
                        "SELECT entity_type, entity_id, at, change FROM history"
                        " WHERE project = ?", (slug,))}
                for row in data["history"]:
                    key = (row["entity_type"], row["entity_id"], row["at"], row["change"])
                    if key in seen:
                        continue
                    seen.add(key)
                    con.execute(
                        "INSERT INTO history(project, entity_type, entity_id, change,"
                        " reason, at) VALUES (?,?,?,?,?,?)",
                        (slug, row["entity_type"], row["entity_id"], row["change"],
                         row["reason"], row["at"]))
        except BaseException:
            for temp, _final in staged:
                temp.unlink(missing_ok=True)
            if made_dir and docs_dir.exists() and not any(docs_dir.iterdir()):
                docs_dir.rmdir()
            raise
        for temp, final in staged:
            os.replace(temp, final)
        return {"slug": slug, "inserted": inserted, "skipped": skipped,
                "conflicts": conflicts, "conflict_lines": conflict_lines}
    finally:
        con.close()
