"""Lint: the S2 section 3 checks (cards HP-7 and HP-8).

Two halves, split so the rules stay testable without a database:

  * ``load_facts(con, project)`` reads everything the checks need — the entity
    rows with their links, the dependency edges, the doc links, one read-only
    pass over ``history``, the file facts (``doc_drift``) and the reference scan
    of the document bodies (``doc_problems``) — into a plain dict;
  * ``check(facts)`` is PURE: no sqlite, no store call, only the dict it is given
    and the pure ``graph``/``scan`` helpers, so a test builds facts by hand.

``run(project)`` is the two calls, in order, and returns the findings SORTED:
kind order R, A, F, S, DOC, then numeric id, then flag name. Same input, same
answer, every time (S1 section 9).

HP-8 shares the document half with ``scan-prd``: ``doc_text``, ``make_resolver``
and ``scan_project`` live here so neither module imports ``cli``.

``NOT_YET`` is the honest list of checks this build does not run yet; the CLI
prints it on every lint run.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Callable, NamedTuple, Optional

try:  # package submodule (Hermes plugin loader)
    from . import code_coverage, docs_text, graph, model, scan, store
except ImportError:  # top-level module (test suite)
    import code_coverage  # type: ignore[no-redef]
    import docs_text  # type: ignore[no-redef]
    import graph  # type: ignore[no-redef]
    import model  # type: ignore[no-redef]
    import scan  # type: ignore[no-redef]
    import store  # type: ignore[no-redef]


class Finding(NamedTuple):
    """One lint result: the flag, its severity, the entry it is about, the reason."""

    flag: str
    severity: str          # "error" | "warning" | "info"
    ref: str               # R3, A21, F7, S12, DOC4
    message: str


#: Checks S2 section 3 lists that this build does not run yet (printed by `lint`).
NOT_YET = ("uncovered_path",)

#: report order: R, A, F, S, DOC (S4's compact order)
KIND_ORDER = {"R": 0, "A": 1, "F": 2, "S": 3, "DOC": 4}

_REF_RE = re.compile(r"^([A-Z]+)(\d+)$")

_HISTORY_SQL = (
    "SELECT id, entity_type, entity_id, change, at FROM history"
    " WHERE project = ? ORDER BY id"
)

#: the kinds with a dependency table, and the display prefix used in messages
_DEPENDENCY_LABEL = {"feature": "F", "requirement": "R", "spec": "S"}

#: the states in which a requirement or feature has already asked for work (HP-26)
_WORK_REQUESTED = {
    "requirement": ("approved", "in_progress", "done"),
    "feature": ("planned", "in_progress", "shipped"),
}

#: the one project-level message telling a reader to put a card on the work
_NO_CARD_LINKS_MESSAGE = (
    'no kanban cards linked; put a "product: F# R#" line in each card body'
    " and run: product sync --board SLUG --apply")

#: a dependent in one of these states can be at risk from its dependency
_AT_RISK_STATES = {
    "feature": ("planned", "in_progress"),
    "requirement": ("draft", "approved", "in_progress"),
}

#: the state that retires a dependency (a cut feature, a dropped requirement)
_RETIRED_WORD = {"feature": "cut", "requirement": "dropped"}


def _sort_key(finding: Finding) -> tuple:
    """Kind order, then numeric id, then flag name — stable and deterministic.

    A ``project`` ref (the coverage infos) carries no id: it sorts after every
    R/A/F/S/DOC finding, then by message.
    """
    if finding.ref == "project":
        return (len(KIND_ORDER), 0, finding.flag, finding.message)
    match = _REF_RE.match(finding.ref)
    if match is None:                                  # defensive: never expected
        return (len(KIND_ORDER), 0, finding.flag)
    return (KIND_ORDER.get(match.group(1), len(KIND_ORDER)), int(match.group(2)),
            finding.flag)


# ------------------------------------------------------- references (HP-8)
def doc_text(project: str, row: dict) -> Optional[str]:
    """A DOC's body text, or None when it cannot be read.

    Same rules as ``cli._doc_text`` (which keeps raising for ``doc read``): the
    stored body file wins; else a LOCAL ``body_ref``, relative to the project root,
    else the working directory. A URL, a missing path or an unreadable file is
    None, so every caller can skip the document silently.
    """
    try:
        if row.get("body_file"):
            path = store.doc_body_path(project, row)
            if not path.is_file():
                return None
            return path.read_text(encoding="utf-8")
        body_ref = str(row.get("body_ref") or "").strip()
        if not body_ref or body_ref.startswith(("http://", "https://")):
            return None
        path = Path(body_ref).expanduser()
        if not path.is_absolute():
            project_row = store.get_project(project) or {}
            base = Path(project_row["root"]) if project_row.get("root") else Path.cwd()
            path = base / path
        if not path.is_file():
            return None
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def make_resolver(con, project: str) -> Callable[[str], str]:
    """Map a reference text to ``"ok"`` | ``"unknown"`` | ``"retired"``.

    A missing row — or a reference that does not parse — is ``"unknown"``. A row
    in its kind's ``RETIRED_STATUS`` is ``"retired"`` (dropped R, cut F, superseded
    S or DOC); a kind without a retired status never answers ``"retired"``. This
    never raises: a bad reference is data, not a crash.
    """
    def resolve(text: str) -> str:
        try:
            kind, identifier = model.parse_ref(text)
        except model.ValidationError:
            return "unknown"
        if isinstance(identifier, str):                   # F:slug
            row = con.execute(
                "SELECT status FROM feature WHERE project = ? AND slug = ?",
                (project, identifier),
            ).fetchone()
        else:
            row = con.execute(
                f"SELECT status FROM {store.ENTITY_TABLE[kind]} WHERE project = ? AND id = ?",
                (project, identifier),
            ).fetchone()
        if row is None:
            return "unknown"
        retired = store.RETIRED_STATUS.get(kind)
        if retired is not None and row["status"] == retired:
            return "retired"
        return "ok"
    return resolve


def scannable_docs(con, project: str) -> list[dict]:
    """The DOCs whose bodies are scanned: every ``current`` DOC, in id order.

    A superseded document is retired, and S2 keeps retired entries out of the
    checks, so a superseded DOC — a PRD included — contributes no problems. That
    is also what the brief's test list asks for ("a superseded PRD too").
    """
    return [row for row in store.list_entities(project, "doc", con=con)
            if row["status"] == "current"]


def scan_project(project: str, con=None) -> dict:
    """Scan the scannable DOC bodies: document/reference counts and the problems.

    ``problems`` holds ``(doc_ref, Problem)`` pairs ordered by document id, then
    line, then column — the order ``scan-prd`` prints.
    """
    own = con is None
    con = con or store.connect()
    try:
        resolver = make_resolver(con, project)
        documents = 0
        references = 0
        problems: list[tuple[str, scan.Problem]] = []
        for doc_row in scannable_docs(con, project):
            text = doc_text(project, doc_row)
            if text is None:                                # URL, missing, unreadable
                continue
            documents += 1
            refs = scan.find_references(text)
            references += len(refs)
            doc_ref = f"DOC{doc_row['id']}"
            problems.extend((doc_ref, problem)
                            for problem in scan.check_references(refs, resolver))
        for ref, text in entity_bodies(con, project):
            documents += 1
            refs = scan.find_references(text)
            references += len(refs)
            problems.extend((ref, problem) for problem in scan.check_references(refs, resolver))
    finally:
        if own:
            con.close()
    problems.sort(key=lambda pair: (KIND_ORDER.get(_REF_RE.match(pair[0]).group(1), 9),
                                    int(_REF_RE.match(pair[0]).group(2)),
                                    pair[1].line, pair[1].col))
    return {"documents": documents, "references": references, "problems": problems}


# ------------------------------------------------------------------- facts (read)
def _paths_list(value: object) -> list[str]:
    """A stored ``paths`` value as a list of strings.

    The store keeps ``paths`` as a JSON list text (``store.create_entity`` dumps
    it); ``cli`` and ``export`` read it back the same way. Anything unparsable is
    one path, and an empty value is no claim at all.
    """
    if value is None or value == "":
        return []
    if isinstance(value, (list, tuple)):
        items = list(value)
    else:
        try:
            items = json.loads(value)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            items = [value]
        if not isinstance(items, list):
            items = [items]
    return [str(item) for item in items if str(item).strip()]


def _coverage_entries(facts: dict) -> dict[str, list[str]]:
    """Every live entry with a non-empty ``paths`` list, as ``{"F3": [...]}``.

    A retired entry (a ``cut`` feature, a ``superseded`` spec) makes no claim: its
    paths no longer describe work this build intends to do.
    """
    entries: dict[str, list[str]] = {}
    for bucket, prefix, retired in (("features", "F", "cut"), ("specs", "S", "superseded")):
        for row in facts[bucket]:
            if row.get("status") == retired:
                continue
            paths = _paths_list(row.get("paths"))
            if paths:
                entries[f"{prefix}{row['id']}"] = paths
    return entries


#: the kinds that carry a row body, with the state in which an entry is retired (S2 keeps
#: retired entries out of the checks)
_BODY_TABLES = (("requirement", "R", "dropped"), ("feature", "F", "cut"),
                ("spec", "S", "superseded"))


def entity_bodies(con, project: str) -> list[tuple[str, str]]:
    """``[(ref, text), ...]`` of every live R#/F#/S# that has a body, kind then id order."""
    found: list[tuple[str, str]] = []
    for table, prefix, retired in _BODY_TABLES:
        for row in con.execute(
                f"SELECT id, status, body FROM {table} WHERE project = ? AND body IS NOT NULL"
                " ORDER BY id", (project,)):
            if row["status"] != retired and row["body"].strip():
                found.append((f"{prefix}{row['id']}", row["body"]))
    return found


def load_facts(con, project: str, *, coverage: bool = False) -> dict:
    """Everything ``check`` needs, read-only from the store (and the body files).

    ``doc_drift`` is a FILE fact, not a store fact: the stored ``body_hash`` is
    compared with the hash of the ``body_ref`` file as it is now. A URL or a
    missing file has no hash (``store._body_hash`` returns None) and never drifts.

    ``doc_problems`` is the reference scan of the scannable documents, so the
    ``broken_reference`` check needs no file read of its own and ``check`` stays
    pure: a list of ``(doc_ref, scan.Problem)``.

    ``coverage=True`` adds one more fact, ``coverage``: the ``code_coverage``
    report for the project's root, with every live F#/S# ``paths`` claim as an
    entry. It needs a real root directory, so a project without one raises.

    ``kanban_links`` is the count of ``external_link`` rows whose system is
    ``kanban``: the only rows that join a store entry to a card (HP-26).
    """
    items = store.list_entities(project, con=con)      # R, F, S, A, DOC with links
    facts: dict[str, Any] = {
        "requirements": [],
        "criteria": [],
        "features": [],
        "specs": [],
        "docs": [],
        "deps": {kind: store.dependency_pairs(con, project, kind)
                 for kind in _DEPENDENCY_LABEL},
        "history": [dict(row) for row in con.execute(_HISTORY_SQL, (project,))],
        "doc_problems": [],
        "doc_drift": [],
        "bodies": [],                 # (ref, text) of every live body: DOC files, R#/F#/S# rows
        "kanban_links": con.execute(
            "SELECT COUNT(*) AS n FROM external_link WHERE project = ? AND system = 'kanban'",
            (project,)).fetchone()["n"],
    }
    buckets = {
        "requirement": "requirements",
        "criterion": "criteria",
        "feature": "features",
        "spec": "specs",
        "doc": "docs",
    }
    for item in items:
        facts[buckets[item["kind"]]].append(item)
    resolver = make_resolver(con, project)
    for doc_row in facts["docs"]:
        body_ref = doc_row.get("body_ref")
        stored_hash = doc_row.get("body_hash")
        if body_ref and stored_hash:
            current = store._body_hash(body_ref)
            if current is not None and current != stored_hash:
                facts["doc_drift"].append(doc_row["id"])
        if doc_row["status"] != "current":
            continue
        text = doc_text(project, doc_row)
        if text is None:
            continue
        doc_ref = f"DOC{doc_row['id']}"
        facts["bodies"].append((doc_ref, text))
        facts["doc_problems"].extend(
            (doc_ref, problem) for problem in scan.scan_text(text, resolver))
    for ref, text in entity_bodies(con, project):
        facts["bodies"].append((ref, text))
        facts["doc_problems"].extend(
            (ref, problem) for problem in scan.scan_text(text, resolver))
    if coverage:
        project_row = store.get_project(project, con=con) or {}
        root = project_row.get("root")
        if not root or not Path(root).is_dir():
            raise store.StoreError(
                "--coverage needs the project's root directory"
                f" (project add --root DIR); {project} has root {root or 'none'}"
            )
        facts["coverage"] = code_coverage.coverage_report(
            code_coverage.walk_repo(root), _coverage_entries(facts), max_items=50)
    return facts


# -------------------------------------------------------------- checks (pure)
def check(facts: dict) -> list[Finding]:
    """Every S2 section 3 finding for one facts dict, sorted (PURE)."""
    findings: list[Finding] = []
    requirements = facts.get("requirements") or []
    criteria = facts.get("criteria") or []
    features = facts.get("features") or []
    specs = facts.get("specs") or []
    docs = facts.get("docs") or []
    deps = facts.get("deps") or {}
    history = facts.get("history") or []

    criteria_by_requirement: dict[int, list[dict]] = {}
    for criterion in criteria:
        criteria_by_requirement.setdefault(criterion["requirement_id"], []).append(criterion)
    requirements_by_id = {row["id"]: row for row in requirements}
    features_by_id = {row["id"]: row for row in features}

    for requirement in requirements:
        findings += _requirement_findings(requirement,
                                          criteria_by_requirement.get(requirement["id"], []))
    for spec in specs:
        findings += _spec_findings(spec, history)
        findings += _spec_behind_findings(spec, features_by_id, requirements_by_id)
    for feature in features:
        findings += _feature_findings(feature)
    findings += _at_risk_findings(requirements, features, deps,
                                 requirements_by_id, features_by_id)
    findings += _cycle_findings(deps)
    findings += _cut_findings(features, requirements_by_id)
    drifted = set(facts.get("doc_drift") or ())
    for doc_row in docs:
        findings += _doc_findings(doc_row, drifted)
    for doc_ref, problem in facts.get("doc_problems") or []:
        rendered = scan.format_problem("", problem)[1:]        # drop the leading colon
        findings.append(Finding("broken_reference", "error", doc_ref, f"line {rendered}"))
    findings += _coverage_findings(facts.get("coverage"))
    findings += _card_link_findings(facts)
    for spec in specs:
        findings += _thin_findings(f"S{spec['id']}", spec, "summary", spec["status"] == "superseded")
    for doc_row in docs:
        findings += _thin_findings(f"DOC{doc_row['id']}", doc_row, "summary",
                                   doc_row["status"] == "superseded")
    for feature in features:
        findings += _thin_findings(f"F{feature['id']}", feature, "description",
                                   feature["status"] == "cut")
    for ref, text in facts.get("bodies") or []:
        for number, line in docs_text.status_lines(text):
            findings.append(Finding(
                "status_in_body", "warning", ref,
                f"line {number}: {_clip(line)!r} states a status in text; state lives in the"
                " row's status field"))
    return sorted(findings, key=_sort_key)


_PLACEHOLDER_RE = re.compile(r"^(imported|migrated)\b|\(legacy\)|\blegacy import\b",
                             re.IGNORECASE)
_PATHISH_RE = re.compile(r"^\S+\.(md|go|py|ya?ml|txt|rst|json|toml)$|^[\w.-]+(/[\w.-]+)+/?$",
                         re.IGNORECASE)
#: fewer words than this cannot say what an entry decides
MIN_SUMMARY_WORDS = 4


def thin_reason(title: Any, text: Any) -> Optional[str]:
    """Why a one-line summary/description says nothing, or None when it is substantive.

    Thin means it restates the title, is an import placeholder (``Imported spec
    document X (legacy)``), is only a file path, or is too short to decide anything
    (DOC5 section 2.2: a hollow row that passes validation looks the same as a
    migrated one).
    """
    flat = " ".join(str(text or "").split())
    if not flat:
        return None                                    # empty is the field rule's business
    if flat.casefold().rstrip(".") == " ".join(str(title or "").split()).casefold().rstrip("."):
        return "restates the title"
    if _PLACEHOLDER_RE.search(flat):
        return "is an import placeholder"
    if _PATHISH_RE.match(flat):
        return "is only a file path"
    if len(flat.split()) < MIN_SUMMARY_WORDS:
        return f"is {len(flat.split())} word(s); say what it decides"
    return None


def _thin_findings(ref: str, row: dict, field: str, retired: bool) -> list[Finding]:
    if retired:
        return []
    reason = thin_reason(row.get("title"), row.get(field))
    if reason is None:
        return []
    return [Finding("thin_summary", "warning", ref, f"{field} {reason}")]


def _clip(text: str, width: int = 60) -> str:
    return text if len(text) <= width else text[: width - 1] + "\u2026"


def _coverage_findings(report: Optional[dict]) -> list[Finding]:
    """uncovered_path infos for one ``code_coverage`` report (PURE; None yields none).

    Listed items are already bounded by the report's ``max_items``; each list the
    report truncated gets one extra info naming how many it hid.
    """
    if not report:
        return []
    findings: list[Finding] = []
    for path in report.get("uncovered_packages") or ():
        findings.append(Finding("uncovered_path", "info", "project",
                                f"package {path}: no F#/S# paths entry covers it"))
    for path in report.get("uncovered_specs") or ():
        findings.append(Finding("uncovered_path", "info", "project",
                                f"spec {path}: no F#/S# paths entry covers it"))
    truncated = report.get("truncated") or {}
    for key, label in (("uncovered_packages", "packages"), ("uncovered_specs", "specs")):
        hidden = int(truncated.get(key, 0) or 0)
        if hidden > 0:
            findings.append(Finding("uncovered_path", "info", "project",
                                    f"and {hidden} more uncovered {label}"))
    return findings


def _card_link_findings(facts: dict) -> list[Finding]:
    """no_card_links: work was asked for and no kanban card is linked (PURE, HP-26).

    One project-level Info. It fires only when the store says the questions were
    asked — an ``approved``/``in_progress``/``done`` R# or a ``planned`` or later
    F#, with ``cut`` never counting — and no ``external_link`` row has system
    ``kanban``. A facts dict without the ``kanban_links`` key means unknown (an
    older caller, a hand-built dict): no finding, never an assumption of zero.
    """
    count = facts.get("kanban_links")
    if count is None or int(count) > 0:
        return []
    if not any(row.get("status") in _WORK_REQUESTED[kind]
               for kind, bucket in (("requirement", "requirements"), ("feature", "features"))
               for row in facts.get(bucket) or ()):
        return []
    return [Finding("no_card_links", "info", "project", _NO_CARD_LINKS_MESSAGE)]


def _requirement_findings(requirement: dict, own_criteria: list[dict]) -> list[Finding]:
    """orphan_requirement, unspecified, no_criteria, unverified_done for one R#."""
    entity_id = requirement["id"]
    status = requirement["status"]
    links = requirement.get("links") or {}
    ref = f"R{entity_id}"
    findings: list[Finding] = []
    if status in ("approved", "in_progress", "done") and not links.get("features"):
        findings.append(Finding("orphan_requirement", "warning", ref,
                                f"{status} with no linked feature"))
    if status == "in_progress" and not links.get("specs"):
        findings.append(Finding("unspecified", "warning", ref, "in_progress with no linked spec"))
    if status in ("in_progress", "done") and not own_criteria:
        severity = "warning" if requirement.get("legacy") else "error"
        findings.append(Finding("no_criteria", severity, ref, f"{status} with no criterion"))
    if status == "done":
        unfinished = next((criterion for criterion in own_criteria
                           if criterion["status"] not in ("passed", "waived")), None)
        if unfinished is not None:
            findings.append(Finding(
                "unverified_done", "error", ref,
                f"done but A{unfinished['id']} is {unfinished['status']}",
            ))
    return findings


def _spec_findings(spec: dict, history: list[dict]) -> list[Finding]:
    """orphan_spec and stale_spec for one S#."""
    ref = f"S{spec['id']}"
    status = spec["status"]
    linked = (spec.get("links") or {}).get("requirements") or []
    findings: list[Finding] = []
    if status == "approved" and not linked:
        # S2 says "Error (Warning if legacy)", but only requirement carries `legacy`.
        findings.append(Finding("orphan_spec", "error", ref,
                                "approved with no linked requirement"))
    if status in ("approved", "implemented", "verified"):
        approved_at = _approval_row_id(history, spec["id"])
        if approved_at is not None:
            changed_after = sorted(requirement_id for requirement_id in linked
                                   if _statement_change_id(history, requirement_id) > approved_at)
            if changed_after:
                findings.append(Finding(
                    "stale_spec", "warning", ref,
                    f"R{changed_after[0]} statement changed after approval",
                ))
    return findings


#: the spec states that should already have caught up with shipped or done work
_SPEC_OPEN_STATES = ("draft", "approved")


def _spec_behind_findings(spec: dict, features_by_id: dict,
                          requirements_by_id: dict) -> list[Finding]:
    """spec_behind: a ``draft``/``approved`` S# whose own work already finished (PURE).

    A linked feature ``shipped`` or a linked requirement ``done`` is work the spec
    still presents as open — the spec is behind it. The first piece of evidence
    wins, and features are checked before requirements, so a spec linked to both
    names the feature. A retired row (``cut`` feature, ``dropped`` requirement) is
    never evidence, because it is not one of the two words above.
    """
    status = spec["status"]
    if status not in _SPEC_OPEN_STATES:
        return []
    ref = f"S{spec['id']}"
    links = spec.get("links") or {}
    for feature_id in links.get("features") or ():
        feature = features_by_id.get(feature_id)
        if feature is not None and feature["status"] == "shipped":
            return [Finding("spec_behind", "warning", ref,
                            f"{ref} {status} but F{feature_id} shipped")]
    for requirement_id in links.get("requirements") or ():
        requirement = requirements_by_id.get(requirement_id)
        if requirement is not None and requirement["status"] == "done":
            return [Finding("spec_behind", "warning", ref,
                            f"{ref} {status} but R{requirement_id} done")]
    return []


def _approval_row_id(history: list[dict], spec_id: int) -> Optional[int]:
    """The highest history row id of this spec whose change ends with ``-> approved``."""
    ids = [row["id"] for row in history
           if row["entity_type"] == "spec" and row["entity_id"] == spec_id
           and str(row.get("change") or "").endswith("-> approved")]
    return max(ids) if ids else None


def _statement_change_id(history: list[dict], requirement_id: int) -> int:
    """The highest history row id of a ``statement changed`` for one requirement (0: none)."""
    ids = [row["id"] for row in history
           if row["entity_type"] == "requirement" and row["entity_id"] == requirement_id
           and row.get("change") == "statement changed"]
    return max(ids) if ids else 0


def _feature_findings(feature: dict) -> list[Finding]:
    """feature_without_requirement for one F#."""
    status = feature["status"]
    linked = (feature.get("links") or {}).get("requirements") or []
    if (status in ("planned", "in_progress", "shipped") and not linked
            and not feature.get("infra")):
        return [Finding("feature_without_requirement", "warning", f"F{feature['id']}",
                        f"{status} with no requirement")]
    return []


def _at_risk_findings(requirements: list[dict], features: list[dict], deps: dict,
                      requirements_by_id: dict, features_by_id: dict) -> list[Finding]:
    """One at_risk per dependent whose dependency is blocked or retired.

    Feature edges only count when mandatory (optional == 0); requirement edges all
    count. The message names the first offending dependency (lowest id) and why.
    """
    findings: list[Finding] = []
    for kind, rows in (("feature", features), ("requirement", requirements)):
        label = _DEPENDENCY_LABEL[kind]
        by_id = features_by_id if kind == "feature" else requirements_by_id
        retired_word = _RETIRED_WORD[kind]
        for subject in rows:
            if subject["status"] not in _AT_RISK_STATES[kind]:
                continue
            why = _at_risk_reason(kind, subject["id"], deps.get(kind) or (), by_id, retired_word)
            if why is not None:
                findings.append(Finding("at_risk", "warning", f"{label}{subject['id']}", why))
    return findings


def _at_risk_reason(kind: str, entity_id: int, edges, by_id: dict,
                    retired_word: str) -> Optional[str]:
    """``F2 is cut`` style reason for the first bad dependency, or None."""
    label = _DEPENDENCY_LABEL[kind]
    for row_id, needs_id, optional in edges:
        if row_id != entity_id:
            continue
        if kind == "feature" and optional:
            continue
        dependency = by_id.get(needs_id)
        if dependency is None:
            continue
        dependency_ref = f"{label}{needs_id}"
        if dependency.get("blocked"):
            return f"{dependency_ref} is blocked"
        if dependency["status"] == retired_word:
            return f"{dependency_ref} is {retired_word}"
    return None


def _cycle_findings(deps: dict) -> list[Finding]:
    """One dependency_cycle Error per kind whose edges contain a cycle."""
    findings: list[Finding] = []
    for kind, label in (("feature", "F"), ("requirement", "R"), ("spec", "S")):
        edges = [(row_id, needs_id) for row_id, needs_id, _optional in deps.get(kind) or []]
        cycle = graph.find_cycle(edges)
        if cycle is None:
            continue
        path = " -> ".join(f"{label}{value}" for value in cycle)
        findings.append(Finding("dependency_cycle", "error", f"{label}{cycle[0]}", path))
    return findings


def _cut_findings(features: list[dict], requirements_by_id: dict) -> list[Finding]:
    """cut_with_open_requirements: a cut feature that still leaves an open R# homeless."""
    findings: list[Finding] = []
    for feature in features:
        if feature["status"] != "cut":
            continue
        homeless: list[int] = []
        for requirement_id in (feature.get("links") or {}).get("requirements") or []:
            requirement = requirements_by_id.get(requirement_id)
            if requirement is None or requirement["status"] == "dropped":
                continue
            if _linked_to_a_live_feature(features, feature["id"], requirement_id):
                continue
            homeless.append(requirement_id)
        if homeless:
            named = ", ".join(f"R{value}" for value in sorted(homeless))
            findings.append(Finding("cut_with_open_requirements", "error",
                                    f"F{feature['id']}", f"cut with open requirements {named}"))
    return findings


def _linked_to_a_live_feature(features: list[dict], cut_id: int, requirement_id: int) -> bool:
    """Is ``requirement_id`` linked to a feature other than the cut one and not cut?"""
    for other in features:
        if other["id"] == cut_id or other["status"] == "cut":
            continue
        if requirement_id in ((other.get("links") or {}).get("requirements") or []):
            return True
    return False


def _doc_findings(doc_row: dict, drifted: set) -> list[Finding]:
    """orphan_doc and doc_drift for one DOC#.

    The PRD is exempt from orphan_doc: it is the document the project is built around, not a
    supporting note that has to justify itself by linking something. Seeding it at project
    creation would otherwise put a warning on a project that has done nothing wrong yet.
    """
    ref = f"DOC{doc_row['id']}"
    findings: list[Finding] = []
    is_prd = bool(int(doc_row.get("is_prd") or 0)) or doc_row.get("kind") == "prd"
    if doc_row["status"] == "current" and not is_prd and not (doc_row.get("doc_links") or []):
        findings.append(Finding("orphan_doc", "warning", ref, "current with no link"))
    if doc_row["id"] in drifted:
        findings.append(Finding("doc_drift", "warning", ref,
                                "body hash differs from the stored hash"))
    return findings


# ------------------------------------------------------------------------- run
def run(project: str, con=None, *, coverage: bool = False) -> list[Finding]:
    """``check(load_facts(...))`` for one project; opens its own connection if needed."""
    own = con is None
    con = con or store.connect()
    try:
        facts = load_facts(con, project, coverage=coverage)
    finally:
        if own:
            con.close()
    return check(facts)
