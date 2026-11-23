"""Link verbs and the feature graph (card HP-3).

Pinned here: every valid link shape (cross-kind both orders, same-kind
dependencies, optional features), every refusal (criterion, same-kind without
--needs, --needs across kinds, --optional off a feature, retired on each kind,
cross-project, unknown id, duplicate, self, cycles in all three tables including
through an optional edge), unlink and its missing-link error, history on both
sides, A5 through show/list, A6 (nothing changes on a refused call), the
feature-dependency status rule with --override-reason, and `graph` (A15).
"""

from __future__ import annotations

from pathlib import Path

import pytest

import store
from _helpers import (  # `home` is a pytest fixture imported for discovery
    add_criterion,
    add_feature,
    add_requirement,
    add_spec,
    cli,
    history_rows,
    home,
    make_project,
    move,
    set_field,
    table_rows,
)

PROJECT = "shop"


# ------------------------------------------------------------------ valid shapes
@pytest.mark.parametrize("first,second,expected", [
    ("R1", "F1", "R1 linked F1"),
    ("F1", "R1", "F1 linked R1"),
])
def test_feature_requirement_link_works_in_both_orders(
    home: Path, capsys, first: str, second: str, expected: str
) -> None:
    make_project()
    add_requirement()
    add_feature()
    rc, out, err = cli(capsys, "link", first, second)
    assert rc == 0, err
    assert out == expected + "\n"
    assert table_rows(PROJECT, "feature_requirement") == [(1, 1)]
    assert "F:1" in cli(capsys, "list", "R")[1]
    assert "R:1" in cli(capsys, "list", "F")[1]


def test_show_shows_the_link_on_both_sides_A5(home: Path, capsys) -> None:
    make_project()
    add_requirement()
    add_feature()
    assert cli(capsys, "link", "R1", "F1")[0] == 0

    rc, out, err = cli(capsys, "show", "R1")
    assert rc == 0, err
    assert "links: F:1" in out.splitlines()
    rc, out, err = cli(capsys, "show", "F1")
    assert rc == 0, err
    assert "links: R:1" in out.splitlines()


def test_spec_requirement_link(home: Path, capsys) -> None:
    make_project()
    add_requirement()
    add_spec()
    rc, out, err = cli(capsys, "link", "S1", "R1")
    assert rc == 0, err
    assert out == "S1 linked R1\n"
    assert table_rows(PROJECT, "spec_requirement") == [(1, 1)]
    assert "S:1" in cli(capsys, "list", "R")[1]
    assert "R:1" in cli(capsys, "list", "S")[1]


def test_feature_spec_link(home: Path, capsys) -> None:
    make_project()
    add_feature()
    add_spec()
    rc, out, err = cli(capsys, "link", "F1", "S1")
    assert rc == 0, err
    assert out == "F1 linked S1\n"
    assert table_rows(PROJECT, "feature_spec") == [(1, 1)]
    assert "S:1" in cli(capsys, "list", "F")[1]
    assert "F:1" in cli(capsys, "list", "S")[1]


def test_feature_dependency(home: Path, capsys) -> None:
    make_project()
    add_feature(title="Stripe integration")
    add_feature(title="Accounts")
    rc, out, err = cli(capsys, "link", "F1", "--needs", "F2")
    assert rc == 0, err
    assert out == "F1 needs F2\n"
    assert table_rows(PROJECT, "feature_dependency") == [(1, 2, 0)]
    assert "needs F:2" in cli(capsys, "list", "F")[1]
    assert "links: needs F:2" in cli(capsys, "show", "F1")[1].splitlines()


def test_optional_feature_dependency_is_stored_and_drawn_dashed(home: Path, capsys) -> None:
    make_project()
    add_feature(title="Stripe integration")
    add_feature(title="Accounts")
    rc, out, err = cli(capsys, "link", "F1", "--needs", "F2", "--optional")
    assert rc == 0, err
    assert out == "F1 needs F2 (optional)\n"
    assert table_rows(PROJECT, "feature_dependency") == [(1, 2, 1)]
    assert "F2 -.-> F1" in cli(capsys, "graph")[1]


def test_requirement_dependency(home: Path, capsys) -> None:
    make_project()
    add_requirement()
    add_requirement(title="Accounts")
    rc, out, err = cli(capsys, "link", "R1", "--needs", "R2")
    assert rc == 0, err
    assert out == "R1 needs R2\n"
    assert table_rows(PROJECT, "requirement_dependency") == [(1, 2)]


def test_spec_dependency(home: Path, capsys) -> None:
    make_project()
    add_spec(title="Payments")
    add_spec(title="Accounts")
    rc, out, err = cli(capsys, "link", "S1", "--needs", "S2")
    assert rc == 0, err
    assert out == "S1 needs S2\n"
    assert table_rows(PROJECT, "spec_dependency") == [(1, 2)]


# ---------------------------------------------------------------------- unlink
def test_unlink_a_relation(home: Path, capsys) -> None:
    make_project()
    add_requirement()
    add_feature()
    assert cli(capsys, "link", "F1", "R1")[0] == 0
    rc, out, err = cli(capsys, "unlink", "F1", "R1")
    assert rc == 0, err
    assert out == "F1 unlinked R1\n"
    assert table_rows(PROJECT, "feature_requirement") == []


def test_unlink_a_dependency(home: Path, capsys) -> None:
    make_project()
    add_feature(title="Stripe integration")
    add_feature(title="Accounts")
    assert cli(capsys, "link", "F1", "--needs", "F2")[0] == 0
    rc, out, err = cli(capsys, "unlink", "F1", "--needs", "F2")
    assert rc == 0, err
    assert out == "F1 no longer needs F2\n"
    assert table_rows(PROJECT, "feature_dependency") == []


def test_unlink_a_link_that_does_not_exist(home: Path, capsys) -> None:
    # HP-12: old -> rc 1 with "no such link: F1 R1" on stderr; new -> rc 0 with
    # "not linked: F1 R1" on stdout and nothing written (idempotent unlink).
    make_project()
    add_requirement()
    add_feature()
    rc, out, err = cli(capsys, "unlink", "F1", "R1")
    assert rc == 0, err
    assert out == "not linked: F1 R1\n"
    assert table_rows(PROJECT, "feature_requirement") == []
    assert history_rows(PROJECT, "feature", 1) == []
    assert history_rows(PROJECT, "requirement", 1) == []


# ----------------------------------------------------------- refusals: the shape
def test_duplicate_link_is_refused(home: Path, capsys) -> None:
    # HP-12: old -> rc 1 with "already linked" on stderr; new -> rc 0 with the same
    # message on stdout and nothing written (idempotent link).
    make_project()
    add_requirement()
    add_feature()
    assert cli(capsys, "link", "F1", "R1")[0] == 0
    before = history_rows(PROJECT, "feature", 1)
    rc, out, err = cli(capsys, "link", "R1", "F1")
    assert rc == 0, err
    assert out == "already linked: R1 F1\n"
    assert table_rows(PROJECT, "feature_requirement") == [(1, 1)]
    assert history_rows(PROJECT, "feature", 1) == before


def test_self_dependency_is_refused(home: Path, capsys) -> None:
    make_project()
    add_feature()
    rc, _out, err = cli(capsys, "link", "F1", "--needs", "F1")
    assert rc == 1
    assert "cannot link F1 to itself" in err
    assert table_rows(PROJECT, "feature_dependency") == []


def test_a_criterion_is_never_linkable(home: Path, capsys) -> None:
    make_project()
    add_requirement()
    add_criterion()
    rc, _out, err = cli(capsys, "link", "A1", "R1")
    assert rc == 1
    assert "a criterion belongs to its requirement" in err
    rc, _out, err = cli(capsys, "link", "R1", "A1")
    assert rc == 1
    assert "a criterion belongs to its requirement" in err


def test_same_kind_pair_without_needs_names_the_valid_shapes(home: Path, capsys) -> None:
    make_project()
    add_feature(title="Stripe integration")
    add_feature(title="Accounts")
    rc, _out, err = cli(capsys, "link", "F1", "F2")
    assert rc == 1
    assert "cannot link F1 F2: same-kind pairs are a dependency" in err
    assert "F1 --needs F2" in err
    assert table_rows(PROJECT, "feature_dependency") == []


def test_needs_across_kinds_names_the_valid_shapes(home: Path, capsys) -> None:
    make_project()
    add_requirement()
    add_feature()
    rc, _out, err = cli(capsys, "link", "F1", "--needs", "R1")
    assert rc == 1
    assert "cannot link F1 --needs R1: --needs joins two entries of the same kind" in err
    assert "F1 R1" in err


def test_optional_is_only_for_feature_dependencies(home: Path, capsys) -> None:
    make_project()
    add_requirement()
    add_requirement(title="Accounts")
    rc, _out, err = cli(capsys, "link", "R1", "--needs", "R2", "--optional")
    assert rc == 1
    assert "--optional is only valid for a feature dependency" in err
    assert table_rows(PROJECT, "requirement_dependency") == []


# ------------------------------------------------------------------- cycles (A14)
def test_feature_dependency_cycle_is_refused_A14(home: Path, capsys) -> None:
    make_project()
    add_feature(title="Stripe integration")
    add_feature(title="Accounts")
    assert cli(capsys, "link", "F1", "--needs", "F2")[0] == 0
    rc, _out, err = cli(capsys, "link", "F2", "--needs", "F1")
    assert rc == 1
    assert "would create a dependency cycle: F1 -> F2 -> F1" in err
    assert table_rows(PROJECT, "feature_dependency") == [(1, 2, 0)]


def test_requirement_dependency_cycle_is_refused(home: Path, capsys) -> None:
    make_project()
    add_requirement()
    add_requirement(title="Accounts")
    assert cli(capsys, "link", "R1", "--needs", "R2")[0] == 0
    rc, _out, err = cli(capsys, "link", "R2", "--needs", "R1")
    assert rc == 1
    assert "would create a dependency cycle: R1 -> R2 -> R1" in err
    assert table_rows(PROJECT, "requirement_dependency") == [(1, 2)]


def test_spec_dependency_cycle_is_refused(home: Path, capsys) -> None:
    make_project()
    add_spec(title="Payments")
    add_spec(title="Accounts")
    assert cli(capsys, "link", "S1", "--needs", "S2")[0] == 0
    rc, _out, err = cli(capsys, "link", "S2", "--needs", "S1")
    assert rc == 1
    assert "would create a dependency cycle: S1 -> S2 -> S1" in err
    assert table_rows(PROJECT, "spec_dependency") == [(1, 2)]


def test_a_three_node_cycle_is_refused_and_named(home: Path, capsys) -> None:
    make_project()
    for title in ("One", "Two", "Three"):
        add_feature(title=f"Feature {title}")
    assert cli(capsys, "link", "F1", "--needs", "F2")[0] == 0
    assert cli(capsys, "link", "F2", "--needs", "F3")[0] == 0
    rc, _out, err = cli(capsys, "link", "F3", "--needs", "F1")
    assert rc == 1
    assert "F1 -> F2 -> F3 -> F1" in err
    assert len(table_rows(PROJECT, "feature_dependency")) == 2


def test_an_optional_edge_still_closes_a_cycle(home: Path, capsys) -> None:
    make_project()
    add_feature(title="Stripe integration")
    add_feature(title="Accounts")
    assert cli(capsys, "link", "F1", "--needs", "F2", "--optional")[0] == 0
    rc, _out, err = cli(capsys, "link", "F2", "--needs", "F1")
    assert rc == 1
    assert "would create a dependency cycle" in err
    assert table_rows(PROJECT, "feature_dependency") == [(1, 2, 1)]


# --------------------------------------------------- refusals: retired / missing
def test_a_dropped_requirement_cannot_be_linked(home: Path, capsys) -> None:
    make_project()
    add_requirement()
    add_feature()
    add_requirement(title="Accounts")
    assert set_field(capsys, "R2", "status=dropped", "status_reason=Deferred")[0] == 0
    rc, _out, err = cli(capsys, "link", "R2", "F1")
    assert rc == 1
    assert "cannot link R2: it is dropped" in err
    assert table_rows(PROJECT, "feature_requirement") == []


def test_a_cut_feature_cannot_be_linked(home: Path, capsys) -> None:
    make_project()
    add_requirement()
    add_feature()
    add_feature(title="Reports")
    assert set_field(capsys, "F2", "status=cut", "status_reason=Deferred")[0] == 0
    rc, _out, err = cli(capsys, "link", "F2", "R1")
    assert rc == 1
    assert "cannot link F2: it is cut" in err
    assert table_rows(PROJECT, "feature_requirement") == []


def test_a_superseded_spec_cannot_be_linked(home: Path, capsys) -> None:
    make_project()
    add_requirement()
    add_spec(title="Payments")
    add_spec(title="Payments v2")
    assert set_field(capsys, "S2", "status=superseded", "superseded_by=S1")[0] == 0
    rc, _out, err = cli(capsys, "link", "S2", "R1")
    assert rc == 1
    assert "cannot link S2: it is superseded" in err
    assert table_rows(PROJECT, "spec_requirement") == []


def test_a_cross_project_reference_is_refused(home: Path, capsys) -> None:
    make_project("shop")
    make_project("other")
    add_requirement(project="other")
    add_feature(project="shop")
    rc, _out, err = cli(capsys, "link", "F1", "R1", "--project", "shop")
    assert rc == 1
    assert "R1 does not exist in project 'shop'" in err
    assert table_rows(PROJECT, "feature_requirement") == []


def test_an_unknown_id_leaves_the_link_table_unchanged_A6(home: Path, capsys) -> None:
    make_project()
    add_feature()
    rc, _out, err = cli(capsys, "link", "F1", "R9")
    assert rc == 1
    assert "R9 does not exist in project 'shop'" in err
    assert table_rows(PROJECT, "feature_requirement") == []
    assert history_rows(PROJECT, "feature", 1) == []


# --------------------------------------------------------------------- history
def test_link_writes_one_history_row_per_entity(home: Path, capsys) -> None:
    make_project()
    add_requirement()
    add_feature()
    assert cli(capsys, "link", "F1", "R1")[0] == 0
    assert [row["change"] for row in history_rows(PROJECT, "feature", 1)] == ["linked R1"]
    assert [row["change"] for row in history_rows(PROJECT, "requirement", 1)] == ["linked F1"]
    assert all(row["reason"] is None for row in history_rows(PROJECT, "feature", 1))


def test_a_dependency_writes_history_on_both_sides(home: Path, capsys) -> None:
    make_project()
    add_feature(title="Stripe integration")
    add_feature(title="Accounts")
    assert cli(capsys, "link", "F1", "--needs", "F2")[0] == 0
    assert [row["change"] for row in history_rows(PROJECT, "feature", 1)] == ["needs F2"]
    assert [row["change"] for row in history_rows(PROJECT, "feature", 2)] == ["needed by F1"]


def test_unlink_writes_history_on_both_sides(home: Path, capsys) -> None:
    make_project()
    add_requirement()
    add_feature()
    assert cli(capsys, "link", "F1", "R1")[0] == 0
    assert cli(capsys, "unlink", "F1", "R1")[0] == 0
    assert [row["change"] for row in history_rows(PROJECT, "feature", 1)] == [
        "linked R1", "unlinked R1"
    ]
    assert [row["change"] for row in history_rows(PROJECT, "requirement", 1)] == [
        "linked F1", "unlinked F1"
    ]


def test_link_stamps_updated_at_on_both_entities(home: Path, capsys, monkeypatch) -> None:
    make_project()
    add_requirement()
    add_feature()
    monkeypatch.setattr(store, "_now", lambda: "2030-01-01T00:00:00+00:00")
    assert cli(capsys, "link", "F1", "R1")[0] == 0
    assert store.get_entity(PROJECT, "F1")["row"]["updated_at"] == "2030-01-01T00:00:00+00:00"
    assert store.get_entity(PROJECT, "R1")["row"]["updated_at"] == "2030-01-01T00:00:00+00:00"


def test_a_refused_link_writes_nothing(home: Path, capsys) -> None:
    # HP-12: a duplicate link is now idempotent (rc 0) yet still writes nothing, so
    # this A6 test shows the write-nothing claim with both it and a real refusal.
    make_project()
    add_requirement()
    add_feature()
    assert cli(capsys, "link", "F1", "R1")[0] == 0
    assert cli(capsys, "link", "R1", "F1")[0] == 0            # idempotent, not a write
    assert cli(capsys, "link", "F1", "R9")[0] == 1            # refused
    assert len(history_rows(PROJECT, "feature", 1)) == 1
    assert len(history_rows(PROJECT, "requirement", 1)) == 1


# --------------------------------------------------------------- graph (A15)
def test_graph_prints_the_mermaid_graph(home: Path, capsys) -> None:
    make_project()
    add_feature(title="Stripe integration")
    add_feature(title="Accounts")
    add_feature(title="Reports")
    assert cli(capsys, "link", "F1", "--needs", "F2")[0] == 0
    assert cli(capsys, "link", "F3", "--needs", "F2", "--optional")[0] == 0
    rc, out, err = cli(capsys, "graph")
    assert rc == 0, err
    assert out == (
        "graph LR\n"
        '  F1["F1 Stripe integration"]\n'
        '  F2["F2 Accounts"]\n'
        '  F3["F3 Reports"]\n'
        "  F2 --> F1\n"
        "  F2 -.-> F3\n"
    )


def test_graph_leaves_cut_features_and_their_edges_out(home: Path, capsys) -> None:
    make_project()
    add_feature(title="Stripe integration")
    add_feature(title="Reports")
    assert cli(capsys, "link", "F1", "--needs", "F2")[0] == 0
    assert set_field(capsys, "F2", "status=cut", "status_reason=Deferred")[0] == 0
    rc, out, err = cli(capsys, "graph")
    assert rc == 0, err
    assert out == 'graph LR\n  F1["F1 Stripe integration"]\n'


def test_graph_critical_prints_the_longest_mandatory_chain_A15(home: Path, capsys) -> None:
    make_project()
    for title in ("Stripe integration", "Accounts", "Sessions"):
        add_feature(title=title)
    assert cli(capsys, "link", "F1", "--needs", "F2")[0] == 0
    assert cli(capsys, "link", "F2", "--needs", "F3")[0] == 0
    rc, out, err = cli(capsys, "graph", "--critical")
    assert rc == 0, err
    assert out == "critical path (3): F3 -> F2 -> F1\n"


def test_graph_critical_ignores_optional_edges(home: Path, capsys) -> None:
    make_project()
    for title in ("Stripe integration", "Accounts", "Sessions"):
        add_feature(title=title)
    assert cli(capsys, "link", "F1", "--needs", "F2", "--optional")[0] == 0
    assert cli(capsys, "link", "F2", "--needs", "F3")[0] == 0
    rc, out, err = cli(capsys, "graph", "--critical")
    assert rc == 0, err
    assert out == "critical path (2): F3 -> F2\n"


def test_graph_critical_without_features(home: Path, capsys) -> None:
    make_project()
    rc, out, err = cli(capsys, "graph", "--critical")
    assert rc == 0, err
    assert out == "no features\n"


def test_graph_without_features_prints_the_header(home: Path, capsys) -> None:
    make_project()
    rc, out, _err = cli(capsys, "graph")
    assert rc == 0
    assert out == "graph LR\n"


# ------------------------------------------- feature planned -> in_progress rule
def test_mandatory_planned_dependency_blocks_in_progress(home: Path, capsys) -> None:
    make_project()
    add_feature(title="Stripe integration")
    add_feature(title="Accounts")
    assert cli(capsys, "link", "F2", "--needs", "F1")[0] == 0
    rc, _out, err = move(capsys, "F2", "in_progress")
    assert rc == 1
    assert "cannot move F2 planned -> in_progress: depends on F1 (planned)" in err
    assert store.get_entity(PROJECT, "F2")["row"]["status"] == "planned"


def test_an_optional_dependency_does_not_block(home: Path, capsys) -> None:
    make_project()
    add_feature(title="Stripe integration")
    add_feature(title="Accounts")
    assert cli(capsys, "link", "F2", "--needs", "F1", "--optional")[0] == 0
    rc, out, err = move(capsys, "F2", "in_progress")
    assert rc == 0, err
    assert out == "F2 planned -> in_progress\n"


def test_a_dependency_already_in_progress_does_not_block(home: Path, capsys) -> None:
    make_project()
    add_feature(title="Stripe integration")
    add_feature(title="Accounts")
    assert cli(capsys, "link", "F2", "--needs", "F1")[0] == 0
    assert move(capsys, "F1", "in_progress")[0] == 0
    rc, _out, err = move(capsys, "F2", "in_progress")
    assert rc == 0, err


def test_a_cut_dependency_does_not_block(home: Path, capsys) -> None:
    make_project()
    add_feature(title="Stripe integration")
    add_feature(title="Accounts")
    assert cli(capsys, "link", "F2", "--needs", "F1")[0] == 0
    assert set_field(capsys, "F1", "status=cut", "status_reason=Deferred")[0] == 0
    rc, _out, err = move(capsys, "F2", "in_progress")
    assert rc == 0, err


def test_override_reason_allows_the_move_and_is_recorded(home: Path, capsys) -> None:
    make_project()
    add_feature(title="Stripe integration")
    add_feature(title="Accounts")
    assert cli(capsys, "link", "F2", "--needs", "F1")[0] == 0
    rc, out, err = move(capsys, "F2", "in_progress",
                        override_reason="F1 lands tomorrow")
    assert rc == 0, err
    assert out == "F2 planned -> in_progress\n"
    last = history_rows(PROJECT, "feature", 2)[-1]
    assert last["change"] == "status planned -> in_progress"
    assert last["reason"] == "F1 lands tomorrow"


def test_override_reason_is_ignored_elsewhere(home: Path, capsys) -> None:
    make_project()
    add_requirement()
    add_feature()
    assert cli(capsys, "link", "R1", "F1")[0] == 0
    rc, _out, err = move(capsys, "R1", "approved", "rationale=Revenue",
                         override_reason="not a feature")
    assert rc == 0, err
    assert history_rows(PROJECT, "requirement", 1)[-1]["reason"] is None


def test_graph_critical_prints_build_order_needs_first(home: Path, capsys) -> None:
    """F2 needs F1 and F3 needs F2: the path reads F1 -> F2 -> F3 (what to build first)."""
    make_project()
    for title in ("Accounts", "Payments", "Checkout"):
        add_feature(title=title)
    assert cli(capsys, "link", "F2", "--needs", "F1")[0] == 0
    assert cli(capsys, "link", "F3", "--needs", "F2")[0] == 0
    rc, out, err = cli(capsys, "graph", "--critical")
    assert rc == 0, err
    assert out == "critical path (3): F1 -> F2 -> F3\n"
