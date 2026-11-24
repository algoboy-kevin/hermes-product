"""HP-15: ``migrate_fg`` imports the feature-graph plugin's store as F# + S#.

Three layers are pinned here:

* ``map_nodes`` alone, fed plain dicts (no sqlite, no filesystem): the F# order
  and ids, the layer rule (foundation only for a node with no parent and no
  dependency, everything else core), the status rule (``verified`` decides
  ``shipped``/``planned``), the slug, title, notes marker and byte-exact
  original notes, the dependency rows with their misfits (unknown, self, cycle),
  the dropped interacts/metrics/cards line, and the per-path spec dedupe with its
  ``feature_spec`` links. No R#, A# or DOC# is ever produced;
* ``read_graph`` through a private ``.backup`` copy of a tmp feature-graph db
  (that db shape only): node order by ``position, id``, the JSON columns decoded,
  an unknown graph refused with its exact message, and the source file left
  byte-identical with no ``-wal``/``-shm`` beside it;
* the end-to-end run through ``migrate`` and ``product_command``: the counts
  line, the rows read back with ``list``/``show``, a lint with zero errors and
  only ``feature_without_requirement`` warnings, ``next`` listing nothing for
  shipped features, the empty-project refusal, an unknown project, ``--dry-run``
  writing nothing, ``main()`` exit codes, and two fresh installs exporting
  byte-identical text.

Every db is built under ``tmp_path``; the real feature-graph store is never read.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

import export
import migrate_fg
import store
from _helpers import cli, home  # noqa: F401  (fixtures imported for discovery)

#: the title limit of model.py's field rule (3-80 characters)
TITLE_MAX = 80


# --------------------------------------------------------------- pure fixtures
def node(node_id: str, **fields) -> dict:
    """One node dict shaped like ``read_graph`` returns it."""
    base = {
        "id": node_id,
        "name": f"{node_id} name",
        "parent": None,
        "description": "",
        "notes": "",
        "packages": [],
        "metrics": [],
        "spec": None,
        "cards": [],
        "venue": None,
        "depends_on": [],
        "interacts": [],
        "verified": "none",
        "override": None,
        "override_reason": None,
        "position": 0,
    }
    base.update(fields)
    return base


def mapped(*nodes: dict, graph: str = "gmd") -> dict:
    return migrate_fg.map_nodes(list(nodes), graph)


def by_slug(result: dict, slug: str) -> dict:
    return next(row for row in result["features"] if row.get("slug") == slug)


# ------------------------------------------------------------- the feature graph
FG_SCHEMA = """
CREATE TABLE graphs (
  name        TEXT PRIMARY KEY,
  description TEXT NOT NULL DEFAULT '',
  root        TEXT,
  created_at  INTEGER NOT NULL DEFAULT 0,
  updated_at  INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE nodes (
  graph           TEXT NOT NULL REFERENCES graphs(name) ON DELETE CASCADE,
  id              TEXT NOT NULL,
  name            TEXT NOT NULL DEFAULT '',
  parent          TEXT,
  description     TEXT NOT NULL DEFAULT '',
  notes           TEXT NOT NULL DEFAULT '',
  venue           TEXT,
  verified        TEXT NOT NULL DEFAULT 'none',
  override        TEXT,
  override_reason TEXT,
  packages        TEXT NOT NULL DEFAULT '[]',
  metrics         TEXT NOT NULL DEFAULT '[]',
  spec            TEXT,
  cards           TEXT NOT NULL DEFAULT '[]',
  depends_on      TEXT NOT NULL DEFAULT '[]',
  interacts       TEXT NOT NULL DEFAULT '[]',
  position        INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (graph, id)
);
"""

NODE_COLUMNS = ("graph", "id", "name", "parent", "description", "notes", "venue",
                "verified", "override", "override_reason", "packages", "metrics",
                "spec", "cards", "depends_on", "interacts", "position")
JSON_COLUMNS = ("packages", "metrics", "cards", "depends_on", "interacts")


def make_fg_db(db_path: Path, nodes: list[dict], *, graph: str = "gmd",
               root: str | None = None, wal: bool = False) -> Path:
    """Write a tmp feature-graph db with only the tables/columns read_graph uses."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(db_path))
    try:
        con.executescript(FG_SCHEMA)
        con.execute("INSERT INTO graphs(name, root) VALUES (?, ?)", (graph, root))
        placeholders = ", ".join("?" for _ in NODE_COLUMNS)
        for entry in nodes:
            values = []
            for column in NODE_COLUMNS:
                if column == "graph":
                    values.append(graph)
                elif column in JSON_COLUMNS:
                    values.append(json.dumps(entry.get(column) or []))
                elif column in ("parent", "venue", "override", "override_reason", "spec"):
                    values.append(entry.get(column))
                elif column == "position":
                    values.append(int(entry.get(column, 0)))
                else:
                    values.append(str(entry.get(column) or ""))
            con.execute(f"INSERT INTO nodes({', '.join(NODE_COLUMNS)})"
                        f" VALUES ({placeholders})", tuple(values))
        con.commit()
        if wal:
            con.execute("PRAGMA journal_mode = WAL")
    finally:
        con.close()
    for suffix in ("-wal", "-shm"):
        Path(f"{db_path}{suffix}").unlink(missing_ok=True)
    return db_path


#: the end-to-end graph: five shipped features, two distinct spec paths, three edges
E2E_NODES = [
    node("core-primitives", name="Core primitives", verified="unit",
         spec="docs/specs/S1-core.md", packages=["core/a.go", "core/b.go"],
         notes="Original notes.\nSecond line.", position=0),
    node("market-data", name="Market data pipeline", verified="sim", position=1),
    node("md-router", name="MD router", parent="market-data",
         depends_on=["core-primitives"], verified="sim",
         spec="docs/ARCHITECTURE.md", position=2),
    node("grid-reconciler", name="Grid reconciler", parent="market-data",
         depends_on=["md-router"], verified="unit",
         spec="docs/ARCHITECTURE.md", position=3),
    node("shard-loop", name="Shard loop", parent="market-data",
         depends_on=["grid-reconciler"], verified="mainnet", position=4),
]

E2E_COUNTS = "5 features, 2 specs, 3 dependencies"


@pytest.fixture()
def fg_db(tmp_path: Path) -> Path:
    return make_fg_db(tmp_path / "feature-graph.db", E2E_NODES)


@pytest.fixture()
def gmd(home: Path) -> str:  # noqa: F811  (the `home` fixture sets HERMES_HOME)
    store.create_project("gmd", "GMD")
    return "gmd"


def rows(kind: str, project: str = "gmd") -> list[dict]:
    connection = store.connect()
    try:
        return [dict(row) for row in connection.execute(
            f"SELECT * FROM {store.ENTITY_TABLE[kind]} WHERE project = ? ORDER BY id",
            (project,))]
    finally:
        connection.close()


# ------------------------------------------------------------ map: order and layer
def test_ids_follow_the_node_order() -> None:
    result = mapped(node("c", position=5), node("a", position=0), node("b", position=2))
    assert [row["id"] for row in result["features"]] == [1, 2, 3]
    assert [row["slug"] for row in result["features"]] == ["c", "a", "b"]
    assert result["misfits"] == []


def test_layer_is_foundation_only_without_parent_and_dependencies() -> None:
    result = mapped(
        node("alone"),
        node("depends-only", depends_on=["alone"]),
        node("child", parent="alone"),
    )
    assert [row["layer"] for row in result["features"]] == ["foundation", "core", "core"]


def test_layer_never_grows() -> None:
    result = mapped(node("a"), node("b", parent="a", depends_on=["a"]))
    assert {row["layer"] for row in result["features"]} <= {"foundation", "core"}


def test_status_follows_verified() -> None:
    result = mapped(
        node("a", verified="none"),
        node("b", verified="unit"),
        node("c", verified="sim"),
        node("d", verified="mainnet"),
        node("e", verified=""),
    )
    assert [row["status"] for row in result["features"]] == [
        "planned", "shipped", "shipped", "shipped", "planned"]


# ------------------------------------------------------------ map: F# row fields
def test_feature_row_carries_title_description_paths_and_infra() -> None:
    result = mapped(node("core", name="Core primitives", description="Shared types.",
                         packages=["core/a.go", "core/b.go"]))
    row = result["features"][0]
    assert row["title"] == "Core primitives"
    assert row["description"] == "Shared types."
    assert row["paths"] == ["core/a.go", "core/b.go"]
    assert row["infra"] is False
    assert row["blocked"] is False
    assert row["status"] == "planned"
    assert row["layer"] == "foundation"


def test_valid_node_id_is_the_slug() -> None:
    result = mapped(node("core-primitives"))
    assert result["features"][0]["slug"] == "core-primitives"
    assert result["misfits"] == []


def test_invalid_node_id_loses_the_slug_with_a_misfit() -> None:
    result = mapped(node("Core Primitives!"))
    row = result["features"][0]
    assert "slug" not in row or row["slug"] is None
    assert "slug: Core Primitives! is not a valid slug (kept in notes)" in result["misfits"]
    assert row["notes"].startswith("legacy: imported from feature-graph gmd/Core Primitives!")


def test_title_is_clipped_to_the_limit_with_a_misfit() -> None:
    result = mapped(node("long", name="x" * (TITLE_MAX + 20)))
    assert len(result["features"][0]["title"]) == TITLE_MAX
    assert any(line.startswith("title: long") and "clipped" in line
               for line in result["misfits"])


def test_notes_marker_has_parent_venue_and_override() -> None:
    result = mapped(node("n", parent="p", venue="aster", override="retired",
                         override_reason="superseded by x", verified="unit"))
    assert result["features"][0]["notes"] == (
        "legacy: imported from feature-graph gmd/n; verified=unit; parent=p;"
        " venue=aster; override=retired (superseded by x)")


def test_notes_marker_alone_when_nothing_else_is_set() -> None:
    assert mapped(node("n"))["features"][0]["notes"] == (
        "legacy: imported from feature-graph gmd/n; verified=none")


def test_original_notes_are_appended_byte_exact() -> None:
    original = "Line one\n\nLine two  \ntrailing space above\n"
    result = mapped(node("n", notes=original))
    row = result["features"][0]
    marker = "legacy: imported from feature-graph gmd/n; verified=none"
    assert row["notes"] == f"{marker}\n\n{original}"


def test_node_without_notes_has_no_blank_line() -> None:
    assert "\n\n" not in mapped(node("n"))["features"][0]["notes"]


# ------------------------------------------------------------------ map: edges
def test_dependency_rows_are_mandatory() -> None:
    result = mapped(node("a"), node("b", depends_on=["a"]))
    assert result["links"]["feature_dependency"] == [[2, 1, False]]
    assert result["misfits"] == []


def test_unknown_dependency_is_skipped_with_a_misfit() -> None:
    result = mapped(node("a"), node("b", depends_on=["a", "nope"]))
    assert result["links"]["feature_dependency"] == [[2, 1, False]]
    assert "dependency: b needs unknown node nope" in result["misfits"]


def test_self_dependency_is_skipped_with_a_misfit() -> None:
    result = mapped(node("a", depends_on=["a"]))
    assert result["links"]["feature_dependency"] == []
    assert "dependency: a needs itself" in result["misfits"]


def test_a_cycle_is_reported_and_all_its_edges_are_skipped() -> None:
    result = mapped(node("a", depends_on=["b"]), node("b", depends_on=["a"]),
                    node("c", depends_on=["a"]))
    assert result["links"]["feature_dependency"] == [[3, 1, False]]
    cycle_lines = [line for line in result["misfits"] if line.startswith("cycle:")]
    assert len(cycle_lines) == 1
    assert "F1" in cycle_lines[0] and "F2" in cycle_lines[0]


def test_two_independent_cycles_are_both_skipped() -> None:
    result = mapped(node("a", depends_on=["b"]), node("b", depends_on=["a"]),
                    node("c", depends_on=["d"]), node("d", depends_on=["c"]))
    assert result["links"]["feature_dependency"] == []
    assert len([line for line in result["misfits"] if line.startswith("cycle:")]) == 2


def test_interacts_metrics_and_cards_are_dropped_with_one_line() -> None:
    result = mapped(node("a", interacts=["b"], metrics=["m1", "m2"], cards=["t_1"]),
                    node("b"))
    assert "dropped: a interacts=1 metrics=2 cards=1" in result["misfits"]
    assert not any(line.startswith("dropped: b ") for line in result["misfits"])


def test_misfits_are_sorted_by_kind_then_text() -> None:
    result = mapped(
        node("n", name="x" * 100, metrics=["m"], depends_on=["nope"]),
        node("Bad!", depends_on=["Bad!"]),
    )
    assert result["misfits"] == sorted(
        result["misfits"], key=lambda line: (line.split(":", 1)[0], line))


def test_map_nodes_is_deterministic() -> None:
    assert mapped(*E2E_NODES) == mapped(*E2E_NODES)


# ------------------------------------------------------------------- map: specs
def test_specs_are_deduped_by_path_with_links_from_every_node() -> None:
    result = mapped(node("a", spec="docs/ARCHITECTURE.md"),
                    node("b", spec="docs/ARCHITECTURE.md", parent="a"))
    assert len(result["specs"]) == 1
    assert result["specs"][0]["id"] == 1
    assert sorted(result["links"]["feature_spec"]) == [[1, 1], [2, 1]]


def test_spec_row_fields() -> None:
    result = mapped(node("a", spec="docs/specs/S33-dynamic-spread.md"))
    row = result["specs"][0]
    assert row["title"] == "S33-dynamic-spread"
    assert row["summary"] == ("Imported spec document docs/specs/S33-dynamic-spread.md"
                              " (legacy)")
    assert row["status"] == "draft"
    assert row["paths"] == ["docs/specs/S33-dynamic-spread.md"]
    assert row["notes"] == ("legacy: imported from feature-graph gmd; spec path"
                            " docs/specs/S33-dynamic-spread.md")
    assert row["blocked"] is False


def test_nodes_without_a_spec_create_none() -> None:
    result = mapped(node("a"), node("b", spec=""))
    assert result["specs"] == []
    assert result["links"]["feature_spec"] == []


def test_no_requirement_criterion_or_doc_rows_are_created() -> None:
    result = mapped(*E2E_NODES)
    assert set(result) == {"features", "specs", "links", "misfits"}
    for table in ("feature_requirement", "spec_requirement", "requirement_dependency",
                  "spec_dependency", "doc_link", "external_link"):
        assert result["links"][table] == []


# --------------------------------------------------------------- read_graph (io)
def test_read_graph_orders_by_position_and_decodes_json(tmp_path: Path) -> None:
    db = make_fg_db(tmp_path / "fg.db", [
        node("late", position=9, packages=["z.go"], depends_on=["early"],
             interacts=["late"], metrics=["m"], cards=["t"]),
        node("early", position=0, venue="aster", verified="unit"),
        node("middle", position=4, parent="early"),
    ])
    found = migrate_fg.read_graph(db, "gmd")
    assert [entry["id"] for entry in found] == ["early", "middle", "late"]
    late = found[2]
    assert late["packages"] == ["z.go"]
    assert late["depends_on"] == ["early"]
    assert late["interacts"] == ["late"]
    assert late["metrics"] == ["m"]
    assert late["cards"] == ["t"]
    assert found[0]["venue"] == "aster"
    assert found[0]["verified"] == "unit"
    assert found[1]["verified"] == "none"


def test_read_graph_unknown_graph_names_the_db(tmp_path: Path) -> None:
    db = make_fg_db(tmp_path / "fg.db", [node("a")])
    with pytest.raises(store.StoreError) as caught:
        migrate_fg.read_graph(db, "nope")
    assert str(caught.value) == f"graph nope does not exist in {db}"


def test_read_graph_leaves_the_source_untouched(tmp_path: Path) -> None:
    db = make_fg_db(tmp_path / "fg.db", [node("a", verified="unit")], wal=True)
    assert not Path(f"{db}-wal").exists() and not Path(f"{db}-shm").exists()
    before_bytes = db.read_bytes()
    before_mtime = db.stat().st_mtime_ns
    migrate_fg.read_graph(db, "gmd")
    assert db.read_bytes() == before_bytes
    assert db.stat().st_mtime_ns == before_mtime
    assert not Path(f"{db}-wal").exists()
    assert not Path(f"{db}-shm").exists()
    assert sorted(path.name for path in tmp_path.iterdir()) == ["fg.db"]


def test_read_graph_missing_db_is_a_store_error(tmp_path: Path) -> None:
    with pytest.raises(store.StoreError):
        migrate_fg.read_graph(tmp_path / "absent.db", "gmd")


# ------------------------------------------------------------- migrate + the CLI
def run_main(capsys, *argv: str) -> tuple[int, str, str]:
    rc = migrate_fg.main(list(argv))
    captured = capsys.readouterr()
    return rc, captured.out, captured.err


def test_migrate_counts_line_and_rows_read_back(fg_db: Path, gmd: str, capsys) -> None:
    rc, out, err = run_main(capsys, "--graph", "gmd", "--project", "gmd",
                            "--db", str(fg_db))
    assert rc == 0, err
    assert out.splitlines()[0] == (
        f"migrate-fg gmd -> gmd: {E2E_COUNTS} (7 inserted, 0 skipped, 0 conflicts)")
    assert len(rows("feature")) == 5
    assert len(rows("spec")) == 2
    assert rows("feature")[0]["notes"].startswith("legacy: imported from feature-graph")

    rc, out, err = cli(capsys, "list", "F", "--project", "gmd")
    assert rc == 0, err
    assert out.splitlines()[0].startswith("F1  shipped  foundation")
    assert len(out.splitlines()) == 5

    rc, out, err = cli(capsys, "list", "S", "--project", "gmd")
    assert rc == 0, err
    assert out.splitlines()[0].startswith("S1  draft  S1-core")

    rc, out, err = cli(capsys, "show", "F1", "--project", "gmd")
    assert rc == 0, err
    notes = [line for line in out.splitlines() if line.startswith("notes:")]
    assert notes and notes[0].startswith(
        "notes: legacy: imported from feature-graph gmd/core-primitives; verified=unit")


def test_migrate_lint_has_zero_errors_and_only_feature_warnings(
        fg_db: Path, gmd: str, capsys) -> None:
    assert run_main(capsys, "--graph", "gmd", "--project", "gmd",
                    "--db", str(fg_db))[0] == 0
    rc, out, err = cli(capsys, "lint", "--json", "--project", "gmd")
    payload = json.loads(out)
    assert payload["errors"] == 0
    by_flag: dict = {}
    for finding in payload["findings"]:
        by_flag[finding["flag"]] = by_flag.get(finding["flag"], 0) + 1
    # the migrated specs carry the importer's "Imported ... (legacy)" summaries: the
    # thin_summary lint now says so, which is exactly what it is for; and they stay
    # `draft` next to the imported shipped features, which spec_behind (HP-25) reports
    assert by_flag == {"feature_without_requirement": 5, "thin_summary": 2, "spec_behind": 2,
                       "no_card_links": 1}
    assert payload["warnings"] == 9
    assert rc == 0, err


def test_migrate_next_lists_nothing_for_shipped_features(
        fg_db: Path, gmd: str, capsys) -> None:
    assert run_main(capsys, "--graph", "gmd", "--project", "gmd",
                    "--db", str(fg_db))[0] == 0
    rc, out, err = cli(capsys, "next", "--project", "gmd")
    assert rc == 0, err
    assert out.splitlines() == ["next (gmd): 0 ready, 0 blocked", "nothing ready"]


def test_migrate_refuses_a_project_that_already_has_features(
        fg_db: Path, gmd: str, capsys) -> None:
    store.create_entity("gmd", "feature", {"title": "Existing", "layer": "core"})
    rc, out, err = run_main(capsys, "--graph", "gmd", "--project", "gmd",
                            "--db", str(fg_db))
    assert rc == 1
    assert err.strip() == ("project gmd already has features/specs;"
                           " migrate-fg only fills an empty project")
    assert len(rows("feature")) == 1


def test_migrate_refuses_a_project_that_already_has_specs(
        fg_db: Path, gmd: str, capsys) -> None:
    store.create_entity("gmd", "spec", {"title": "Existing", "summary": "x"})
    rc, _out, err = run_main(capsys, "--graph", "gmd", "--project", "gmd",
                             "--db", str(fg_db))
    assert rc == 1
    assert "already has features/specs" in err


def test_migrate_unknown_project_names_the_fix(fg_db: Path, home: Path, capsys) -> None:
    rc, _out, err = run_main(capsys, "--graph", "gmd", "--project", "nope",
                             "--db", str(fg_db))
    assert rc == 1
    assert err.strip() == ("project nope does not exist (create it with:"
                           " project add nope <name> --root DIR)")


def test_dry_run_maps_and_writes_nothing(fg_db: Path, gmd: str, capsys) -> None:
    rc, out, err = run_main(capsys, "--graph", "gmd", "--project", "gmd",
                            "--db", str(fg_db), "--dry-run")
    assert rc == 0, err
    assert out.splitlines() == [f"migrate-fg gmd -> gmd (dry run): {E2E_COUNTS}"]
    for kind in ("feature", "spec", "requirement", "criterion", "doc"):
        assert rows(kind) == []
    # and the real run still works afterwards
    assert run_main(capsys, "--graph", "gmd", "--project", "gmd",
                    "--db", str(fg_db))[0] == 0


def test_main_exit_zero_on_success_and_one_on_an_unknown_graph(
        fg_db: Path, gmd: str, capsys) -> None:
    assert run_main(capsys, "--graph", "gmd", "--project", "gmd",
                    "--db", str(fg_db))[0] == 0
    store.create_project("other", "Other")
    rc, _out, err = run_main(capsys, "--graph", "nope", "--project", "other",
                             "--db", str(fg_db))
    assert rc == 1
    assert err.strip() == f"graph nope does not exist in {fg_db}"


def test_main_exit_three_on_import_conflicts(gmd: str, capsys, monkeypatch) -> None:
    def fake_migrate(_db, _graph, _project, *, dry_run=False):
        assert dry_run is False
        return {"features": 1, "specs": 0, "dependencies": 0, "inserted": 0,
                "skipped": 0, "conflicts": 1, "misfits": [], "dry_run": False}

    monkeypatch.setattr(migrate_fg, "migrate", fake_migrate)
    rc, _out, _err = run_main(capsys, "--graph", "gmd", "--project", "gmd",
                              "--db", "/nowhere.db")
    assert rc == 3


def test_the_stage_directory_is_removed(fg_db: Path, gmd: str, capsys, tmp_path) -> None:
    before = {path.name for path in Path(tmp_path).iterdir() if path.is_dir()}
    assert run_main(capsys, "--graph", "gmd", "--project", "gmd",
                    "--db", str(fg_db))[0] == 0
    after = {path.name for path in Path(tmp_path).iterdir() if path.is_dir()}
    assert after == before


def test_two_fresh_installs_export_byte_identically(
        fg_db: Path, tmp_path: Path, capsys, monkeypatch) -> None:
    homes = [tmp_path / "home-a", tmp_path / "home-b"]
    exports = []
    for root in homes:
        monkeypatch.setenv("HERMES_HOME", str(root))
        store.create_project("gmd", "GMD", root="/tmp")
        assert run_main(capsys, "--graph", "gmd", "--project", "gmd",
                        "--db", str(fg_db))[0] == 0
        files, _counts = export.build_export("gmd")
        exports.append(files)
    assert exports[0] == exports[1]
    assert exports[0]["features.yaml"] and exports[0]["links.yaml"]
