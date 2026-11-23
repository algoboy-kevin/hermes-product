"""Pure tests for ``graph.py`` (card HP-3). No store, no sqlite: data in, data out.

Pinned here: cycle detection (direct, chained, three-node, self loop, the
``new_edge`` variant, and a graph without a cycle), the critical path (simple,
branching, the smallest-ids tie-break, optional edges excluded by the caller,
empty graph) and the EXACT Mermaid text including a dashed optional edge and the
double-quote replacement.
"""

from __future__ import annotations

import graph


# ------------------------------------------------------------------ find_cycle
def test_find_cycle_empty_graph_is_none() -> None:
    assert graph.find_cycle([]) is None


def test_find_cycle_chain_without_a_cycle_is_none() -> None:
    assert graph.find_cycle([(1, 2), (2, 3), (1, 3)]) is None


def test_find_cycle_diamond_without_a_cycle_is_none() -> None:
    assert graph.find_cycle([(1, 2), (1, 3), (2, 4), (3, 4)]) is None


def test_find_cycle_self_loop() -> None:
    assert graph.find_cycle([(1, 1)]) == [1, 1]


def test_find_cycle_two_node_cycle() -> None:
    assert graph.find_cycle([(1, 2), (2, 1)]) == [1, 2, 1]


def test_find_cycle_three_node_cycle_returns_the_loop_start_to_start() -> None:
    assert graph.find_cycle([(1, 2), (2, 3), (3, 1)]) == [1, 2, 3, 1]


def test_find_cycle_new_edge_completes_a_cycle() -> None:
    assert graph.find_cycle([(1, 2)], new_edge=(2, 1)) == [1, 2, 1]


def test_find_cycle_new_edge_without_completing_is_none() -> None:
    assert graph.find_cycle([(1, 2)], new_edge=(2, 3)) is None


def test_find_cycle_new_edge_self_loop() -> None:
    assert graph.find_cycle([], new_edge=(5, 5)) == [5, 5]


def test_find_cycle_ignores_an_isolated_component() -> None:
    assert graph.find_cycle([(1, 2), (2, 1), (7, 8)]) == [1, 2, 1]


# -------------------------------------------------------------- critical_path
def test_critical_path_empty_graph_is_empty() -> None:
    assert graph.critical_path([], []) == []


def test_critical_path_single_node() -> None:
    assert graph.critical_path([1], []) == [1]


def test_critical_path_chain() -> None:
    assert graph.critical_path([1, 2, 3], [(1, 2), (2, 3)]) == [1, 2, 3]


def test_critical_path_branching_prefers_the_smallest_ids() -> None:
    assert graph.critical_path([1, 2, 3], [(1, 2), (1, 3)]) == [1, 2]


def test_critical_path_tie_break_compares_the_whole_sequence() -> None:
    nodes = [1, 2, 3, 4, 5]
    edges = [(1, 2), (2, 3), (1, 4), (4, 5)]
    assert graph.critical_path(nodes, edges) == [1, 2, 3]


def test_critical_path_picks_the_longest_even_when_a_smaller_id_starts_first() -> None:
    nodes = [1, 2, 3, 4, 5]
    edges = [(1, 2), (3, 4), (4, 5)]
    assert graph.critical_path(nodes, edges) == [3, 4, 5]


def test_critical_path_ignores_nodes_that_only_appear_in_edges() -> None:
    # Optional edges are excluded by the caller, so F9 is not a node at all.
    assert graph.critical_path([1, 2], [(1, 2)]) == [1, 2]


# --------------------------------------------------------------------- mermaid
def test_mermaid_exact_text() -> None:
    text = graph.mermaid({1: "Pay by card", 2: "Accounts"}, [(1, 2, 0)])
    assert text == (
        'graph LR\n'
        '  F1["F1 Pay by card"]\n'
        '  F2["F2 Accounts"]\n'
        '  F2 --> F1'
    )


def test_mermaid_nodes_follow_id_order_not_dict_order() -> None:
    text = graph.mermaid({3: "Reports", 1: "Stripe"}, [])
    assert text.splitlines()[1:] == ['  F1["F1 Stripe"]', '  F3["F3 Reports"]']


def test_mermaid_optional_edge_is_dashed() -> None:
    text = graph.mermaid({1: "Stripe", 2: "Accounts"}, [(1, 2, 1)])
    assert text.splitlines()[-1] == "  F2 -.-> F1"


def test_mermaid_edges_sort_by_needs_then_feature() -> None:
    text = graph.mermaid({1: "A", 2: "B", 3: "C"}, [(3, 1, 0), (2, 1, 0), (2, 3, 0)])
    assert text.splitlines()[4:] == ["  F1 --> F2", "  F1 --> F3", "  F3 --> F2"]


def test_mermaid_replaces_double_quotes_in_titles() -> None:
    text = graph.mermaid({1: 'Say "hi" now'}, [])
    assert text.splitlines()[1] == '  F1["F1 Say \'hi\' now"]'


def test_mermaid_empty_graph_is_just_the_header() -> None:
    assert graph.mermaid({}, []) == "graph LR"
