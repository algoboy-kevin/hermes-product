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

import hashlib
import json
import os
import re
import shutil
import sqlite3
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional, Union

try:  # package submodule (Hermes plugin loader)
    from . import graph, model
except ImportError:  # top-level module (test suite)
    import graph  # type: ignore[no-redef]
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

def _migrate_v2_entity_body(con: sqlite3.Connection) -> None:
    """v2: R#, F# and S# rows carry their defining text in a nullable `body` column."""
    for table in ("requirement", "feature", "spec"):
        con.execute(f"ALTER TABLE {table} ADD COLUMN body TEXT")


def _migrate_v3_open_doc_kinds(con: sqlite3.Connection) -> None:
    """v3: `doc.kind` is validated by the model, not by a CHECK that needs a rebuild per kind.

    SQLite cannot drop a CHECK in place, so the table is rebuilt (rows, links and ids
    unchanged). This is what lets `report` (and any later kind) exist without another
    migration.
    """
    con.execute("""
CREATE TABLE doc_v3 (
  project       TEXT NOT NULL REFERENCES project(slug) ON DELETE CASCADE,
  id            INTEGER NOT NULL,
  title         TEXT,
  kind          TEXT,
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
)""")
    con.execute("INSERT INTO doc_v3 SELECT project, id, title, kind, summary, body_file,"
                " body_ref, body_hash, status, superseded_by, notes, updated_at FROM doc")
    con.execute("DROP TABLE doc")
    con.execute("ALTER TABLE doc_v3 RENAME TO doc")


MIGRATIONS: list[tuple[int, str, Callable[[sqlite3.Connection], None]]] = [
    (2, "body column on requirement, feature and spec", _migrate_v2_entity_body),
    (3, "doc.kind without a CHECK (kinds are validated by the model)", _migrate_v3_open_doc_kinds),
]

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
            # SQLite's table-redefinition recipe: foreign keys OFF outside the
            # transaction (the pragma is a no-op inside one), then a foreign_key_check
            # BEFORE the commit so a migration can never leave a dangling reference.
            con.execute("PRAGMA foreign_keys = OFF")
            con.execute("BEGIN IMMEDIATE")
            apply_migration(con)
            violations = con.execute("PRAGMA foreign_key_check").fetchall()
            if violations:
                raise StoreError(f"foreign key check failed after the migration:"
                                 f" {len(violations)} dangling reference(s)")
            con.execute(f"PRAGMA user_version = {int(target)}")
            con.execute("COMMIT")
            con.execute("PRAGMA foreign_keys = ON")
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


PROJECT_SET_FIELDS = ("name", "root")


def update_project(slug: str, changes: dict, con: Optional[sqlite3.Connection] = None) -> dict:
    """Change a project's ``name`` and/or ``root``; the slug is the key and never changes.

    ``root`` is stored absolute (like ``create_project``); an empty value, ``-`` or
    ``none`` clears it. An unknown key, an empty name or a missing project raises
    ``StoreError`` and nothing is written.
    """
    if not changes:
        raise StoreError("project set needs key=value pairs (name, root)")
    for key in changes:
        if key not in PROJECT_SET_FIELDS:
            raise StoreError(f"unknown field {key!r} for a project;"
                             f" editable keys are {', '.join(PROJECT_SET_FIELDS)}")
    values: dict[str, Any] = {}
    if "name" in changes:
        name = str(changes["name"]).strip()
        if not name:
            raise StoreError("name must not be empty")
        values["name"] = name
    if "root" in changes:
        raw = str(changes["root"]).strip()
        values["root"] = (None if raw.lower() in ("", "-", "none", "null")
                          else str(Path(raw).expanduser().resolve()))
    own = con is None
    con = con or connect()
    try:
        if not con.execute("SELECT 1 FROM project WHERE slug = ?", (slug,)).fetchone():
            raise StoreError(f"project {slug!r} does not exist")
        assignments = ", ".join(f"{name} = ?" for name in values)
        with con:
            con.execute(f"UPDATE project SET {assignments}, updated_at = ? WHERE slug = ?",
                        (*values.values(), _now(), slug))
        return {"slug": slug, "changed": list(values), **values}
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


def find_existing(project: str, kind: str, fields: dict,
                  con: Optional[sqlite3.Connection] = None) -> Optional[int]:
    """The id of the entry that ``add --if-missing`` would duplicate, or None.

    The natural key (DOC5 section 2.4): F# by ``slug`` when given, else title; A# by
    parent requirement + text; R#, S# and DOC# by title. Titles and text compare
    case-insensitively with whitespace collapsed. A retired entry still counts: re-adding
    a dropped requirement is not what an idempotent rerun means.
    """
    kind = model.kind_for(kind)

    def norm(value: Any) -> str:
        return " ".join(str(value or "").split()).casefold()

    own = con is None
    con = con or connect()
    try:
        if not project_exists(project, con):
            raise StoreError(f"project {project!r} does not exist")
        table = ENTITY_TABLE[kind]
        if kind == "feature" and fields.get("slug"):
            row = con.execute("SELECT id FROM feature WHERE project = ? AND slug = ?",
                              (project, str(fields["slug"]))).fetchone()
            return row["id"] if row else None
        if kind == "criterion":
            parent = model._as_ref_int("requirement_id", fields.get("requirement_id"))
            wanted = norm(fields.get("text"))
            for row in con.execute(
                    "SELECT id, text FROM criterion WHERE project = ? AND requirement_id = ?",
                    (project, parent)):
                if norm(row["text"]) == wanted:
                    return row["id"]
            return None
        wanted = norm(fields.get("title"))
        if not wanted:
            return None
        for row in con.execute(f"SELECT id, title FROM {table} WHERE project = ? ORDER BY id",
                               (project,)):
            if norm(row["title"]) == wanted:
                return row["id"]
        return None
    finally:
        if own:
            con.close()


def create_entity(project: str, kind: str, fields: dict,
                  con: Optional[sqlite3.Connection] = None) -> dict:
    """Create an R#/A#/F#/S# row: validate, then allocate the id and insert atomically.

    Validation and cross-reference checks run BEFORE the id is allocated, so a
    refused payload consumes no id; the allocation and the row sit in ONE
    transaction, so a failure after the allocation leaves no gap and no row.
    """
    kind = model.kind_for(kind)
    own = con is None
    con = con or connect()
    try:
        if not project_exists(project, con):
            raise StoreError(f"project {project!r} does not exist")
        if kind == "doc":
            return _create_doc(con, project, dict(fields))
        values = dict(fields)
        _fold_body_options(kind, values)
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


# --------------------------------------------------------------- documents (HP-4)
#: directory (under the project's store directory) holding the DOC body files
DOC_BODY_DIR = "docs"

#: the ref order of a DOC's links: R, then A, then F, then S, each by id
DOC_LINK_ORDER = ("requirement", "criterion", "feature", "spec")


def docs_dir(project: str) -> Path:
    """``<db dir>/<project>/docs``: where this project's DOC bodies live."""
    return db_path().parent / str(project) / DOC_BODY_DIR


def doc_body_relative(doc_id: int) -> str:
    """The ``body_file`` value stored in the row, relative to the project dir."""
    return f"{DOC_BODY_DIR}/DOC{doc_id}.md"


def doc_body_path(project: str, row: dict) -> Path:
    """Absolute path of a DOC's stored body file (the row keeps it relative)."""
    return db_path().parent / str(project) / str(row["body_file"])


def _write_doc_body(project: str, doc_id: int, text: str) -> Path:
    """Write the body file ATOMICALLY: a temp file in the same dir, then os.replace."""
    directory = docs_dir(project)
    directory.mkdir(parents=True, exist_ok=True)
    final = directory / f"DOC{doc_id}.md"
    handle, temp_name = tempfile.mkstemp(dir=directory, prefix=f".DOC{doc_id}.", suffix=".tmp")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_name, final)
    except BaseException:
        Path(temp_name).unlink(missing_ok=True)
        raise
    return final


def _after_body_write(doc_id: int) -> None:
    """Seam between the body file landing and the COMMIT (tests force a failure)."""
    return None


def read_body_source(path_like: str) -> str:
    """Read a ``--body-file`` source, naming it when it cannot be read."""
    path = Path(str(path_like)).expanduser()
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        raise model.ValidationError(f"cannot read {path}: {exc}") from exc


def _body_hash(body_ref: Optional[str]) -> Optional[str]:
    """sha256 of a local ``body_ref`` file; None for a URL or a missing path."""
    if not body_ref:
        return None
    if str(body_ref).startswith(("http://", "https://")):
        return None
    path = Path(str(body_ref)).expanduser()
    if not path.is_file():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _extract_body_options(fields: dict) -> tuple[Optional[str], Optional[str]]:
    """Pop the CLI-only body options; returns (copied text or None, body_ref or None)."""
    body_file = fields.pop("body_file", None)
    body = fields.pop("body", None)
    body_ref = fields.pop("body_ref", None)
    given = [name for name, value in (("--body-file", body_file), ("--body", body),
                                      ("--body-ref", body_ref)) if value not in (None, "")]
    if len(given) > 1:
        raise model.ValidationError(
            "pass only one of --body-file, --body, --body-ref (got " + ", ".join(given) + ")"
        )
    text: Optional[str] = None
    if body_file:
        text = read_body_source(str(body_file))
    elif body is not None:
        text = str(body)
    return text, (str(body_ref) if body_ref else None)


def _fold_body_options(kind: str, values: dict) -> None:
    """Turn the CLI-only ``body_file`` / ``body`` / ``body_ref`` of an ``add`` into ``body``.

    R#, F# and S# store the text on the row. A# has no body, and ``--body-ref`` is a
    DOC# option: each is REFUSED, never dropped (an ``add S --body-file`` that said
    "created" and kept nothing was the silent-loss defect of the first adoption).
    """
    text, body_ref = _extract_body_options(values)
    if body_ref is not None:
        raise model.ValidationError(
            f"--body-ref is only for DOC#; {model.PREFIX[kind]}# stores its text with"
            " --body or --body-file"
        )
    if text is None:
        return
    if kind not in model.BODY_KINDS:
        raise model.ValidationError(
            f"{model.PREFIX[kind]}# has no body (a criterion is one sentence plus evidence;"
            " put the context on its requirement)"
        )
    values["body"] = text


def _check_single_prd(con: sqlite3.Connection, project: str,
                      *, exclude_id: Optional[int] = None) -> None:
    """Exactly one `prd` document per project (S1 section 7)."""
    row = con.execute(
        "SELECT id FROM doc WHERE project = ? AND kind = 'prd'", (project,)
    ).fetchone()
    if row is not None and row["id"] != exclude_id:
        raise model.ValidationError(
            f"only one prd document per project (DOC{row['id']} is already prd)"
        )


def _create_doc(con: sqlite3.Connection, project: str, fields: dict) -> dict:
    """Add a DOC#: validate, then id + row + body file in ONE transaction.

    The body file is written (temp file then os.replace) BEFORE COMMIT: a failure
    anywhere after it removes the file and rolls the row back, so a refused or
    broken create leaves neither a row nor a file.
    """
    values = dict(fields)
    text, body_ref = _extract_body_options(values)
    if body_ref is not None:
        values["body_ref"] = body_ref
    validated = model.validate_new("doc", values)
    if validated.get("kind") == "prd":
        _check_single_prd(con, project)
    validated["updated_at"] = _now()
    validated["body_hash"] = _body_hash(body_ref)
    written: Optional[Path] = None
    try:
        with con:                              # id + row + body file: one transaction
            new_id = next_id(con, project, "DOC", commit=False)
            if text is not None:
                validated["body_file"] = doc_body_relative(new_id)
            _insert_row(con, project, "doc", new_id, validated)
            if text is not None:
                written = _write_doc_body(project, new_id, text)
            _after_body_write(new_id)
    except BaseException:
        if written is not None:
            written.unlink(missing_ok=True)
        raise
    return {"kind": "doc", "id": new_id, "ref": model.format_ref("doc", new_id),
            "body_chars": len(text) if text is not None else 0}


def docs_of(con: sqlite3.Connection, project: str, kind: str,
            entity_id: int) -> list[dict]:
    """The DOC rows linked to one entry, in doc id order (summary only, never a body)."""
    rows = con.execute(
        "SELECT d.id, d.kind, d.title, d.summary, d.status FROM doc_link l"
        " JOIN doc d ON d.project = l.project AND d.id = l.doc_id"
        " WHERE l.project = ? AND l.entity_type = ? AND l.entity_id = ?"
        " ORDER BY d.id",
        (project, kind, entity_id),
    ).fetchall()
    return [dict(row) for row in rows]


def doc_link_refs(con: sqlite3.Connection, project: str, doc_id: int) -> list[tuple]:
    """A DOC's linked entries as ``(kind, id)``, in R, A, F, S then id order."""
    rows = [
        (row["entity_type"], row["entity_id"])
        for row in con.execute(
            "SELECT entity_type, entity_id FROM doc_link WHERE project = ? AND doc_id = ?",
            (project, doc_id),
        )
    ]
    return sorted(rows, key=lambda pair: (DOC_LINK_ORDER.index(pair[0]), pair[1]))


def doc_link_items(con: sqlite3.Connection, project: str, doc_id: int) -> list[dict]:
    """The linked entries of a DOC, shaped like ``list_entities`` items (for `show`)."""
    items: list[dict] = []
    for kind, entity_id in doc_link_refs(con, project, doc_id):
        row = _select_row(con, project, kind, entity_id)
        if row is None:
            continue
        row["kind"] = kind
        row["ref"] = ref_for(kind, entity_id)
        row["links"] = links_of(con, project, kind, entity_id)
        items.append(row)
    return items


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


def dependency_pairs(con: sqlite3.Connection, project: str, kind: str) -> list[tuple]:
    """(id, needs_id, optional) of one dependency table; ordered and complete.

    The table is picked by ``kind`` (requirement/feature/spec); ``optional`` is
    0 for the kinds whose dependency table has no such column.
    """
    table, self_column, other_column = DEPENDENCY_LINKS[kind]
    optional_column = ", optional" if kind == "feature" else ", 0 AS optional"
    rows = con.execute(
        f"SELECT {self_column}, {other_column}{optional_column} FROM {table}"
        f" WHERE project = ? ORDER BY {self_column}, {other_column}",
        (project,),
    )
    return [tuple(row) for row in rows]


def external_links_of(con: sqlite3.Connection, project: str, kind: str,
                      entity_id: int) -> list[dict]:
    """The external_link rows of one entity, deterministic order."""
    rows = con.execute(
        "SELECT system, external_id, url, role FROM external_link"
        " WHERE project = ? AND entity_type = ? AND entity_id = ?"
        " ORDER BY system, external_id",
        (project, kind, entity_id),
    )
    return [dict(row) for row in rows]


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


def with_body_size(row: dict, *, keep: bool = False) -> dict:
    """An R#/F#/S# row with its ``body`` text swapped for ``body_chars`` (0: no body).

    Retrieval stays cheap (R5): only ``show --body`` asks for the text (``keep``).
    A row of a kind without a body column comes back unchanged.
    """
    if "body" not in row:
        return row
    out = dict(row)
    text = out.get("body")
    out["body_chars"] = len(text) if text else 0
    if not keep:
        out.pop("body")
    return out


def get_entity(project: str, ref: str, *, with_body: bool = False,
               con: Optional[sqlite3.Connection] = None) -> dict:
    """One entry: row, link ids, child records and its last 10 history rows.

    An R#/F#/S# row carries ``body_chars``; the ``body`` text only when ``with_body``.
    """
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
            "row": with_body_size(row, keep=with_body),
            "links": links_of(con, project, kind, entity_id),
            "children": _children_of(con, project, kind, entity_id),
            "docs": docs_of(con, project, kind, entity_id),
            "doc_links": doc_link_items(con, project, entity_id) if kind == "doc" else [],
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
        kinds = [model.kind_for(kind)] if kind else [*LIST_ORDER, "doc"]
        found: list[dict] = []
        for one in kinds:
            sql = f"SELECT * FROM {ENTITY_TABLE[one]} WHERE project = ?"
            params: list[Any] = [project]
            if status is not None:
                sql += " AND status = ?"
                params.append(status)
            sql += " ORDER BY id"
            for row in con.execute(sql, tuple(params)):
                item = with_body_size(dict(row))
                if one == "doc":
                    # the row's own `kind` (prd/design/...) is kept under another name
                    item["doc_kind"] = item.get("kind")
                    item["doc_links"] = doc_link_refs(con, project, item["id"])
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
            "mandatory_planned": _id_column(
                con,
                "SELECT d.needs_id FROM feature_dependency d"
                " JOIN feature f ON f.project = d.project AND f.id = d.needs_id"
                " WHERE d.project = ? AND d.feature_id = ?"
                "   AND d.optional = 0 AND f.status = 'planned'"
                " ORDER BY d.needs_id",
                (project, entity_id),
            ),
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
    if kind == "doc":
        return {"superseded_by_ok": _superseded_ok(con, project, "doc", entity_id, fields)}
    return {}


def update_entity(project: str, ref: str, changes: dict, *, reason: Optional[str] = None,
                  override_reason: Optional[str] = None,
                  body_source: Optional[str] = None,
                  con: Optional[sqlite3.Connection] = None) -> dict:
    """Change fields and/or the status of one entry, validated in one transaction.

    Every mutation stamps ``updated_at``; a status change and a requirement
    statement change each append a history row (S1 section 9). A statement change
    on an approved-or-later requirement requires ``reason``. ``override_reason``
    only overrides the feature planned -> in_progress dependency check: when that
    check is the one in play it becomes the status history row's reason, and it is
    ignored everywhere else. ``body_source`` (a path) replaces a DOC body or an
    R#/F#/S# ``body``, logs ``body changed`` and is refused for A#.
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
        if kind == "doc":
            if values.get("kind") == "prd":
                _check_single_prd(con, project, exclude_id=entity_id)
            if "body_ref" in values:
                values["body_hash"] = _body_hash(values["body_ref"])
        body_text: Optional[str] = None           # DOC#: the body FILE is rewritten
        row_body_changed = False                  # R#/F#/S#: the `body` column changed
        if body_source is not None:
            if kind == "doc":
                body_text = read_body_source(body_source)
                values["body_file"] = doc_body_relative(entity_id)
            elif kind in model.BODY_KINDS:
                if "body" in changes:
                    raise model.ValidationError("pass either body=... or --body-file, not both")
                values["body"] = model.validate_field(kind, "body", read_body_source(body_source))
            else:
                raise model.ValidationError(
                    f"{model.PREFIX[kind]}# has no body (a criterion is one sentence plus"
                    " evidence; put the context on its requirement)"
                )
        if kind in model.BODY_KINDS and "body" in values:
            row_body_changed = values["body"] != row.get("body")

        merged = {**row, **values}
        model.check_blocked(merged)

        status_change: Optional[tuple[str, str]] = None
        clean_override = (str(override_reason).strip() or None) if override_reason is not None else None
        blocked_dependencies: list = []
        if "status" in values:
            facts = load_facts(con, project, kind, entity_id, merged)
            if clean_override:
                facts["override_reason"] = clean_override
            model.check_transition(kind, row, values["status"], merged, facts)
            status_change = (row["status"], values["status"])
            blocked_dependencies = list(facts.get("mandatory_planned") or [])

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
            status_reason = clean_override if (clean_override and blocked_dependencies) else clean_reason
            events.append((f"status {status_change[0]} -> {status_change[1]}", status_reason))
        if statement_changed:
            events.append(("statement changed", clean_reason))
        if body_text is not None or row_body_changed:
            events.append(("body changed", clean_reason))

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
            if body_text is not None:
                _write_doc_body(project, entity_id, body_text)
        changed = list(changes)
        if "body" not in changed and (body_text is not None or "body" in values):
            changed.append("body")
        return {
            "kind": kind,
            "id": entity_id,
            "ref": model.format_ref(kind, entity_id),
            "changed": changed,
            "status_change": status_change,
            "row": with_body_size(_select_row(con, project, kind, entity_id)),
        }
    finally:
        if own:
            con.close()


# --------------------------------------------------------------------- links (HP-3)
#: same-kind dependency tables: kind -> (table, subject column, needs column)
DEPENDENCY_LINKS = {
    "feature": ("feature_dependency", "feature_id", "needs_id"),
    "requirement": ("requirement_dependency", "requirement_id", "needs_id"),
    "spec": ("spec_dependency", "spec_id", "needs_id"),
}

#: cross-kind relation tables: unordered kind pair -> (table, {kind: column})
RELATION_LINKS = {
    frozenset(("feature", "requirement")): (
        "feature_requirement", {"feature": "feature_id", "requirement": "requirement_id"}),
    frozenset(("spec", "requirement")): (
        "spec_requirement", {"spec": "spec_id", "requirement": "requirement_id"}),
    frozenset(("feature", "spec")): (
        "feature_spec", {"feature": "feature_id", "spec": "spec_id"}),
}

#: an entry in this state cannot take part in a new link (S2 section 2)
RETIRED_STATUS = {"requirement": "dropped", "feature": "cut", "spec": "superseded",
                  "doc": "superseded"}

#: the combinations `link`/`unlink` accept, named in every shape error
LINK_SHAPES = (
    "valid: link F1 R1 / S1 R1 / F1 S1 (cross-kind), or "
    "link F1 --needs F2 / R1 --needs R2 / S1 --needs S2 (same kind; "
    "--optional for feature dependencies)"
)


def _row_status(con: sqlite3.Connection, project: str, kind: str,
                entity_id: int) -> Optional[str]:
    row = con.execute(
        f"SELECT status FROM {ENTITY_TABLE[kind]} WHERE project = ? AND id = ?",
        (project, entity_id),
    ).fetchone()
    return row["status"] if row is not None else None


def _refuse_retired(con: sqlite3.Connection, project: str, kind: str,
                    entity_id: int, verb: str) -> None:
    """Refuse a link to a retired entry, naming the entry and its state."""
    retired = RETIRED_STATUS.get(kind)
    status = _row_status(con, project, kind, entity_id)
    if retired is not None and status == retired:
        raise model.ValidationError(
            f"cannot {verb} {ref_for(kind, entity_id)}: it is {status}"
        )


def _stamp_and_log(con: sqlite3.Connection, project: str, now: str,
                   events: list) -> None:
    """Set ``updated_at`` and append one history row per (kind, id, change)."""
    for kind, entity_id, change in events:
        con.execute(
            f"UPDATE {ENTITY_TABLE[kind]} SET updated_at = ? WHERE project = ? AND id = ?",
            (now, project, entity_id),
        )
        con.execute(
            "INSERT INTO history(project, entity_type, entity_id, change, reason, at)"
            " VALUES (?,?,?,?,?,?)",
            (project, kind, entity_id, change, None, now),
        )


def link_entries(project: str, first: str, second: Optional[str] = None, *,
                 needs: Optional[str] = None, optional: bool = False,
                 remove: bool = False, con: Optional[sqlite3.Connection] = None) -> dict:
    """Create (or with ``remove`` drop) one relation or dependency edge.

    Shapes are frozen (S4): two references of different kinds are a relation;
    ``<REF> --needs <REF>`` of the same kind is a dependency (feature edges carry
    ``optional``). A DOC# links to any entry through ``doc_link`` and never to
    another document. Every rule is checked before the first write, and the writes
    run in ONE transaction, so a refused call changes nothing and a crash cannot
    leave half a link.
    """
    verb = "unlink" if remove else "link"
    own = con is None
    con = con or connect()
    try:
        if not project_exists(project, con):
            raise StoreError(f"project {project!r} does not exist")
        kind_a, id_a = resolve_entity(project, first, con=con)
        ref_a = ref_for(kind_a, id_a)
        pair_ids: tuple = ()
        optional_value = 0
        subject_col = other_col = ""
        if kind_a == "criterion":
            raise model.ValidationError(
                f"cannot {verb} {ref_a}: a criterion belongs to its requirement"
            )

        doc_pair = False
        dependency = False
        if needs is not None:
            if second is not None:
                raise model.ValidationError(
                    f"cannot {verb} {ref_a} {second} --needs {needs}: pass either two"
                    f" references or one reference and --needs ({LINK_SHAPES})"
                )
            kind_b, id_b = resolve_entity(project, needs, con=con)
            ref_b = ref_for(kind_b, id_b)
            if kind_b == "criterion":
                raise model.ValidationError(
                    f"cannot {verb} {ref_b}: a criterion belongs to its requirement"
                )
            if "doc" in (kind_a, kind_b):
                raise model.ValidationError(
                    f"--needs is only for R/F/S dependencies ({LINK_SHAPES})"
                )
            if kind_a != kind_b:
                raise model.ValidationError(
                    f"cannot {verb} {ref_a} --needs {ref_b}: --needs joins two entries"
                    f" of the same kind ({LINK_SHAPES})"
                )
            if optional and kind_a != "feature":
                raise model.ValidationError(
                    "--optional is only valid for a feature dependency"
                    " (F1 --needs F2 --optional)"
                )
            table, subject_col, other_col = DEPENDENCY_LINKS[kind_a]
            optional_value = 1 if (optional and kind_a == "feature") else 0
            pair_ids = (id_a, id_b)
            dependency = True
        else:
            if second is None:
                raise model.ValidationError(
                    f"{verb} needs two references, or a reference and --needs <REF>"
                    f" ({LINK_SHAPES})"
                )
            kind_b, id_b = resolve_entity(project, second, con=con)
            ref_b = ref_for(kind_b, id_b)
            if kind_b == "criterion" and kind_a != "doc":
                raise model.ValidationError(
                    f"cannot {verb} {ref_b}: a criterion belongs to its requirement"
                )
            if kind_a == kind_b:
                if kind_a == "doc":
                    raise model.ValidationError(
                        f"cannot {verb} {ref_a} {ref_b}: a document links to an entry,"
                        " not to another document"
                    )
                raise model.ValidationError(
                    f"cannot {verb} {ref_a} {ref_b}: same-kind pairs are a dependency,"
                    f" not a relation ({LINK_SHAPES})"
                )
            if optional:
                raise model.ValidationError(
                    "--optional is only valid for a feature dependency"
                    " (F1 --needs F2 --optional)"
                )
            if "doc" in (kind_a, kind_b):
                doc_pair = True
                table = "doc_link"
                pair_ids = (id_a, id_b)
            else:
                table, columns = RELATION_LINKS[frozenset((kind_a, kind_b))]
                ordered = sorted((kind_a, kind_b), key=lambda kind: columns[kind])
                subject_col, other_col = columns[ordered[0]], columns[ordered[1]]
                by_kind = {kind_a: id_a, kind_b: id_b}
                pair_ids = (by_kind[ordered[0]], by_kind[ordered[1]])

        if not remove and kind_a == kind_b and id_a == id_b:
            raise model.ValidationError(f"cannot link {ref_a} to itself")
        _refuse_retired(con, project, kind_a, id_a, verb)
        _refuse_retired(con, project, kind_b, id_b, verb)

        if doc_pair:
            doc_kind, doc_id = (kind_a, id_a) if kind_a == "doc" else (kind_b, id_b)
            entity_kind, entity_id = (kind_b, id_b) if kind_a == "doc" else (kind_a, id_a)
            exists = con.execute(
                "SELECT 1 FROM doc_link WHERE project = ? AND doc_id = ?"
                " AND entity_type = ? AND entity_id = ?",
                (project, doc_id, entity_kind, entity_id),
            ).fetchone() is not None
        else:
            exists = con.execute(
                f"SELECT 1 FROM {table} WHERE project = ? AND {subject_col} = ?"
                f" AND {other_col} = ?",
                (project, pair_ids[0], pair_ids[1]),
            ).fetchone() is not None

        if remove and not exists:
            raise model.ValidationError(f"no such link: {ref_a} {ref_b}")
        if not remove and exists:
            raise model.ValidationError(f"already linked: {ref_a} {ref_b}")

        if not remove and dependency:
            edges = [tuple(row) for row in con.execute(
                f"SELECT {subject_col}, {other_col} FROM {table} WHERE project = ?",
                (project,),
            )]
            cycle = graph.find_cycle(edges, (pair_ids[0], pair_ids[1]))
            if cycle is not None:
                named = " -> ".join(ref_for(kind_a, value) for value in cycle)
                raise model.ValidationError(f"would create a dependency cycle: {named}")

        if dependency:
            other_change = (f"needs {ref_b}", f"needed by {ref_a}") if not remove else (
                f"no longer needs {ref_b}", f"no longer needed by {ref_a}")
            message = (f"{ref_a} needs {ref_b}" if not remove else
                       f"{ref_a} no longer needs {ref_b}")
            if not remove and optional_value:
                message += " (optional)"
        else:
            verb_word = "linked" if not remove else "unlinked"
            other_change = (f"{verb_word} {ref_b}", f"{verb_word} {ref_a}")
            message = f"{ref_a} {verb_word} {ref_b}"

        now = _now()
        with con:
            if doc_pair:
                if remove:
                    con.execute(
                        "DELETE FROM doc_link WHERE project = ? AND doc_id = ?"
                        " AND entity_type = ? AND entity_id = ?",
                        (project, doc_id, entity_kind, entity_id),
                    )
                else:
                    con.execute(
                        "INSERT INTO doc_link(project, doc_id, entity_type, entity_id)"
                        " VALUES (?,?,?,?)",
                        (project, doc_id, entity_kind, entity_id),
                    )
                events = [(doc_kind, doc_id, other_change[0]),
                          (entity_kind, entity_id, other_change[1])]
            elif dependency:
                needs_optional = kind_a == "feature"
                if remove:
                    con.execute(
                        f"DELETE FROM {table} WHERE project = ? AND {subject_col} = ?"
                        f" AND {other_col} = ?",
                        (project, pair_ids[0], pair_ids[1]),
                    )
                elif needs_optional:
                    con.execute(
                        f"INSERT INTO {table}(project, {subject_col}, {other_col}, optional)"
                        " VALUES (?,?,?,?)",
                        (project, pair_ids[0], pair_ids[1], optional_value),
                    )
                else:
                    con.execute(
                        f"INSERT INTO {table}(project, {subject_col}, {other_col})"
                        " VALUES (?,?,?)",
                        (project, pair_ids[0], pair_ids[1]),
                    )
                events = [(kind_a, id_a, other_change[0]), (kind_b, id_b, other_change[1])]
            else:
                if remove:
                    con.execute(
                        f"DELETE FROM {table} WHERE project = ? AND {subject_col} = ?"
                        f" AND {other_col} = ?",
                        (project, pair_ids[0], pair_ids[1]),
                    )
                else:
                    con.execute(
                        f"INSERT INTO {table}(project, {subject_col}, {other_col})"
                        " VALUES (?,?,?)",
                        (project, pair_ids[0], pair_ids[1]),
                    )
                events = [(kind_a, id_a, other_change[0]), (kind_b, id_b, other_change[1])]
            _stamp_and_log(con, project, now, events)

        return {
            "verb": verb,
            "table": table,
            "subject": ref_a,
            "other": ref_b,
            "optional": optional_value if dependency else 0,
            "message": message,
        }
    finally:
        if own:
            con.close()


# ----------------------------------------------------------- feature graph (HP-3)
def feature_graph(project: str, con: Optional[sqlite3.Connection] = None) -> dict:
    """The non-cut features and their dependency edges, for ``graph``.

    A cut feature is out of the graph, and so is an edge that touches one: the
    written reason for cutting a feature is that nothing depends on it any more.
    """
    own = con is None
    con = con or connect()
    try:
        if not project_exists(project, con):
            raise StoreError(f"project {project!r} does not exist")
        nodes = {
            row["id"]: row["title"] or ""
            for row in con.execute(
                "SELECT id, title FROM feature WHERE project = ? AND status != 'cut'"
                " ORDER BY id",
                (project,),
            )
        }
        edges: list = []
        mandatory: list = []
        for row in con.execute(
            "SELECT feature_id, needs_id, optional FROM feature_dependency"
            " WHERE project = ? ORDER BY needs_id, feature_id",
            (project,),
        ):
            if row["feature_id"] not in nodes or row["needs_id"] not in nodes:
                continue
            edges.append((row["feature_id"], row["needs_id"], int(row["optional"])))
            if not row["optional"]:
                mandatory.append((row["feature_id"], row["needs_id"]))
        return {"nodes": nodes, "edges": edges, "mandatory": mandatory}
    finally:
        if own:
            con.close()
