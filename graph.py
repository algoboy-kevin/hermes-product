"""The feature graph, pure (card HP-3).

No sqlite, no store import: ``store`` loads the feature rows and dependency edges
and hands them here as plain tuples, so every rule below is testable without a
database.

Vocabulary used throughout:

  * an edge is ``(feature_id, needs_id)`` — the feature NEEDS the other one;
  * a cycle is returned start-to-start, e.g. ``[1, 2, 1]``;
  * the critical path follows the same direction: ``[1, 2, 3]`` means F1 needs
    F2 needs F3, and it is the longest chain of MANDATORY edges.

Determinism is part of the contract (S1 section 9's "same input, same answer"):
nodes and neighbours are visited in id order and ties are broken by the smallest
id sequence, so the same graph always prints the same Mermaid and the same path.
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping, Optional, Sequence, Tuple

Edge = Tuple[int, int]


# ---------------------------------------------------------------------- cycles
def find_cycle(edges: Iterable[Edge], new_edge: Optional[Edge] = None) -> Optional[list[int]]:
    """The first dependency cycle in ``edges`` (plus ``new_edge``), or None.

    Depth-first with three colours, the method borrowed from the feature-graph
    plugin's ``dependency_cycles``: a grey neighbour is a back edge, and the loop
    is the grey part of the stack followed by the node it points at.

    ``new_edge`` lets the caller ask "would adding this edge close a loop?"
    without mutating the stored edges (link's duplicate/cycle check).
    """
    adjacency: dict[int, set[int]] = {}
    all_edges = list(edges)
    if new_edge is not None:
        all_edges.append(new_edge)
    for feature_id, needs_id in all_edges:
        adjacency.setdefault(int(feature_id), set()).add(int(needs_id))
        adjacency.setdefault(int(needs_id), set())

    WHITE, GREY, BLACK = 0, 1, 2
    colour = {node: WHITE for node in adjacency}
    stack: list[int] = []

    def visit(node: int) -> Optional[list[int]]:
        colour[node] = GREY
        stack.append(node)
        for neighbour in sorted(adjacency[node]):
            if colour[neighbour] == GREY:
                return stack[stack.index(neighbour):] + [neighbour]
            if colour[neighbour] == WHITE:
                found = visit(neighbour)
                if found is not None:
                    return found
        stack.pop()
        colour[node] = BLACK
        return None

    for node in sorted(adjacency):
        if colour[node] == WHITE:
            found = visit(node)
            if found is not None:
                return found
    return None


# --------------------------------------------------------------- critical path
def critical_path(nodes: Iterable[int], edges: Iterable[Edge]) -> list[int]:
    """The longest chain of mandatory edges, as a node list; ``[]`` when empty.

    ``nodes`` is the exported node set (non-cut features) and ``edges`` must
    already contain only mandatory ones — the caller owns the ``optional`` flag.
    A tie on length is broken by the lexicographically smallest id sequence, so
    the output does not depend on dict or row order.
    """
    node_ids = sorted({int(node) for node in nodes})
    successors: dict[int, list[int]] = {node: [] for node in node_ids}
    for feature_id, needs_id in edges:
        if feature_id in successors and needs_id in successors:
            successors[feature_id].append(int(needs_id))
    for node in successors:
        successors[node] = sorted(set(successors[node]))

    memo: dict[int, list[int]] = {}

    def longest_from(node: int, visiting: set[int]) -> list[int]:
        if node in memo:
            return memo[node]
        if node in visiting:                       # defensive: never loop forever
            return []
        visiting.add(node)
        best = [node]
        for neighbour in successors[node]:
            candidate = [node, *longest_from(neighbour, visiting)]
            if len(candidate) > len(best) or (len(candidate) == len(best) and candidate < best):
                best = candidate
        visiting.discard(node)
        memo[node] = best
        return best

    answer: list[int] = []
    for node in node_ids:
        candidate = longest_from(node, set())
        if len(candidate) > len(answer) or (len(candidate) == len(answer) and candidate < answer):
            answer = candidate
    return answer


# ---------------------------------------------------------------------- mermaid
def mermaid(nodes: Mapping[int, Any], edges: Sequence[Tuple[int, int, Any]]) -> str:
    """The EXACT Mermaid text for one project (S4: ``graph`` prints this).

    Node lines come in id order; edge lines in ``(needs_id, feature_id)`` order.
    A title's double quotes become single quotes so the Mermaid label survives.
    """
    lines = ["graph LR"]
    for feature_id in sorted(nodes):
        title = str(nodes[feature_id] or "").replace('"', "'")
        lines.append(f'  F{feature_id}["F{feature_id} {title}"]')
    for feature_id, needs_id, optional in sorted(edges, key=lambda edge: (edge[1], edge[0])):
        arrow = "-.->" if optional else "-->"
        lines.append(f"  F{needs_id} {arrow} F{feature_id}")
    return "\n".join(lines)
