"""SQLite store for the ``product`` plugin (card HP-1).

One db holds many projects: ``<HERMES_HOME>/product/product.db``. The schema is
versioned in ``PRAGMA user_version``; v1 is the baseline (SCHEMA_V1 below),
created by the fresh-install path — never by a migration function. How to add a
migration:
  * APPEND one (target_version, one-line description, apply(con)) tuple to
    MIGRATIONS; target_version must be exactly the current latest + 1.
  * NEVER edit or delete an old tuple — an old db runs exactly the tuples it missed.
  * NEVER skip a version number — every intermediate version must stay buildable.
A fresh install runs the baseline and then the same tuples, so its column set
always ends identical to an upgraded db's.

This is a NEW store: there is no legacy-unversioned adoption. An empty db file
(user_version 0, no tables) is a fresh install -> create schema v1 and stamp it,
no backup; user_version 0 WITH existing tables is refused untouched.

The schema follows docs/specs/S1-data-model.md: every entity table has the
composite PRIMARY KEY (project, id) and every project-scoped reference is a
COMPOSITE FOREIGN KEY (project, x_id) REFERENCES table(project, id), so a link
can never cross projects.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional, Union

try:  # package submodule (Hermes plugin loader)
    from . import model
except ImportError:  # top-level module (test suite)
    import model  # type: ignore[no-redef]

DB_FILENAME = "product.db"

SCHEMA_V1 = """
CREATE TABLE IF NOT EXISTS project (
  slug       TEXT PRIMARY KEY
             CHECK (slug GLOB '[a-z0-9]*' AND slug NOT GLOB '*[^a-z0-9-]*'
                    AND length(slug) BETWEEN 1 AND 40),
  name       TEXT NOT NULL,
  root       TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS meta (
  key   TEXT PRIMARY KEY,
  value TEXT
);
CREATE TABLE IF NOT EXISTS id_counter (
  project TEXT NOT NULL REFERENCES project(slug) ON DELETE CASCADE,
  kind    TEXT NOT NULL CHECK (kind IN ('R','A','F','S','DOC')),
  next_id INTEGER NOT NULL,
  PRIMARY KEY (project, kind)
);
CREATE TABLE IF NOT EXISTS requirement (
  project        TEXT NOT NULL REFERENCES project(slug) ON DELETE CASCADE,
  id             INTEGER NOT NULL,
  title          TEXT,
  statement      TEXT,
  rationale      TEXT,
  priority       TEXT CHECK (priority IN ('must','should','could')),
  phase          TEXT,
  status         TEXT CHECK (status IN ('draft','approved','in_progress','done','dropped')),
  status_reason  TEXT,
  superseded_by  INTEGER,
  blocked        INTEGER NOT NULL DEFAULT 0,
  blocked_reason TEXT,
  hypothesis_ref TEXT,
  legacy         INTEGER NOT NULL DEFAULT 0,
  prd_ref        TEXT,
  notes          TEXT,
  updated_at     TEXT,
  PRIMARY KEY (project, id),
  FOREIGN KEY (project, superseded_by) REFERENCES requirement (project, id)
);
CREATE TABLE IF NOT EXISTS criterion (
  project        TEXT NOT NULL REFERENCES project(slug) ON DELETE CASCADE,
  id             INTEGER NOT NULL,
  requirement_id INTEGER NOT NULL,
  text           TEXT,
  status         TEXT CHECK (status IN ('not_run','passed','failed','waived')),
  evidence       TEXT,
  evidence_level TEXT DEFAULT 'none'
                 CHECK (evidence_level IN ('none','unit','integration','staging','production')),
  notes          TEXT,
  updated_at     TEXT,
  PRIMARY KEY (project, id),
  FOREIGN KEY (project, requirement_id) REFERENCES requirement (project, id)
);
CREATE TABLE IF NOT EXISTS feature (
  project        TEXT NOT NULL REFERENCES project(slug) ON DELETE CASCADE,
  id             INTEGER NOT NULL,
  slug           TEXT,
  title          TEXT,
  layer          TEXT CHECK (layer IN ('foundation','core','growth')),
  status         TEXT CHECK (status IN ('planned','in_progress','shipped','cut')),
  status_reason  TEXT,
  phase          TEXT,
  description    TEXT,
  infra          INTEGER NOT NULL DEFAULT 0,
  paths          TEXT,
  blocked        INTEGER NOT NULL DEFAULT 0,
  blocked_reason TEXT,
  notes          TEXT,
  updated_at     TEXT,
  PRIMARY KEY (project, id),
  UNIQUE (project, slug)
);
CREATE TABLE IF NOT EXISTS spec (
  project        TEXT NOT NULL REFERENCES project(slug) ON DELETE CASCADE,
  id             INTEGER NOT NULL,
  title          TEXT,
  summary        TEXT,
  status         TEXT CHECK (status IN ('draft','approved','implemented','verified','superseded')),
  status_reason  TEXT,
  superseded_by  INTEGER,
  paths          TEXT,
  blocked        INTEGER NOT NULL DEFAULT 0,
  blocked_reason TEXT,
  notes          TEXT,
  updated_at     TEXT,
  PRIMARY KEY (project, id),
  FOREIGN KEY (project, superseded_by) REFERENCES spec (project, id)
);
CREATE TABLE IF NOT EXISTS doc (
  project       TEXT NOT NULL REFERENCES project(slug) ON DELETE CASCADE,
  id            INTEGER NOT NULL,
  title         TEXT,
  kind          TEXT CHECK (kind IN ('prd','spec','context','research','design','api','runbook','other')),
  summary       TEXT,
  body_file     TEXT,
  body_ref      TEXT,
  body_hash     TEXT,
  status        TEXT CHECK (status IN ('current','superseded')),
  superseded_by INTEGER,
  notes         TEXT,
  updated_at    TEXT,
  PRIMARY KEY (project, id),
  FOREIGN KEY (project, superseded_by) REFERENCES doc (project, id)
);
CREATE TABLE IF NOT EXISTS feature_requirement (
  project        TEXT NOT NULL,
  feature_id     INTEGER NOT NULL,
  requirement_id INTEGER NOT NULL,
  PRIMARY KEY (project, feature_id, requirement_id),
  FOREIGN KEY (project, feature_id) REFERENCES feature (project, id),
  FOREIGN KEY (project, requirement_id) REFERENCES requirement (project, id)
);
CREATE TABLE IF NOT EXISTS spec_requirement (
  project        TEXT NOT NULL,
  spec_id        INTEGER NOT NULL,
  requirement_id INTEGER NOT NULL,
  PRIMARY KEY (project, spec_id, requirement_id),
  FOREIGN KEY (project, spec_id) REFERENCES spec (project, id),
  FOREIGN KEY (project, requirement_id) REFERENCES requirement (project, id)
);
CREATE TABLE IF NOT EXISTS feature_spec (
  project    TEXT NOT NULL,
  feature_id INTEGER NOT NULL,
  spec_id    INTEGER NOT NULL,
  PRIMARY KEY (project, feature_id, spec_id),
  FOREIGN KEY (project, feature_id) REFERENCES feature (project, id),
  FOREIGN KEY (project, spec_id) REFERENCES spec (project, id)
);
CREATE TABLE IF NOT EXISTS feature_dependency (
  project    TEXT NOT NULL,
  feature_id INTEGER NOT NULL,
  needs_id   INTEGER NOT NULL,
  optional   INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (project, feature_id, needs_id),
  FOREIGN KEY (project, feature_id) REFERENCES feature (project, id),
  FOREIGN KEY (project, needs_id) REFERENCES feature (project, id)
);
CREATE TABLE IF NOT EXISTS requirement_dependency (
  project        TEXT NOT NULL,
  requirement_id INTEGER NOT NULL,
  needs_id       INTEGER NOT NULL,
  PRIMARY KEY (project, requirement_id, needs_id),
  FOREIGN KEY (project, requirement_id) REFERENCES requirement (project, id),
  FOREIGN KEY (project, needs_id) REFERENCES requirement (project, id)
);
CREATE TABLE IF NOT EXISTS spec_dependency (
  project  TEXT NOT NULL,
  spec_id  INTEGER NOT NULL,
  needs_id INTEGER NOT NULL,
  PRIMARY KEY (project, spec_id, needs_id),
  FOREIGN KEY (project, spec_id) REFERENCES spec (project, id),
  FOREIGN KEY (project, needs_id) REFERENCES spec (project, id)
);
CREATE TABLE IF NOT EXISTS doc_link (
  project     TEXT NOT NULL,
  doc_id      INTEGER NOT NULL,
  entity_type TEXT NOT NULL,
  entity_id   INTEGER NOT NULL,
  PRIMARY KEY (project, doc_id, entity_type, entity_id),
  FOREIGN KEY (project, doc_id) REFERENCES doc (project, id)
);
CREATE TABLE IF NOT EXISTS external_link (
  project     TEXT NOT NULL,
  entity_type TEXT NOT NULL,
  entity_id   INTEGER NOT NULL,
  system      TEXT NOT NULL,
  external_id TEXT NOT NULL,
  url         TEXT,
  role        TEXT,
  PRIMARY KEY (project, entity_type, entity_id, system, external_id)
);
CREATE TABLE IF NOT EXISTS history (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  project     TEXT NOT NULL,
  entity_type TEXT NOT NULL,
  entity_id   INTEGER NOT NULL,
  change      TEXT,
  reason      TEXT,
  at          TEXT
);
"""

# ---------------------------------------------------------------- schema versioning

MIGRATIONS: list[tuple[int, str, Callable[[sqlite3.Connection], None]]] = []

def latest_version() -> int:
    """Newest schema version this build understands: baseline v1, or the last migration."""
    return max([1, *(target for target, _description, _apply in MIGRATIONS)])

LATEST_VERSION = latest_version()


class StoreError(Exception):
    """Bad slug, missing project, or a db this build refuses to touch."""


def hermes_home() -> Path:
    env = os.environ.get("HERMES_HOME")
    return Path(env).expanduser() if env else Path.home() / ".hermes"


def db_path() -> Path:
    return hermes_home() / "product" / DB_FILENAME


def connect(path: Union[str, Path, None] = None) -> sqlite3.Connection:
    target = Path(path) if path is not None else db_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(target))
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    # Version gate BEFORE any writing pragma: a db from a newer plugin must come out
    # byte-identical (journal_mode = WAL rewrites the file header, so it waits).
    version = con.execute("PRAGMA user_version").fetchone()[0]
    latest = latest_version()
    if version > latest:
        con.close()
        raise StoreError(
            f"product db is schema v{version}, this plugin understands up to v{latest};"
            " upgrade the plugin (db untouched)"
        )
    if version == 0:
        version, fresh_install = _init_unversioned(con)   # fresh install -> v1
    else:
        fresh_install = False
    if version < latest:
        _apply_migrations(con, target, version, backup=not fresh_install)
    con.execute("PRAGMA journal_mode = WAL")
    return con


def _init_unversioned(con: sqlite3.Connection) -> tuple[int, bool]:
    """A ``user_version = 0`` db is either a fresh install or untouchable.

    Empty file (no tables) -> create the v1 baseline and stamp it (fresh install,
    nothing to back up). Tables already present -> StoreError, db untouched: this
    is a NEW store and adopts nothing.
    """
    tables = sorted(
        row["name"] for row in con.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
        )
    )
    if tables:
        con.close()
        raise StoreError(
            "product db has tables but no schema version; refusing to touch it"
            f" (db untouched): {', '.join(tables)}"
        )
    con.executescript(SCHEMA_V1)          # fresh install == the v1 baseline
    con.execute("PRAGMA user_version = 1")
    return 1, True


def _backup_db(con: sqlite3.Connection, db_file: Path, version: int) -> Path:
    """Checkpoint the WAL, then copy the db to ``<db>.bak-v<version>`` (never overwriting)."""
    con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    dest = db_file.with_name(f"{db_file.name}.bak-v{version}")
    suffix = 1
    while dest.exists():
        dest = db_file.with_name(f"{db_file.name}.bak-v{version}-{suffix}")
        suffix += 1
    shutil.copy2(db_file, dest)
    return dest


def _apply_migrations(con: sqlite3.Connection, db_file: Path, from_version: int,
                      *, backup: bool = True) -> None:
    """Apply every migration above ``from_version``, backing up before the first one.

    ``backup=False`` only for a fresh install created moments earlier in the same
    ``connect()`` — there is nothing to lose. Each migration runs in ONE transaction
    together with its ``user_version`` bump, so a failure rolls back both the DDL and
    the version stamp.
    """
    pending = sorted((m for m in MIGRATIONS if m[0] > from_version), key=lambda m: m[0])
    if not pending:
        return
    backup_path = _backup_db(con, db_file, from_version) if backup else None
    original_isolation = con.isolation_level
    con.isolation_level = None   # explicit BEGIN/COMMIT: DDL must roll back atomically
    last = from_version
    for target, description, apply_migration in pending:
        try:
            con.execute("BEGIN IMMEDIATE")
            apply_migration(con)
            con.execute(f"PRAGMA user_version = {int(target)}")
            con.execute("COMMIT")
        except Exception as exc:  # noqa: BLE001 - any failure must abort cleanly
            try:
                con.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            con.close()
            where = f"; backup at {backup_path}" if backup_path else ""
            raise StoreError(
                f"migration to v{target} failed: {exc}; db restored to v{last}{where}"
            ) from exc
        last = target
    con.isolation_level = original_isolation


def schema_info(con: Optional[sqlite3.Connection] = None) -> dict:
    """Store version report: {"version": v, "latest": LATEST_VERSION, "path": str(db_path)}."""
    own = con is None
    if own:
        target = db_path()
        if not target.exists():
            return {"version": 0, "latest": latest_version(), "path": str(target)}
        con = sqlite3.connect(str(target))
    try:
        version = con.execute("PRAGMA user_version").fetchone()[0]
    finally:
        if own:
            con.close()
    return {"version": version, "latest": latest_version(), "path": str(db_path())}


# ------------------------------------------------------------------- projects CRUD

# The one slug rule, in Python and as a GLOB CHECK on the project table.
SLUG_RULE = "^[a-z0-9][a-z0-9-]{0,39}$"
_SLUG_RE = re.compile(SLUG_RULE)

# id_counter kinds: the S1 id prefixes.
ID_KINDS = ("R", "A", "F", "S", "DOC")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def create_project(slug: str, name: str, root: Union[str, Path, None] = None,
                   con: Optional[sqlite3.Connection] = None) -> dict:
    """Create a project; a duplicate slug or an invalid one raises StoreError (nothing created)."""
    clean = str(slug or "")
    if not _SLUG_RE.match(clean):
        raise StoreError(f"invalid project slug {clean!r}: must match {SLUG_RULE}")
    root_str = str(Path(root).expanduser().resolve()) if root else None
    own = con is None
    con = con or connect()
    try:
        if con.execute("SELECT 1 FROM project WHERE slug = ?", (clean,)).fetchone():
            raise StoreError(f"project {clean!r} already exists")
        now = _now()
        con.execute(
            "INSERT INTO project(slug, name, root, created_at, updated_at) VALUES (?,?,?,?,?)",
            (clean, str(name), root_str, now, now),
        )
        con.commit()
        return {"slug": clean, "name": str(name), "root": root_str}
    finally:
        if own:
            con.close()


def list_projects(con: Optional[sqlite3.Connection] = None) -> list:
    own = con is None
    con = con or connect()
    try:
        rows = con.execute("SELECT slug, name, root FROM project ORDER BY slug").fetchall()
        return [{"slug": row["slug"], "name": row["name"], "root": row["root"]} for row in rows]
    finally:
        if own:
            con.close()


def get_project(slug: str, con: Optional[sqlite3.Connection] = None) -> Optional[dict]:
    """The project dict, or None when the slug is unknown."""
    own = con is None
    con = con or connect()
    try:
        row = con.execute("SELECT slug, name, root FROM project WHERE slug = ?", (slug,)).fetchone()
        return {"slug": row["slug"], "name": row["name"], "root": row["root"]} if row else None
    finally:
        if own:
            con.close()


def project_exists(slug: str, con: Optional[sqlite3.Connection] = None) -> bool:
    own = con is None
    con = con or connect()
    try:
        return con.execute("SELECT 1 FROM project WHERE slug = ?", (slug,)).fetchone() is not None
    finally:
        if own:
            con.close()


def set_active_project(slug: str, con: Optional[sqlite3.Connection] = None) -> None:
    """Remember the project `hermes product project use` picked (meta.active_project)."""
    own = con is None
    con = con or connect()
    try:
        if not project_exists(slug, con):
            known = [p["slug"] for p in list_projects(con)]
            raise StoreError(
                f"project {slug!r} does not exist (known: {', '.join(known) or 'none'})"
            )
        con.execute(
            "INSERT INTO meta(key, value) VALUES ('active_project', ?)"
            " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (slug,),
        )
        con.commit()
    finally:
        if own:
            con.close()


def get_active_project(con: Optional[sqlite3.Connection] = None) -> Optional[str]:
    """The slug set by `project use`, or None. Stale slugs are ignored."""
    own = con is None
    con = con or connect()
    try:
        row = con.execute("SELECT value FROM meta WHERE key = 'active_project'").fetchone()
        slug = row["value"] if row else None
        if slug and not project_exists(slug, con):
            return None
        return slug
    finally:
        if own:
            con.close()


def next_id(con: sqlite3.Connection, project: str, kind: str, *, commit: bool = True) -> int:
    """Atomically allocate the next id of ``kind`` for ``project``; starts at 1.

    The counter only ever grows: an id is NEVER reused, even if the row that
    used it was deleted. ``commit=False`` leaves the allocation in the caller's
    open transaction, so an entity is created (id + row) atomically — a failure
    rolls both back and leaves no gap.
    """
    if kind not in ID_KINDS:
        raise StoreError(f"id kind {kind!r} not in {'/'.join(ID_KINDS)}")
    if not project_exists(project, con):
        raise StoreError(f"project {project!r} does not exist")
    row = con.execute(
        "INSERT INTO id_counter(project, kind, next_id) VALUES (?, ?, 2)"
        " ON CONFLICT(project, kind) DO UPDATE SET next_id = id_counter.next_id + 1"
        " RETURNING next_id",
        (project, kind),
    ).fetchone()
    if commit:
        con.commit()
    return row[0] - 1


def resolve_project(slug: Optional[str] = None, con: Optional[sqlite3.Connection] = None, *,
                    cwd: Union[str, Path, None] = None) -> str:
    """Which project the current work belongs to; returns its slug.

    Order: (1) the explicit slug; (2) the project whose root equals or contains
    ``cwd`` (path-segment containment, so /a/proj does not match /a/proj-other;
    longest root wins; a tie among equal roots falls through); (3) the project
    set by ``project use``; (4) the only project; (5) an error listing them all.
    """
    own = con is None
    con = con or connect()
    try:
        if slug is not None:
            if not project_exists(slug, con):
                known = [p["slug"] for p in list_projects(con)]
                raise StoreError(
                    f"project {slug!r} does not exist (known: {', '.join(known) or 'none'})"
                )
            return slug
        projects = list_projects(con)
        if not projects:
            raise StoreError("no projects yet - hermes product project add <slug> <name>")
        here = Path(cwd if cwd is not None else Path.cwd()).expanduser().resolve()
        matches: list = []   # (root length, project slug)
        for project in projects:
            if not project["root"]:
                continue
            root = Path(project["root"]).expanduser().resolve()
            if here.is_relative_to(root):
                matches.append((len(str(root)), project["slug"]))
        if matches:
            longest = max(length for length, _slug in matches)
            best = [pslug for length, pslug in matches if length == longest]
            if len(best) == 1:
                return best[0]
        active = get_active_project(con)
        if active:
            return active
        if len(projects) == 1:
            return projects[0]["slug"]
        listed = ", ".join(f"{p['slug']} [{p['root'] or 'no root'}]" for p in projects)
        raise StoreError(
            f"several projects ({listed}) - pass --project <slug> or run:"
            " hermes product project use <slug>"
        )
    finally:
        if own:
            con.close()


# ------------------------------------------------------------------- entities (HP-2)
# History rows use these spellings (S1 section 9); the display prefixes live in model.
ENTITY_TABLE = {
    "requirement": "requirement",
    "criterion": "criterion",
    "feature": "feature",
    "spec": "spec",
    "doc": "doc",
}

#: list order when no kind is given (S4 section 1 examples: R, then F, then S, then A)
LIST_ORDER = ("requirement", "feature", "spec", "criterion")


def ref_for(kind: str, entity_id: int) -> str:
    """``("requirement", 3)`` -> ``R3``."""
    return model.format_ref(model.kind_for(kind), entity_id)


def _select_row(con: sqlite3.Connection, project: str, kind: str,
                entity_id: int) -> Optional[dict]:
    row = con.execute(
        f"SELECT * FROM {ENTITY_TABLE[kind]} WHERE project = ? AND id = ?",
        (project, entity_id),
    ).fetchone()
    return dict(row) if row is not None else None


def resolve_entity(project: str, ref: str, *,
                   con: Optional[sqlite3.Connection] = None) -> tuple[str, int]:
    """``"R3"`` / ``"F:stripe"`` -> ``("requirement", 3)`` / ``("feature", 7)``.

    Strict: the ref must name a row of THIS project (cross-project refs never resolve).
    """
    own = con is None
    con = con or connect()
    try:
        if not project_exists(project, con):
            raise StoreError(f"project {project!r} does not exist")
        kind, identifier = model.parse_ref(ref)
        if isinstance(identifier, str):                    # F:slug
            row = con.execute(
                "SELECT id FROM feature WHERE project = ? AND slug = ?",
                (project, identifier),
            ).fetchone()
            if row is None:
                raise StoreError(f"no feature with slug {identifier!r} in project {project!r}")
            return "feature", row["id"]
        if _select_row(con, project, kind, identifier) is None:
            raise StoreError(
                f"{model.format_ref(kind, identifier)} does not exist in project {project!r}"
            )
        return kind, identifier
    finally:
        if own:
            con.close()


def _resolve_link(con: sqlite3.Connection, project: str, target_kind: str,
                  value: Any, field_name: str) -> int:
    """Turn a ref/name/id into an id of ``target_kind`` that exists in ``project``."""
    if value is None or value == "":
        raise model.ValidationError(f"{field_name} is required")
    if isinstance(value, bool):
        raise model.ValidationError(f"{field_name} must be a reference, got {value!r}")
    if isinstance(value, int):
        identifier: Any = value
    else:
        kind, identifier = model.parse_ref(value)          # ValidationError on bad shape
        if kind != target_kind or isinstance(identifier, str):
            raise model.ValidationError(
                f"{field_name} must be a {target_kind} reference like "
                f"{model.format_ref(target_kind, 3)}, got {value!r}"
            )
    exists = con.execute(
        f"SELECT 1 FROM {ENTITY_TABLE[target_kind]} WHERE project = ? AND id = ?",
        (project, identifier),
    ).fetchone()
    if exists is None:
        raise model.ValidationError(
            f"{field_name} {model.format_ref(target_kind, identifier)} does not exist"
            f" in project {project!r}"
        )
    return identifier


def _check_feature_slug(con: sqlite3.Connection, project: str, slug: str,
                        *, exclude_id: Optional[int] = None) -> None:
    row = con.execute(
        "SELECT id FROM feature WHERE project = ? AND slug = ?", (project, slug)
    ).fetchone()
    if row is not None and row["id"] != exclude_id:
        raise model.ValidationError(f"slug {slug!r} already exists in project {project!r}")


def _insert_row(con: sqlite3.Connection, project: str, kind: str, entity_id: int,
                values: dict) -> None:
    """The one INSERT of a new entity row (a seam the tests can force to fail)."""
    columns = ["project", "id", *values]
    placeholders = ", ".join("?" for _ in columns)
    con.execute(
        f"INSERT INTO {ENTITY_TABLE[kind]}({', '.join(columns)}) VALUES ({placeholders})",
        (project, entity_id, *values.values()),
    )


def create_entity(project: str, kind: str, fields: dict,
                  con: Optional[sqlite3.Connection] = None) -> dict:
    """Create an R#/A#/F#/S# row: validate, then allocate the id and insert atomically.

    Validation and cross-reference checks run BEFORE the id is allocated, so a
    refused payload consumes no id; the allocation and the row sit in ONE
    transaction, so a failure after the allocation leaves no gap and no row.
    """
    kind = model.kind_for(kind)
    if kind == "doc":
        raise StoreError("NOT IMPLEMENTED: add DOC (card HP-4)")
    own = con is None
    con = con or connect()
    try:
        if not project_exists(project, con):
            raise StoreError(f"project {project!r} does not exist")
        values = dict(fields)
        if kind == "criterion":
            values["requirement_id"] = _resolve_link(
                con, project, "requirement", values.get("requirement_id"), "requirement_id"
            )
        if kind == "feature" and values.get("slug"):
            _check_feature_slug(con, project, str(values["slug"]))
        values = model.validate_new(kind, values)
        if isinstance(values.get("paths"), (list, tuple)):
            values["paths"] = json.dumps(list(values["paths"]))
        values["updated_at"] = _now()
        with con:                                  # id + row: one transaction
            new_id = next_id(con, project, model.PREFIX[kind], commit=False)
            _insert_row(con, project, kind, new_id, values)
    finally:
        if own:
            con.close()
    return {"kind": kind, "id": new_id, "ref": model.format_ref(kind, new_id)}


# ---------------------------------------------------------------- reading entities
def _id_column(con: sqlite3.Connection, sql: str, params: tuple) -> list[int]:
    return [row[0] for row in con.execute(sql, params)]


def links_of(con: sqlite3.Connection, project: str, kind: str, entity_id: int) -> dict:
    """The link tables as id lists (HP-3 fills them; here they are read only).

    Requirement: features, specs, criteria, criteria_total, criteria_passed.
    Feature: needs, requirements, specs. Spec: requirements, features.
    """
    if kind == "requirement":
        criteria = con.execute(
            "SELECT status FROM criterion WHERE project = ? AND requirement_id = ?",
            (project, entity_id),
        ).fetchall()
        return {
            "features": _id_column(
                con,
                "SELECT feature_id FROM feature_requirement"
                " WHERE project = ? AND requirement_id = ? ORDER BY feature_id",
                (project, entity_id),
            ),
            "specs": _id_column(
                con,
                "SELECT spec_id FROM spec_requirement"
                " WHERE project = ? AND requirement_id = ? ORDER BY spec_id",
                (project, entity_id),
            ),
            "criteria": _id_column(
                con,
                "SELECT id FROM criterion WHERE project = ? AND requirement_id = ? ORDER BY id",
                (project, entity_id),
            ),
            "criteria_total": len(criteria),
            "criteria_passed": sum(1 for row in criteria
                                   if row["status"] in ("passed", "waived")),
        }
    if kind == "feature":
        return {
            "needs": _id_column(
                con,
                "SELECT needs_id FROM feature_dependency"
                " WHERE project = ? AND feature_id = ? ORDER BY needs_id",
                (project, entity_id),
            ),
            "requirements": _id_column(
                con,
                "SELECT requirement_id FROM feature_requirement"
                " WHERE project = ? AND feature_id = ? ORDER BY requirement_id",
                (project, entity_id),
            ),
            "specs": _id_column(
                con,
                "SELECT spec_id FROM feature_spec"
                " WHERE project = ? AND feature_id = ? ORDER BY spec_id",
                (project, entity_id),
            ),
        }
    if kind == "spec":
        return {
            "requirements": _id_column(
                con,
                "SELECT requirement_id FROM spec_requirement"
                " WHERE project = ? AND spec_id = ? ORDER BY requirement_id",
                (project, entity_id),
            ),
            "features": _id_column(
                con,
                "SELECT feature_id FROM feature_spec"
                " WHERE project = ? AND spec_id = ? ORDER BY feature_id",
                (project, entity_id),
            ),
        }
    return {}


def history_of(con: sqlite3.Connection, project: str, kind: str, entity_id: int,
               *, limit: int = 10) -> list[dict]:
    """The last ``limit`` history rows, newest first."""
    rows = con.execute(
        "SELECT at, change, reason FROM history"
        " WHERE project = ? AND entity_type = ? AND entity_id = ?"
        " ORDER BY id DESC LIMIT ?",
        (project, kind, entity_id, limit),
    ).fetchall()
    return [{"at": row["at"], "change": row["change"], "reason": row["reason"]} for row in rows]


def _children_of(con: sqlite3.Connection, project: str, kind: str,
                 entity_id: int) -> list[dict]:
    """Child records: a requirement's criteria (A#), in id order. Nothing else yet."""
    if kind != "requirement":
        return []
    rows = con.execute(
        "SELECT * FROM criterion WHERE project = ? AND requirement_id = ? ORDER BY id",
        (project, entity_id),
    ).fetchall()
    return [dict(row) for row in rows]


def get_entity(project: str, ref: str, *,
               con: Optional[sqlite3.Connection] = None) -> dict:
    """One entry: raw row, link ids, child records and its last 10 history rows."""
    own = con is None
    con = con or connect()
    try:
        kind, entity_id = resolve_entity(project, ref, con=con)
        row = _select_row(con, project, kind, entity_id)
        assert row is not None                     # resolve_entity checked it
        return {
            "kind": kind,
            "id": entity_id,
            "ref": model.format_ref(kind, entity_id),
            "row": row,
            "links": links_of(con, project, kind, entity_id),
            "children": _children_of(con, project, kind, entity_id),
            "history": history_of(con, project, kind, entity_id),
        }
    finally:
        if own:
            con.close()


def list_entities(project: str, kind: Optional[str] = None, *, status: Optional[str] = None,
                  limit: Optional[int] = None,
                  con: Optional[sqlite3.Connection] = None) -> list[dict]:
    """Rows of one kind (or R, F, S, A), id order, each with ``kind``/``ref``/``links``."""
    own = con is None
    con = con or connect()
    try:
        if not project_exists(project, con):
            raise StoreError(f"project {project!r} does not exist")
        kinds = [model.kind_for(kind)] if kind else list(LIST_ORDER)
        found: list[dict] = []
        for one in kinds:
            sql = f"SELECT * FROM {ENTITY_TABLE[one]} WHERE project = ?"
            params: list[Any] = [project]
            if status is not None:
                sql += " AND status = ?"
                params.append(status)
            sql += " ORDER BY id"
            for row in con.execute(sql, tuple(params)):
                item = dict(row)
                item["kind"] = one
                item["ref"] = model.format_ref(one, item["id"])
                item["links"] = links_of(con, project, one, item["id"])
                found.append(item)
        if limit is not None:
            found = found[: max(int(limit), 0)]
        return found
    finally:
        if own:
            con.close()


# --------------------------------------------------------------- changes (set, HP-2)
def _superseded_ok(con: sqlite3.Connection, project: str, kind: str, entity_id: int,
                   fields: dict) -> bool:
    """``superseded_by`` names an existing row of the same kind, in the same project."""
    target = fields.get("superseded_by")
    if target is None or target == entity_id:
        return False
    return _select_row(con, project, kind, int(target)) is not None


def load_facts(con: sqlite3.Connection, project: str, kind: str, entity_id: int,
               fields: dict) -> dict:
    """The counts and statuses the status rules need, read from the link tables.

    This is the only place that touches sqlite for the status machines: ``model``
    receives the result as a plain dict.
    """
    if kind == "requirement":
        criteria = con.execute(
            "SELECT COUNT(*) AS total,"
            " COALESCE(SUM(CASE WHEN status NOT IN ('passed','waived') THEN 1 ELSE 0 END), 0)"
            "   AS still_open"
            " FROM criterion WHERE project = ? AND requirement_id = ?",
            (project, entity_id),
        ).fetchone()
        return {
            "features": con.execute(
                "SELECT COUNT(*) AS n FROM feature_requirement"
                " WHERE project = ? AND requirement_id = ?", (project, entity_id),
            ).fetchone()["n"],
            "specs": con.execute(
                "SELECT COUNT(*) AS n FROM spec_requirement"
                " WHERE project = ? AND requirement_id = ?", (project, entity_id),
            ).fetchone()["n"],
            "criteria": criteria["total"],
            "criteria_open": criteria["still_open"],
            "superseded_by_ok": _superseded_ok(con, project, "requirement", entity_id, fields),
        }
    if kind == "feature":
        requirements = con.execute(
            "SELECT COUNT(*) AS total,"
            " COALESCE(SUM(CASE WHEN r.status NOT IN ('done','dropped') THEN 1 ELSE 0 END), 0)"
            "   AS still_open"
            " FROM feature_requirement fr"
            " JOIN requirement r ON r.project = fr.project AND r.id = fr.requirement_id"
            " WHERE fr.project = ? AND fr.feature_id = ?",
            (project, entity_id),
        ).fetchone()
        only_here = con.execute(
            "SELECT COUNT(*) AS n"
            " FROM feature_requirement fr"
            " JOIN requirement r ON r.project = fr.project AND r.id = fr.requirement_id"
            " WHERE fr.project = ? AND fr.feature_id = ?"
            "   AND r.status NOT IN ('done','dropped')"
            "   AND (SELECT COUNT(*) FROM feature_requirement fr2"
            "        JOIN feature f2 ON f2.project = fr2.project AND f2.id = fr2.feature_id"
            "        WHERE fr2.project = fr.project"
            "          AND fr2.requirement_id = fr.requirement_id"
            "          AND f2.status != 'cut') = 1",
            (project, entity_id),
        ).fetchone()
        return {
            "requirements": requirements["total"],
            "requirements_not_done": requirements["still_open"],
            "open_only_here": only_here["n"],
        }
    if kind == "spec":
        criteria = con.execute(
            "SELECT COUNT(*) AS total,"
            " COALESCE(SUM(CASE WHEN c.status NOT IN ('passed','waived') THEN 1 ELSE 0 END), 0)"
            "   AS still_open"
            " FROM criterion c"
            " JOIN spec_requirement sr ON sr.project = c.project"
            "   AND sr.requirement_id = c.requirement_id"
            " WHERE sr.project = ? AND sr.spec_id = ?",
            (project, entity_id),
        ).fetchone()
        return {
            "requirements": con.execute(
                "SELECT COUNT(*) AS n FROM spec_requirement"
                " WHERE project = ? AND spec_id = ?", (project, entity_id),
            ).fetchone()["n"],
            "external_links": con.execute(
                "SELECT COUNT(*) AS n FROM external_link"
                " WHERE project = ? AND entity_type = 'spec' AND entity_id = ?",
                (project, entity_id),
            ).fetchone()["n"],
            "criteria": criteria["total"],
            "criteria_open": criteria["still_open"],
            "superseded_by_ok": _superseded_ok(con, project, "spec", entity_id, fields),
        }
    return {}


def update_entity(project: str, ref: str, changes: dict, *, reason: Optional[str] = None,
                  con: Optional[sqlite3.Connection] = None) -> dict:
    """Change fields and/or the status of one entry, validated in one transaction.

    Every mutation stamps ``updated_at``; a status change and a requirement
    statement change each append a history row (S1 section 9). A statement change
    on an approved-or-later requirement requires ``reason``.
    """
    own = con is None
    con = con or connect()
    try:
        kind, entity_id = resolve_entity(project, ref, con=con)
        row = _select_row(con, project, kind, entity_id)
        assert row is not None
        allowed = model.SET_FIELDS[kind]
        for name in changes:
            if name not in allowed:
                raise model.ValidationError(
                    f"unknown field {name!r} for {model.format_ref(kind, entity_id)};"
                    f" editable keys are {', '.join(allowed)}"
                )
        values: dict[str, Any] = {}
        for name, raw in changes.items():
            values[name] = model.validate_field(kind, name, raw)
        if isinstance(values.get("paths"), (list, tuple)):
            values["paths"] = json.dumps(list(values["paths"]))
        if values.get("superseded_by") is not None:
            if values["superseded_by"] == entity_id:
                raise model.ValidationError(
                    f"superseded_by cannot be {model.format_ref(kind, entity_id)} itself"
                )
            _resolve_link(con, project, kind, values["superseded_by"], "superseded_by")
        if kind == "feature" and "slug" in values:
            _check_feature_slug(con, project, values["slug"], exclude_id=entity_id)

        merged = {**row, **values}
        model.check_blocked(merged)

        status_change: Optional[tuple[str, str]] = None
        if "status" in values:
            facts = load_facts(con, project, kind, entity_id, merged)
            model.check_transition(kind, row, values["status"], merged, facts)
            status_change = (row["status"], values["status"])

        statement_changed = (kind == "requirement" and "statement" in values
                             and values["statement"] != row["statement"])
        clean_reason = (str(reason).strip() or None) if reason is not None else None
        if (statement_changed and row["status"] in model.APPROVED_OR_LATER
                and clean_reason is None):
            raise model.ValidationError(
                f"changing the statement of {model.format_ref(kind, entity_id)}"
                f" ({row['status']}) needs --reason <why>"
            )

        now = _now()
        values["updated_at"] = now
        events: list[tuple[str, Optional[str]]] = []
        if status_change is not None:
            events.append((f"status {status_change[0]} -> {status_change[1]}", clean_reason))
        if statement_changed:
            events.append(("statement changed", clean_reason))

        with con:
            assignments = ", ".join(f"{name} = ?" for name in values)
            con.execute(
                f"UPDATE {ENTITY_TABLE[kind]} SET {assignments} WHERE project = ? AND id = ?",
                (*values.values(), project, entity_id),
            )
            for change, why in events:
                con.execute(
                    "INSERT INTO history(project, entity_type, entity_id, change, reason, at)"
                    " VALUES (?,?,?,?,?,?)",
                    (project, kind, entity_id, change, why, now),
                )
        return {
            "kind": kind,
            "id": entity_id,
            "ref": model.format_ref(kind, entity_id),
            "changed": list(changes),
            "status_change": status_change,
            "row": _select_row(con, project, kind, entity_id),
        }
    finally:
        if own:
            con.close()
