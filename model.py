"""Pure domain model for the ``product`` plugin (card HP-2).

Everything here is data and pure functions: no sqlite import, no connection, no
store. ``store`` loads the facts (counts and statuses of the linked rows) and
calls these functions; ``cli`` prints ``ValidationError`` messages verbatim.

Three rules keep this module honest:

  * entity type spellings are the HISTORY spellings (S1 section 9):
    ``requirement|criterion|feature|spec|doc``; the display prefixes are R/A/F/S/DOC;
  * a reference is parsed strictly (uppercase prefix + digits, or ``F:slug`` for a
    feature slug) — a lower-case or decorated spellings is an error, never a fixup;
  * validation names the field or rule so the caller can fix it in one step.
"""

from __future__ import annotations

import re
from typing import Any, Optional

# --------------------------------------------------------------------- vocabulary
KINDS = ("requirement", "criterion", "feature", "spec", "doc")

#: display prefix per entity type
PREFIX = {"requirement": "R", "criterion": "A", "feature": "F", "spec": "S", "doc": "DOC"}
KIND_BY_PREFIX = {prefix: kind for kind, prefix in PREFIX.items()}

#: the kinds a DOC# may declare (S1 section 7); exactly one `prd` per project
DOC_KINDS = ("prd", "spec", "context", "research", "design", "api", "runbook", "report",
             "other")

#: a document summary is what an agent sees by default: one or two sentences
DOC_SUMMARY_MAX = 400

#: the kinds whose row carries a `body` column: the full text that DEFINES the entry
#: (S1 section 3.1). A# has none (one sentence plus evidence); DOC# keeps its own body file.
BODY_KINDS = ("requirement", "feature", "spec")

STATUSES: dict[str, tuple[str, ...]] = {
    "requirement": ("draft", "approved", "in_progress", "done", "dropped"),
    "criterion": ("not_run", "passed", "failed", "waived"),
    "feature": ("planned", "in_progress", "shipped", "cut"),
    "spec": ("draft", "approved", "implemented", "verified", "superseded"),
    "doc": ("current", "superseded"),
}

INITIAL_STATUS = {
    "requirement": "draft",
    "criterion": "not_run",
    "feature": "planned",
    "spec": "draft",
    "doc": "current",
}

#: the forward chain of a kind; backward moves are allowed within it
CHAIN: dict[str, tuple[str, ...]] = {
    "requirement": ("draft", "approved", "in_progress", "done"),
    "feature": ("planned", "in_progress", "shipped"),
    "spec": ("draft", "approved", "implemented", "verified"),
    "doc": ("current",),
}

#: states reachable from anywhere and never left
TERMINAL = {"requirement": ("dropped",), "feature": ("cut",), "spec": ("superseded",),
            "doc": ("superseded",), "criterion": ()}


def _transitions(kind: str) -> dict[str, tuple[str, ...]]:
    """Allowed target states per state: one forward step, any backward step, terminals."""
    statuses = STATUSES[kind]
    chain = CHAIN.get(kind)
    terminal = TERMINAL.get(kind, ())
    moves: dict[str, tuple[str, ...]] = {}
    if chain is None:                       # criterion: every move is legal
        return {state: tuple(other for other in statuses if other != state) for state in statuses}
    for index, state in enumerate(chain):
        targets = list(chain[:index])                      # backward: any earlier state
        if index + 1 < len(chain):
            targets.append(chain[index + 1])               # forward: exactly one step
        targets.extend(terminal)                           # terminal from anywhere
        moves[state] = tuple(targets)
    for state in terminal:
        moves[state] = ()                                  # a terminal state is never left
    return moves


TRANSITIONS: dict[str, dict[str, tuple[str, ...]]] = {kind: _transitions(kind) for kind in KINDS}

# Requirement statuses where a statement change must carry a reason (S2 section 1).
APPROVED_OR_LATER = {"approved", "in_progress", "done"}


class ValidationError(Exception):
    """A field or rule refused the write; the message names it."""


# ----------------------------------------------------------------------- references
REF_RE = re.compile(r"^(R|A|F|S|DOC)(\d+)$")
FEATURE_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")
FEATURE_SLUG_RULE = FEATURE_SLUG_RE.pattern


def kind_for(value: str) -> str:
    """``R`` -> ``requirement``; an already-spelled kind passes through."""
    if value in PREFIX:
        return value
    if value in KIND_BY_PREFIX:
        return KIND_BY_PREFIX[value]
    raise ValidationError(
        f"unknown entity type {value!r}: expected one of {'/'.join(PREFIX)}"
        f" or {'/'.join(KINDS)}"
    )


def parse_ref(text: str) -> tuple[str, Any]:
    """``R3`` -> ``("requirement", 3)``; ``F:stripe`` -> ``("feature", "stripe")``.

    Strict: uppercase prefix, digits only, no spaces or decoration.
    """
    if not isinstance(text, str):
        raise ValidationError(
            f"invalid reference {text!r}: expected R3, A21, F7, S2, DOC1 or F:slug"
        )
    candidate = text.strip()
    if candidate.startswith("F:"):
        slug = candidate[2:]
        if not slug:
            raise ValidationError(f"invalid reference {text!r}: F: needs a feature slug")
        return "feature", slug
    match = REF_RE.match(candidate)
    if not match:
        raise ValidationError(
            f"invalid reference {text!r}: expected R3, A21, F7, S2, DOC1 or F:slug"
        )
    return KIND_BY_PREFIX[match.group(1)], int(match.group(2))


def format_ref(kind: str, value: Any) -> str:
    """``("requirement", 3)`` -> ``R3``; ``("feature", "stripe")`` -> ``F:stripe``."""
    if kind == "feature" and isinstance(value, str):
        return f"F:{value}"
    return f"{PREFIX[kind]}{value}"


# ---------------------------------------------------------------------- field rules
_ENUM = "enum"

FIELD_RULES: dict[str, dict[str, dict]] = {
    "requirement": {
        "title": {"type": "title"},
        "statement": {"type": "required_text"},
        "rationale": {"type": "text"},
        "priority": {"type": _ENUM, "values": ("must", "should", "could")},
        "phase": {"type": "text"},
        "status": {"type": _ENUM, "values": STATUSES["requirement"]},
        "status_reason": {"type": "text"},
        "superseded_by": {"type": "ref", "target": "requirement"},
        "blocked": {"type": "bool"},
        "blocked_reason": {"type": "text"},
        "hypothesis_ref": {"type": "text"},
        "legacy": {"type": "bool"},
        "prd_ref": {"type": "text"},
        "notes": {"type": "text"},
        "body": {"type": "body"},
    },
    "criterion": {
        "requirement_id": {"type": "ref", "target": "requirement"},
        "text": {"type": "required_text"},
        "status": {"type": _ENUM, "values": STATUSES["criterion"]},
        "evidence": {"type": "text"},
        "evidence_level": {
            "type": _ENUM,
            "values": ("none", "unit", "integration", "staging", "production"),
        },
        "notes": {"type": "text"},
    },
    "feature": {
        "slug": {"type": "slug"},
        "title": {"type": "title"},
        "layer": {"type": _ENUM, "values": ("foundation", "core", "growth")},
        "status": {"type": _ENUM, "values": STATUSES["feature"]},
        "status_reason": {"type": "text"},
        "phase": {"type": "text"},
        "description": {"type": "text"},
        "infra": {"type": "bool"},
        "paths": {"type": "paths"},
        "blocked": {"type": "bool"},
        "blocked_reason": {"type": "text"},
        "notes": {"type": "text"},
        "body": {"type": "body"},
    },
    "spec": {
        "title": {"type": "title"},
        "summary": {"type": "required_text"},
        "status": {"type": _ENUM, "values": STATUSES["spec"]},
        "status_reason": {"type": "text"},
        "superseded_by": {"type": "ref", "target": "spec"},
        "paths": {"type": "paths"},
        "blocked": {"type": "bool"},
        "blocked_reason": {"type": "text"},
        "notes": {"type": "text"},
        "body": {"type": "body"},
    },
    "doc": {
        "title": {"type": "title"},
        "kind": {"type": _ENUM, "values": DOC_KINDS},
        "summary": {"type": "doc_summary"},
        "status": {"type": _ENUM, "values": STATUSES["doc"]},
        "superseded_by": {"type": "ref", "target": "doc"},
        "notes": {"type": "text"},
        "body_ref": {"type": "text"},
        # `pinned`: the reader keeps this document at hand. `is_prd`: this document IS the
        # project's PRD — one flag instead of a view that owns a single kind.
        "pinned": {"type": "bool"},
        "is_prd": {"type": "bool"},
    },
}

#: what `add <KIND>` accepts (never status: the initial state is fixed)
ADD_FIELDS: dict[str, tuple[str, ...]] = {
    "requirement": ("title", "statement", "rationale", "priority", "phase", "notes",
                    "hypothesis_ref", "legacy", "prd_ref", "body"),
    "criterion": ("requirement_id", "text", "notes"),
    "feature": ("title", "layer", "slug", "phase", "description", "infra", "paths", "notes",
                "body"),
    "spec": ("title", "summary", "paths", "notes", "body"),
    "doc": ("title", "kind", "summary", "notes", "body_ref", "pinned", "is_prd"),
}

#: what `set <REF> key=value` accepts
SET_FIELDS: dict[str, tuple[str, ...]] = {
    "requirement": ("title", "statement", "rationale", "priority", "phase", "status",
                    "status_reason", "superseded_by", "blocked", "blocked_reason",
                    "hypothesis_ref", "legacy", "prd_ref", "notes", "body"),
    "criterion": ("text", "status", "evidence", "evidence_level", "notes"),
    "feature": ("slug", "title", "layer", "status", "status_reason", "phase",
                "description", "infra", "paths", "blocked", "blocked_reason", "notes", "body"),
    "spec": ("title", "summary", "status", "status_reason", "superseded_by", "paths",
             "blocked", "blocked_reason", "notes", "body"),
    "doc": ("title", "kind", "summary", "status", "superseded_by", "notes", "body_ref",
            "pinned", "is_prd"),
}

REQUIRED_AT_ADD: dict[str, tuple[str, ...]] = {
    "requirement": ("title", "statement", "priority"),
    "criterion": ("requirement_id", "text"),
    "feature": ("title", "layer"),
    "spec": ("title", "summary"),
    "doc": ("title", "kind", "summary"),
}

DEFAULTS: dict[str, dict[str, Any]] = {
    "requirement": {"blocked": 0, "legacy": 0},
    "criterion": {},
    "feature": {"blocked": 0, "infra": 0},
    "spec": {"blocked": 0},
    "doc": {"pinned": 0, "is_prd": 0},
}


def editable_fields(kind: str) -> tuple[str, ...]:
    """The keys `set` accepts for ``kind`` (named in its unknown-key error)."""
    return SET_FIELDS[kind]


# ------------------------------------------------------------------------ validation
def validate_field(kind: str, name: str, value: Any) -> Any:
    """Check and normalize one field value; raises ``ValidationError`` naming it."""
    rule = FIELD_RULES[kind].get(name)
    if rule is None:
        raise ValidationError(f"{name} is not a field of {PREFIX[kind]}#")
    kind_of_rule = rule["type"]

    if kind_of_rule in ("text", "required_text", "title", "doc_summary"):
        if not isinstance(value, str):
            raise ValidationError(f"{name} must be text, got {value!r}")
        cleaned = value.strip()
        if kind_of_rule in ("required_text", "doc_summary") and not cleaned:
            raise ValidationError(f"{name} must not be empty")
        if kind_of_rule == "doc_summary" and len(cleaned) > DOC_SUMMARY_MAX:
            raise ValidationError(
                f"summary must be 1-2 sentences, got {len(cleaned)} characters"
                f" (max {DOC_SUMMARY_MAX})"
            )
        if kind_of_rule == "title":
            if not 3 <= len(cleaned) <= 80:
                raise ValidationError(
                    f"title must be 3-80 characters, got {len(cleaned)}"
                )
        return cleaned

    if kind_of_rule == "body":
        # the full text, stored exactly as given (no strip: a Markdown body keeps its
        # trailing newline); a blank body means "no body" and is stored as NULL
        if not isinstance(value, str):
            raise ValidationError(f"{name} must be text, got {value!r}")
        return value if value.strip() else None

    if kind_of_rule == _ENUM:
        allowed = rule["values"]
        if value not in allowed:
            raise ValidationError(f"{name} must be one of {'|'.join(allowed)}, got {value!r}")
        return value

    if kind_of_rule == "bool":
        return _as_bool(name, value)

    if kind_of_rule == "slug":
        if not isinstance(value, str) or not FEATURE_SLUG_RE.match(value):
            raise ValidationError(
                f"slug must match {FEATURE_SLUG_RULE}, got {value!r}"
            )
        return value

    if kind_of_rule == "paths":
        return _as_paths(name, value)

    if kind_of_rule == "ref":
        return _as_ref_int(name, value)

    raise ValidationError(f"{name}: unsupported rule {kind_of_rule}")   # pragma: no cover


def _as_bool(name: str, value: Any) -> int:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int) and value in (0, 1):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in ("1", "true", "yes", "on"):
            return 1
        if lowered in ("0", "false", "no", "off"):
            return 0
    raise ValidationError(f"{name} must be 0 or 1 (true/false), got {value!r}")


def _as_paths(name: str, value: Any) -> list[str]:
    if isinstance(value, str):
        items = value.split(",")
    elif isinstance(value, (list, tuple)):
        items = list(value)
    else:
        raise ValidationError(f"{name} must be a comma-separated list of paths, got {value!r}")
    cleaned = [str(item).strip() for item in items if str(item).strip()]
    return cleaned


def _as_ref_int(name: str, value: Any) -> Optional[int]:
    if value is None or value == "":
        return None
    if isinstance(value, int) and value > 0:
        return value
    if isinstance(value, str):
        if value.strip().lower() in ("-", "none", "null"):
            return None
        kind, identifier = parse_ref(value)          # may raise ValidationError
        if isinstance(identifier, str):
            raise ValidationError(f"{name} must be an id reference like R3, got {value!r}")
        return identifier
    raise ValidationError(f"{name} must be an id reference like R3, got {value!r}")


def validate_new(kind: str, fields: dict) -> dict:
    """Check an ``add`` payload and return the row values to store.

    Unknown keys (including ``status``) are refused: ``add`` never sets the state,
    it starts at ``INITIAL_STATUS[kind]``. Nothing here touches the database — the
    store resolves cross-references and checks uniqueness itself.
    """
    if kind not in KINDS:
        raise ValidationError(f"unknown entity type {kind!r}")
    if "status" in fields and fields["status"] is not None:
        raise ValidationError(
            f"add cannot set status: {PREFIX[kind]}# starts at {INITIAL_STATUS[kind]}"
        )
    allowed = ADD_FIELDS[kind]
    for name in fields:
        if name not in allowed:
            raise ValidationError(
                f"{name} is not a field of add {PREFIX[kind]}"
                f" (allowed: {', '.join(allowed)})"
            )
    for name in REQUIRED_AT_ADD[kind]:
        if fields.get(name) in (None, ""):
            raise ValidationError(f"{name} is required for {PREFIX[kind]}#")

    values: dict[str, Any] = {}
    for name, raw in fields.items():
        if raw is None:
            continue
        values[name] = validate_field(kind, name, raw)
    for name, default in DEFAULTS[kind].items():
        values.setdefault(name, default)
    check_blocked(values)
    values["status"] = INITIAL_STATUS[kind]
    return values


def check_blocked(values: dict) -> None:
    """A blocked row must say why (S1: reason required when the flag is 1)."""
    if values.get("blocked") == 1 and not str(values.get("blocked_reason") or "").strip():
        raise ValidationError("blocked_reason is required when blocked is 1")


# ------------------------------------------------------------- status transitions
def _has_text(value: Any) -> bool:
    return bool(str(value or "").strip())


def check_transition(kind: str, current_row: dict, target: str, fields: dict,
                     facts: dict) -> None:
    """Refuse an illegal status move, naming the state, field or rule that blocks it.

    Pure: ``current_row`` is the stored row, ``fields`` the merged values after the
    requested change (so a reason given in the same call counts), ``facts`` the
    counts/statuses the store loaded from the link tables.

    Legal shape (S2 section 1): one forward step along the chain, any backward move,
    and the terminal state from anywhere; a terminal state is never left.
    """
    status = current_row["status"]
    entity = f"{PREFIX[kind]}{current_row['id']}"
    statuses = STATUSES[kind]
    if target not in statuses:
        raise ValidationError(
            f"{target} is not a state of {PREFIX[kind]}# (one of {'|'.join(statuses)})"
        )
    if target == status:
        raise ValidationError(f"{entity} is already {status}; nothing to change")
    move_text = f"{entity} {status} -> {target}"

    chain = CHAIN.get(kind)
    terminal = TERMINAL.get(kind, ())
    allowed = TRANSITIONS[kind].get(status, ())
    if target not in allowed:
        if status in terminal:
            raise ValidationError(
                f"cannot move {move_text}: {status} is terminal and cannot be left"
            )
        if chain is not None and status in chain and target in chain:
            through = chain[chain.index(status) + 1]
            raise ValidationError(f"cannot move {move_text}: go through {through} first")
        raise ValidationError(f"cannot move {move_text}: {target} is not reachable from {status}")

    if kind == "requirement":
        _requirement_exit(move_text, target, fields, facts)
    elif kind == "feature":
        _feature_exit(move_text, target, fields, facts)
    elif kind == "spec":
        _spec_exit(move_text, target, fields, facts)
    elif kind == "doc":
        _doc_exit(move_text, target, fields, facts)
    elif kind == "criterion" and target in ("passed", "waived"):
        if not _has_text(fields.get("evidence")):
            raise ValidationError(f"cannot move {move_text}: {target} needs evidence")


def _requirement_exit(move_text: str, target: str, fields: dict, facts: dict) -> None:
    if target == "approved":
        if not _has_text(fields.get("rationale")):
            raise ValidationError(f"cannot move {move_text}: needs a rationale")
        features = int(facts.get("features", 0))
        if features < 1:
            raise ValidationError(
                f"cannot move {move_text}: needs at least one linked feature (has 0 features)"
            )
    elif target == "in_progress":
        specs = int(facts.get("specs", 0))
        criteria = int(facts.get("criteria", 0))
        if specs < 1 or criteria < 1:
            raise ValidationError(
                "cannot move %s: needs at least one spec and one criterion"
                " (has %d spec%s, %d criteri%s)"
                % (move_text, specs, "" if specs == 1 else "s",
                   criteria, "on" if criteria == 1 else "a")
            )
    elif target == "done":
        total = int(facts.get("criteria", 0))
        still_open = int(facts.get("criteria_open", 0))
        if total < 1:
            raise ValidationError(f"cannot move {move_text}: needs at least one criterion")
        if still_open:
            raise ValidationError(
                f"cannot move {move_text}: every criterion must be passed or waived"
                f" (has {total} criteria, {still_open} not passed or waived)"
            )
    elif target == "dropped":
        if not _has_text(fields.get("status_reason")):
            raise ValidationError(f"cannot move {move_text}: needs status_reason")
        if fields.get("superseded_by") is not None and not facts.get("superseded_by_ok"):
            raise ValidationError(
                f"cannot move {move_text}: superseded_by must be an existing, different"
                " requirement in the same project"
            )


def _feature_exit(move_text: str, target: str, fields: dict, facts: dict) -> None:
    if target == "in_progress":
        blocked = [int(value) for value in facts.get("mandatory_planned", [])]
        if blocked and not facts.get("override_reason"):
            named = ", ".join(f"F{value} (planned)" for value in blocked)
            raise ValidationError(f"cannot move {move_text}: depends on {named}")
    elif target == "shipped":
        total = int(facts.get("requirements", 0))
        still_open = int(facts.get("requirements_not_done", 0))
        if total == 0 and not fields.get("infra"):
            raise ValidationError(
                f"cannot move {move_text}: a feature with no linked requirement may not ship"
                " (set infra=1 for plumbing)"
            )
        if still_open:
            raise ValidationError(
                f"cannot move {move_text}: every linked requirement must be done"
                f" ({still_open} still open)"
            )
    elif target == "cut":
        if not _has_text(fields.get("status_reason")):
            raise ValidationError(f"cannot move {move_text}: needs status_reason")
        only_here = int(facts.get("open_only_here", 0))
        if only_here:
            raise ValidationError(
                f"cannot move {move_text}: {only_here} open requirement(s) are linked only to"
                " this feature - link them to another non-cut feature or drop them first"
            )


def _spec_exit(move_text: str, target: str, fields: dict, facts: dict) -> None:
    if target == "approved":
        if int(facts.get("requirements", 0)) < 1:
            raise ValidationError(
                f"cannot move {move_text}: needs at least one linked requirement"
            )
    elif target == "implemented":
        if not _has_text(fields.get("notes")) and int(facts.get("external_links", 0)) < 1:
            raise ValidationError(f"cannot move {move_text}: needs notes or an external link")
    elif target == "verified":
        if int(facts.get("requirements", 0)) < 1:
            raise ValidationError(
                f"cannot move {move_text}: needs at least one linked requirement"
            )
        total = int(facts.get("criteria", 0))
        still_open = int(facts.get("criteria_open", 0))
        if total < 1:
            raise ValidationError(f"cannot move {move_text}: needs at least one criterion")
        if still_open:
            raise ValidationError(
                f"cannot move {move_text}: every criterion must be passed or waived"
                f" (has {total} criteria, {still_open} not passed or waived)"
            )
    elif target == "superseded":
        if fields.get("superseded_by") is None or not facts.get("superseded_by_ok"):
            raise ValidationError(
                f"cannot move {move_text}: needs superseded_by naming an existing,"
                " different spec in the same project"
            )


def _doc_exit(move_text: str, target: str, fields: dict, facts: dict) -> None:
    if target == "superseded":
        if fields.get("superseded_by") is None or not facts.get("superseded_by_ok"):
            raise ValidationError(
                f"cannot move {move_text}: needs superseded_by naming an existing,"
                " different document in the same project"
            )


# --------------------------------------------------------------- --why-not (HP-13)
def transition_blockers(kind: str, status: str, target: str, fields: dict,
                        facts: dict) -> list[str]:
    """Every unmet condition of ``status -> target`` as a short phrase (HP-13).

    Pure and never raises for rule problems. The phrases mirror exactly what
    ``check_transition`` enforces, so the result is ``[]`` if and only if
    ``check_transition(kind, {"status": status, **fields}, target, fields, facts)``
    would pass. One entry per missing thing, in a stable order: reachability
    first, then the exit conditions in the order ``_*_exit`` checks them.
    """
    statuses = STATUSES[kind]
    if target not in statuses:
        return [f"{target} is not a state of {PREFIX[kind]}# (one of {'|'.join(statuses)})"]
    if target == status:
        return [f"already {status}"]

    chain = CHAIN.get(kind)
    terminal = TERMINAL.get(kind, ())
    allowed = TRANSITIONS[kind].get(status, ())
    if target not in allowed:
        if status in terminal:
            return [f"{status} is terminal and cannot be left"]
        if chain is not None and status in chain and target in chain:
            through = chain[chain.index(status) + 1]
            blockers = [f"go through {through} first"]
            blockers.extend(f"then, to reach {through}: {item}"
                            for item in _exit_blockers(kind, through, fields, facts))
            blockers.extend(f"then, to reach {target}: {item}"
                            for item in _exit_blockers(kind, target, fields, facts))
            return blockers
        return [f"{target} is not reachable from {status}"]
    return _exit_blockers(kind, target, fields, facts)


def _exit_blockers(kind: str, target: str, fields: dict, facts: dict) -> list[str]:
    """The unmet conditions of one target state, one entry per missing thing."""
    blockers: list[str] = []
    if kind == "requirement":
        if target == "approved":
            if not _has_text(fields.get("rationale")):
                blockers.append("needs a rationale")
            if int(facts.get("features", 0)) < 1:
                blockers.append("needs at least one linked feature (has 0 features)")
        elif target == "in_progress":
            specs = int(facts.get("specs", 0))
            criteria = int(facts.get("criteria", 0))
            if specs < 1:
                blockers.append("needs at least one spec (has 0 specs)")
            if criteria < 1:
                blockers.append("needs at least one criterion (has 0 criteria)")
        elif target == "done":
            total = int(facts.get("criteria", 0))
            still_open = int(facts.get("criteria_open", 0))
            if total < 1:
                blockers.append("needs at least one criterion")
            if still_open:
                blockers.append(
                    "every criterion must be passed or waived"
                    f" (has {total} criteria, {still_open} not passed or waived)"
                )
        elif target == "dropped":
            if not _has_text(fields.get("status_reason")):
                blockers.append("needs status_reason")
            if fields.get("superseded_by") is not None and not facts.get("superseded_by_ok"):
                blockers.append("superseded_by must be an existing, different"
                                " requirement in the same project")
    elif kind == "feature":
        if target == "in_progress":
            blocked = [int(value) for value in facts.get("mandatory_planned", [])]
            if blocked and not facts.get("override_reason"):
                named = ", ".join(f"F{value} (planned)" for value in blocked)
                blockers.append(f"depends on {named}")
        elif target == "shipped":
            total = int(facts.get("requirements", 0))
            still_open = int(facts.get("requirements_not_done", 0))
            if total == 0 and not fields.get("infra"):
                blockers.append("a feature with no linked requirement may not ship"
                                " (set infra=1 for plumbing)")
            if still_open:
                blockers.append(f"every linked requirement must be done"
                                f" ({still_open} still open)")
        elif target == "cut":
            if not _has_text(fields.get("status_reason")):
                blockers.append("needs status_reason")
            only_here = int(facts.get("open_only_here", 0))
            if only_here:
                blockers.append(
                    f"{only_here} open requirement(s) are linked only to this feature"
                    " - link them to another non-cut feature or drop them first"
                )
    elif kind == "spec":
        if target == "approved":
            if int(facts.get("requirements", 0)) < 1:
                blockers.append("needs at least one linked requirement")
        elif target == "implemented":
            if not _has_text(fields.get("notes")) and int(facts.get("external_links", 0)) < 1:
                blockers.append("needs notes or an external link")
        elif target == "verified":
            if int(facts.get("requirements", 0)) < 1:
                blockers.append("needs at least one linked requirement")
            total = int(facts.get("criteria", 0))
            still_open = int(facts.get("criteria_open", 0))
            if total < 1:
                blockers.append("needs at least one criterion")
            if still_open:
                blockers.append(
                    "every criterion must be passed or waived"
                    f" (has {total} criteria, {still_open} not passed or waived)"
                )
        elif target == "superseded":
            if fields.get("superseded_by") is None or not facts.get("superseded_by_ok"):
                blockers.append("needs superseded_by naming an existing, different spec"
                                " in the same project")
    elif kind == "doc":
        if target == "superseded":
            if fields.get("superseded_by") is None or not facts.get("superseded_by_ok"):
                blockers.append("needs superseded_by naming an existing, different document"
                                " in the same project")
    elif kind == "criterion" and target in ("passed", "waived"):
        if not _has_text(fields.get("evidence")):
            blockers.append(f"{target} needs evidence")
    return blockers
