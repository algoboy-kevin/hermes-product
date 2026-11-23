"""Derived planning values (card HP-6) — pure functions, no store, no sqlite.

Everything `trace` shows and `next` orders by is computed here so the rules are
testable on their own and never live in the CLI: progress, phase readiness,
evidence span, effective priority (the transitive pull of dependents) and the
ready/blocked queue with its deterministic order.  Definitions come from
S2 section 4: a feature is ready when it is planned or in_progress, not
blocked, and every mandatory dependency is already shipped.
"""
from __future__ import annotations

from typing import Iterable, Optional

PRIORITY_ORDER = ("must", "should", "could")            # best first
LAYER_ORDER = ("foundation", "core", "growth")          # build order
LEVEL_ORDER = ("none", "unit", "integration", "staging", "production")


def requirement_progress(criteria_statuses: Iterable[str]) -> tuple[int, int]:
    """(passed or waived, total) of one requirement's criteria."""
    statuses = list(criteria_statuses)
    done = sum(1 for status in statuses if status in ("passed", "waived"))
    return done, len(statuses)


def feature_progress(requirement_statuses: Iterable[str]) -> tuple[int, int]:
    """(done, total) of one feature's requirements."""
    statuses = list(requirement_statuses)
    done = sum(1 for status in statuses if status == "done")
    return done, len(statuses)


def phase_readiness(requirement_rows: Iterable[dict], phase: str) -> tuple[int, int]:
    """(started, total) of ``phase``'s requirements; dropped rows are excluded."""
    rows = [row for row in requirement_rows
            if row["phase"] == phase and row["status"] != "dropped"]
    started = sum(1 for row in rows if row["status"] in ("in_progress", "done"))
    return started, len(rows)


def evidence_span(levels: Iterable[Optional[str]]) -> Optional[tuple[str, str]]:
    """(strongest, weakest) evidence level over ``levels``; None when empty.

    Missing/None levels count as "none".  An unknown level name raises
    ValueError: the span must never invent an ordering.
    """
    names = ["none" if level is None else level for level in levels]
    for name in names:
        if name not in LEVEL_ORDER:
            raise ValueError(f"unknown evidence level {name!r}")
    if not names:
        return None
    ranks = sorted(LEVEL_ORDER.index(name) for name in names)
    return LEVEL_ORDER[ranks[-1]], LEVEL_ORDER[ranks[0]]


def effective_priority(features: Iterable[int],
                       requirement_priority_by_feature: dict,
                       needs: dict) -> dict:
    """The effective priority of each feature: its own best, or better.

    A feature with no non-dropped requirement is "could"; it inherits the best
    priority of the features that need it (their plumbing is as important as
    the most important dependent), transitively down the dependents.
    """
    ids = list(features)
    own = {fid: "could" for fid in ids}
    for fid in ids:
        ranks = [PRIORITY_ORDER.index(priority)
                 for priority, status in requirement_priority_by_feature.get(fid, ())
                 if status != "dropped" and priority in PRIORITY_ORDER]
        if ranks:
            own[fid] = PRIORITY_ORDER[min(ranks)]
    # needs: {feature_id: [needed ids]}; propagate down the dependents
    dependents = {fid: [] for fid in ids}
    for fid in ids:
        for needs_id in needs.get(fid, ()):
            if needs_id in dependents:
                dependents[needs_id].append(fid)
    effective = dict(own)
    changed = True
    while changed:                                    # transitive closure, best wins
        changed = False
        for fid in ids:
            for dependent in dependents[fid]:
                if PRIORITY_ORDER.index(effective[dependent]) < PRIORITY_ORDER.index(effective[fid]):
                    effective[fid] = effective[dependent]
                    changed = True
    return effective


def is_unplanned(row: dict, needed_by: Iterable[int] = ()) -> bool:
    """A candidate feature that advertises work it cannot show: no requirement, no spec,
    no code path, not marked ``infra`` and not needed by any other feature.

    A feature another one waits for is plumbing in the build order (it inherits that
    feature's priority), so it stays in the queue whether or not it is marked ``infra``.

    Such a row (a rollup or a placeholder) passes validation and used to top `next`;
    it is listed apart as ``unplanned`` instead, so the ready queue only holds work that
    is traceable to something (DOC5 section 2.7).
    """
    if row.get("infra") or row.get("id") in set(needed_by):
        return False
    links = row.get("links") or {}
    if links.get("requirements") or links.get("specs"):
        return False
    raw = row.get("paths")
    if raw not in (None, "", "[]"):
        return False
    return True


def ready_and_blocked(features: Iterable[dict], mandatory_needs: dict,
                      status_by_id: dict) -> tuple[list[dict], list[tuple[dict, str]]]:
    """Split candidate features into (ready, blocked); blocked pairs carry a reason.

    Only planned and in_progress features are candidates (shipped/cut appear in
    neither list).  A candidate is blocked when its blocked flag is set (reason:
    the stored blocked_reason) or when a mandatory dependency (optional=0) is
    not yet shipped (reason: `waiting on F<n> (<status>)`, listing all unmet).
    Optional dependencies and dependencies on cut features are ignored.

    Ready order: effective priority, then in_progress before planned, then
    layer (foundation, core, growth), then id.  Blocked uses the same order.
    """
    def order(row: dict) -> tuple:
        return (PRIORITY_ORDER.index(row.get("priority") or "could"),
                0 if row["status"] == "in_progress" else 1,
                LAYER_ORDER.index(row["layer"]),
                row["id"])

    candidates = [row for row in features if row["status"] in ("planned", "in_progress")]
    ready, blocked = [], []
    for row in sorted(candidates, key=order):
        if row.get("blocked"):
            blocked.append((row, row.get("blocked_reason") or "blocked"))
            continue
        unmet = []
        for needs_id, optional in mandatory_needs.get(row["id"], ()):
            if optional or status_by_id.get(needs_id) == "cut":
                continue
            if status_by_id.get(needs_id) != "shipped":
                unmet.append(f"F{needs_id} ({status_by_id.get(needs_id, 'unknown')})")
        if unmet:
            blocked.append((row, "waiting on " + ", ".join(unmet)))
        else:
            ready.append(row)
    return ready, blocked
