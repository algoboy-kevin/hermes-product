"""``migrate_fg``: the feature-graph plugin's store becomes F# + S# rows (card HP-15).

The feature graph (a different Hermes plugin, ``~/.hermes/feature-graph``) already
holds what this product model calls features and specs; what it does not hold is
requirements, criteria and documents. This module is the mechanical, one-off
bridge, driven as ``python migrate_fg.py --graph gmd --project gmd`` and also
importable and tested as a plain module (there is no CLI verb).

Nothing here writes a row itself: the mapping is turned into the S3 export
layout, staged in a temp directory, handed to ``export.import_export``, and the
stage is removed — the same division of labour as ``importmd`` (HP-10), so every
rule the importer already owns (validation, explicit ids, one transaction,
dangling-link checks) applies unchanged.

Layers:

* ``map_nodes`` is PURE — dicts in, rows out, no sqlite and no filesystem — so
  every mapping rule is testable by hand;
* ``read_graph`` reads ANOTHER plugin's sqlite db through a private ``.backup``
  copy (the live db is never opened: a ``mode=ro`` open of this WAL db fails and
  would create ``-wal``/``-shm`` beside it);
* ``migrate`` guards the project, stages the export and calls the importer;
* ``main`` is the script entry point.

The mapping is lossy on purpose, and every loss is reported as a ``misfit`` line:
interacts/metrics/cards have no home in S1, a node id that is not a slug loses
its slug, a dependency on an unknown node or a dependency cycle is skipped, and a
title longer than the field limit is clipped. Legacy is flagged in every F#/S#
``notes`` with the S6 section 1 rule 5 marker, and the node's original notes are
kept byte-exact below it.
"""

from __future__ import annotations

import argparse
import io
import json
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path
from typing import Any, Optional, Union

from ruamel.yaml import YAML

try:  # package submodule (Hermes plugin loader)
    from . import export, model, store
    from . import graph as graph_module
except ImportError:  # top-level module (test suite)
    import export  # type: ignore[no-redef]
    import model  # type: ignore[no-redef]
    import store  # type: ignore[no-redef]
    import graph as graph_module  # type: ignore[no-redef]

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_CONFLICT = 3

#: model.validate_field's title rule (3-80 characters), enforced by clipping
TITLE_MAX = 80

#: the S6 section 1 rule 5 marker every imported entry's notes start with
LEGACY_PREFIX = "legacy: imported from feature-graph"

#: the whole S3 link table set, so links.yaml is explicit and deterministic
EMPTY_LINKS = {
    "feature_requirement": [],
    "spec_requirement": [],
    "feature_spec": [],
    "feature_dependency": [],
    "requirement_dependency": [],
    "spec_dependency": [],
    "doc_link": [],
    "external_link": [],
}


# ------------------------------------------------------------------- the mapping
def _clip_title(text: str, node_id: str, misfits: list[str]) -> str:
    """The node name as a valid title: stripped, clipped to ``TITLE_MAX``."""
    title = str(text or "").strip()
    if len(title) <= TITLE_MAX:
        return title
    misfits.append(f"title: {node_id} clipped to {TITLE_MAX} characters")
    return title[:TITLE_MAX].rstrip()


def _notes_for(entry: dict, graph: str) -> str:
    """The legacy marker (plus its facts), then the node's original notes byte-exact."""
    node_id = str(entry.get("id") or "")
    level = str(entry.get("verified") or "none")
    text = f"{LEGACY_PREFIX} {graph}/{node_id}; verified={level}"
    if entry.get("parent"):
        text += f"; parent={entry['parent']}"
    if entry.get("venue"):
        text += f"; venue={entry['venue']}"
    if entry.get("override"):
        text += f"; override={entry['override']}"
        if entry.get("override_reason"):
            text += f" ({entry['override_reason']})"
    original = entry.get("notes") or ""
    if original:
        text += f"\n\n{original}"
    return text


def _count(value: Any) -> int:
    """How many members a decoded JSON column holds (a list, a mapping or nothing)."""
    if not value:
        return 0
    try:
        return len(value)
    except TypeError:                       # a bare scalar: one member
        return 1


def map_nodes(nodes: list[dict], graph: str) -> dict:
    """Map feature-graph nodes to S3 feature/spec rows and link rows (PURE).

    ``nodes`` arrive in ``position, id`` order and become F1..Fn in that order.
    Returns ``{"features", "specs", "links", "misfits"}``; every unrepresentable
    fact is one sorted ``misfit`` line, never a silent drop.
    """
    features: list[dict] = []
    specs: list[dict] = []
    misfits: list[str] = []
    id_by_node: dict[str, int] = {}
    spec_id_by_path: dict[str, int] = {}
    feature_spec: list[list[int]] = []
    edges: list[tuple[int, int]] = []

    # -- one F# per node, and one S# per distinct spec path (first-seen order)
    for index, entry in enumerate(nodes, start=1):
        node_id = str(entry.get("id") or "")
        id_by_node[node_id] = index
        title = _clip_title(entry.get("name") or "", node_id, misfits)

        row: dict[str, Any] = {
            "id": index,
            "title": title,
            "layer": "foundation" if not entry.get("parent") and not entry.get("depends_on")
            else "core",
            "status": "planned" if str(entry.get("verified") or "none") in ("none", "")
            else "shipped",
            "infra": False,
            "blocked": False,
            "notes": _notes_for(entry, graph),
        }
        slug = node_id
        if model.FEATURE_SLUG_RE.match(slug):
            row["slug"] = slug
        else:
            misfits.append(f"slug: {node_id} is not a valid slug (kept in notes)")
        if entry.get("description"):
            row["description"] = str(entry["description"])
        if entry.get("packages"):
            row["paths"] = [str(path) for path in entry["packages"]]
        features.append(row)

        counts = {name: _count(entry.get(name)) for name in ("interacts", "metrics", "cards")}
        if any(counts.values()):
            misfits.append(
                f"dropped: {node_id} interacts={counts['interacts']}"
                f" metrics={counts['metrics']} cards={counts['cards']}")

        spec_path = entry.get("spec")
        if spec_path:
            if spec_path not in spec_id_by_path:
                spec_id = len(specs) + 1
                spec_id_by_path[spec_path] = spec_id
                specs.append({
                    "id": spec_id,
                    "title": Path(str(spec_path)).stem[:TITLE_MAX].rstrip(),
                    "summary": f"Imported spec document {spec_path} (legacy)",
                    "status": "draft",
                    "paths": [str(spec_path)],
                    "notes": f"{LEGACY_PREFIX} {graph}; spec path {spec_path}",
                    "blocked": False,
                })
            feature_spec.append([index, spec_id_by_path[spec_path]])

    # -- dependencies, by node id -> F# id
    for entry in nodes:
        node_id = str(entry.get("id") or "")
        for needs in entry.get("depends_on") or []:
            if needs == node_id:
                misfits.append(f"dependency: {node_id} needs itself")
            elif needs not in id_by_node:
                misfits.append(f"dependency: {node_id} needs unknown node {needs}")
            else:
                edges.append((id_by_node[node_id], id_by_node[needs]))
    edges = list(dict.fromkeys(edges))       # one row per edge, first-seen order

    # -- a cycle is never imported: report it and drop every edge of the loop
    while True:
        cycle = graph_module.find_cycle(edges)
        if cycle is None:
            break
        path = " -> ".join(f"F{value}" for value in cycle)
        misfits.append(f"cycle: {path} (every edge of the cycle was skipped)")
        victims = set(zip(cycle, cycle[1:]))
        edges = [edge for edge in edges if edge not in victims]

    links = {table: list(rows) for table, rows in EMPTY_LINKS.items()}
    links["feature_spec"] = feature_spec
    links["feature_dependency"] = [[feature_id, needs_id, False]
                                   for feature_id, needs_id in edges]
    return {
        "features": features,
        "specs": specs,
        "links": links,
        "misfits": sorted(set(misfits), key=lambda line: (line.split(":", 1)[0], line)),
    }


# ------------------------------------------------------------------ reading the db
def _node_dict(row: sqlite3.Row) -> dict:
    """One nodes row as a plain dict with the five JSON columns decoded."""
    def decode(column: str) -> Any:
        text = row[column]
        if not text:
            return []
        try:
            return json.loads(text)
        except (TypeError, ValueError) as exc:
            raise store.StoreError(
                f"node {row['id']}: {column} is not JSON ({exc})") from exc

    return {
        "id": row["id"],
        "name": row["name"] or "",
        "parent": row["parent"],
        "description": row["description"] or "",
        "notes": row["notes"] or "",
        "packages": decode("packages"),
        "metrics": decode("metrics"),
        "spec": row["spec"],
        "cards": decode("cards"),
        "venue": row["venue"],
        "depends_on": decode("depends_on"),
        "interacts": decode("interacts"),
        "verified": row["verified"] or "none",
        "override": row["override"],
        "override_reason": row["override_reason"],
        "position": int(row["position"] or 0),
    }


def read_graph(db_path: Union[str, Path], graph: str) -> list[dict]:
    """One graph's nodes, in ``position, id`` order, read through a private copy.

    The live db is only COPIED (bytes plus any ``-wal``/``-shm``): SQLite's
    ``.backup`` then produces a private snapshot, which is converted out of WAL
    mode (a ``mode=ro`` open of a WAL db needs to create ``-shm``) and opened
    read-only with ``PRAGMA query_only``. The source file and its directory are
    never written, so no ``-wal``/``-shm`` appears beside it.
    """
    source = Path(db_path).expanduser()
    if not source.is_file():
        raise store.StoreError(f"{source}: no feature-graph db")
    directory = Path(tempfile.mkdtemp(prefix="product-migrate-fg-src-"))
    try:
        copy = directory / "source.db"
        shutil.copy2(source, copy)
        for suffix in ("-wal", "-shm"):
            side = Path(f"{source}{suffix}")
            if side.is_file():
                shutil.copy2(side, Path(f"{copy}{suffix}"))
        snapshot = directory / "snapshot.db"
        con = sqlite3.connect(str(copy))
        try:
            destination = sqlite3.connect(str(snapshot))
            try:
                con.backup(destination)
            finally:
                destination.close()
        finally:
            con.close()

        plain = sqlite3.connect(str(snapshot))
        try:
            plain.execute("PRAGMA journal_mode = DELETE")
        finally:
            plain.close()

        con = sqlite3.connect(f"file:{snapshot}?mode=ro", uri=True)
        con.row_factory = sqlite3.Row
        try:
            con.execute("PRAGMA query_only = 1")
            if con.execute("SELECT 1 FROM graphs WHERE name = ?", (graph,)).fetchone() is None:
                raise store.StoreError(f"graph {graph} does not exist in {source}")
            rows = con.execute(
                "SELECT * FROM nodes WHERE graph = ? ORDER BY position, id", (graph,)
            ).fetchall()
            return [_node_dict(row) for row in rows]
        finally:
            con.close()
    except sqlite3.Error as exc:
        raise store.StoreError(f"{source}: not a feature-graph db ({exc})") from exc
    finally:
        shutil.rmtree(directory, ignore_errors=True)


# ------------------------------------------------------------------ the S3 stage
def _yaml_bytes(structure: Any) -> bytes:
    """Deterministic YAML (no folding), parsed back by ``export.load_export``."""
    buffer = io.StringIO()
    yaml = YAML()
    yaml.width = 10 ** 6
    yaml.dump(structure, buffer)
    return buffer.getvalue().encode("utf-8")


def _stage(mapped: dict, project: str) -> Path:
    """The mapped rows as a staged S3 export directory, with explicit ids."""
    features = mapped["features"]
    specs = mapped["specs"]
    counters = {"R": 1, "A": 1, "F": len(features) + 1, "S": len(specs) + 1, "DOC": 1}
    project_row = store.get_project(project) or {}
    project_doc: dict[str, Any] = {
        "slug": project,
        "name": project_row.get("name") or project,
        "counters": counters,
    }
    if project_row.get("root") is not None:
        project_doc["root"] = project_row["root"]

    files = {
        "project.yaml": _yaml_bytes(project_doc),
        "requirements.yaml": _yaml_bytes([]),
        "criteria.yaml": _yaml_bytes([]),
        "features.yaml": _yaml_bytes(features),
        "specs.yaml": _yaml_bytes(specs),
        "docs.yaml": _yaml_bytes([]),
        "links.yaml": _yaml_bytes(mapped["links"]),
        "history.yaml": _yaml_bytes([]),
    }
    directory = Path(tempfile.mkdtemp(prefix="product-migrate-fg-"))
    try:
        for name, payload in files.items():
            (directory / name).write_bytes(payload)
        (directory / "docs").mkdir()
    except BaseException:
        shutil.rmtree(directory, ignore_errors=True)
        raise
    return directory


# ------------------------------------------------------------------------ the verb
def migrate(db_path: Union[str, Path], graph: str, project: str, *,
            dry_run: bool = False) -> dict:
    """Fill an EXISTING, EMPTY project from one graph through the S3 importer.

    The project must exist and hold no features and no specs yet, so the ids the
    import assigns (F1..Fn, S1..Sm) are collision-free. ``dry_run`` maps and
    reports without writing anything.
    """
    if not store.project_exists(project):
        raise store.StoreError(
            f"project {project} does not exist"
            f" (create it with: project add {project} <name> --root DIR)")
    if store.list_entities(project, "feature") or store.list_entities(project, "spec"):
        raise store.StoreError(
            f"project {project} already has features/specs;"
            " migrate-fg only fills an empty project")

    mapped = map_nodes(read_graph(db_path, graph), graph)
    result: dict[str, Any] = {
        "features": len(mapped["features"]),
        "specs": len(mapped["specs"]),
        "dependencies": len(mapped["links"]["feature_dependency"]),
        "misfits": mapped["misfits"],
        "dry_run": dry_run,
        "inserted": 0,
        "skipped": 0,
        "conflicts": 0,
    }
    if dry_run:
        return result
    stage = _stage(mapped, project)
    try:
        imported = export.import_export(stage, as_slug=project)
    finally:
        shutil.rmtree(stage, ignore_errors=True)
    result.update({"inserted": imported["inserted"], "skipped": imported["skipped"],
                   "conflicts": imported["conflicts"]})
    return result


def main(argv: Optional[list[str]] = None) -> int:
    """``python migrate_fg.py --graph NAME --project SLUG [--db PATH] [--dry-run]``.

    Exit 0 on success, 1 on an error or a refusal, 3 when the importer reports
    conflicts. Output is one counts line, then one ``misfit:`` line each.
    """
    parser = argparse.ArgumentParser(
        prog="migrate_fg.py",
        description="import a feature-graph graph as F# + S# rows (legacy) into an"
                    " empty product project")
    parser.add_argument("--graph", required=True, help="the feature-graph graph name")
    parser.add_argument("--project", required=True, help="the empty product project slug")
    parser.add_argument("--db", default=None,
                        help="the feature-graph db (default"
                             " $HERMES_HOME/feature-graph/feature-graph.db)")
    parser.add_argument("--dry-run", action="store_true",
                        help="map and report without writing anything")
    args = parser.parse_args(argv)

    db_path = (Path(args.db).expanduser() if args.db
               else store.hermes_home() / "feature-graph" / "feature-graph.db")
    try:
        result = migrate(db_path, args.graph, args.project, dry_run=args.dry_run)
    except (store.StoreError, model.ValidationError) as exc:
        print(exc, file=sys.stderr)
        return EXIT_ERROR

    counts = (f"{result['features']} features, {result['specs']} specs,"
              f" {result['dependencies']} dependencies")
    if args.dry_run:
        print(f"migrate-fg {args.graph} -> {args.project} (dry run): {counts}")
    else:
        print(f"migrate-fg {args.graph} -> {args.project}: {counts}"
              f" ({result['inserted']} inserted, {result['skipped']} skipped,"
              f" {result['conflicts']} conflicts)")
    for line in result["misfits"]:
        print(f"misfit: {line}")
    return EXIT_CONFLICT if result["conflicts"] else EXIT_OK


if __name__ == "__main__":                                    # pragma: no cover
    raise SystemExit(main())
