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

import os
import re
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional, Union

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


def next_id(con: sqlite3.Connection, project: str, kind: str) -> int:
    """Atomically allocate the next id of ``kind`` for ``project``; starts at 1.

    The counter only ever grows: an id is NEVER reused, even if the row that
    used it was deleted.
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
