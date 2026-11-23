"""``apply PLAN.yaml``: a whole batch of add / set / link / unlink in ONE process (HP-14).

Loading one repository into the board took about 12 minutes because every entity and
every link was its own ``hermes product ...`` process (about 2 s of start-up each, ~350
calls). A plan file runs the same store functions in one process, refuses the whole batch
on the first bad operation WITHOUT writing, and keeps a copy of the db when it does write.

The plan format (frozen, S4 section 2). YAML or JSON (JSON is valid YAML), parsed with
``ruamel.yaml`` ``YAML(typ="safe")``::

    project: demo                        # optional; a --project that differs is refused
    ops:
      - add: {type: R, as: r1, title: "Pay by card", statement: "A buyer can pay",
              priority: must}
      - add: {type: F, as: pay, title: "Card payments", slug: pay, layer: core}
      - link: {a: $r1, b: $pay}
      - set: {ref: $r1, status: approved, rationale: "owner requirement"}

Every op is a mapping with EXACTLY ONE of ``add``, ``set``, ``link``, ``unlink``:

* ``add``: ``type`` (R|A|F|S|DOC) plus the ``add`` flag names with underscores (the keys
  of ``cli._ADD_FLAG_FIELDS``, plus ``body``, ``body_file``, ``body_ref`` and, for a DOC,
  ``kind``), ``as`` (an alias) and ``if_missing`` (default TRUE: an idempotent rerun).
  A relative ``body_file`` is relative to the plan file's directory.
* ``set``: ``ref`` plus field changes like ``set REF key=value``; ``reason``,
  ``override_reason`` and ``body_file`` are the ``set`` options. A ``set`` that only says
  ``<ref> is already <status>; nothing to change`` is a no-op, not a failed batch.
* ``link`` / ``unlink``: ``{a, b}`` for a relation or ``{a, needs, optional}`` for a
  dependency. Both are idempotent (HP-12): an existing or missing link is reported, not
  raised.

``$alias`` in ANY ref position resolves to the ref of an EARLIER ``add ... as: alias``; when
``if_missing`` found an existing row the alias is bound to that row's ref.

The safety order the CLI drives (``cli._cmd_apply``): static validation, then one REHEARSAL
of every op on ``dryrun.throwaway_home()`` (a copy of the store), then the LIVE run after
backing the db up with SQLite's backup API. A rehearsal failure writes nothing; a live
failure names the backup and how to restore it. Nothing here re-implements field validation:
every check and every id comes from ``store`` and ``model``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, NoReturn, Optional

from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError

try:  # package submodule (Hermes plugin loader)
    from . import model, store
except ImportError:  # top-level module (test suite)
    import model  # type: ignore[no-redef]
    import store  # type: ignore[no-redef]

#: the four op keys; exactly one per op, nothing else
OP_KEYS = ("add", "set", "link", "unlink")

#: add keys that are not entity fields
_ADD_SPECIAL = ("type", "as", "if_missing")

#: the body options `add` and `set` carry (the store turns them into a body)
_BODY_OPTIONS = ("body", "body_file", "body_ref")

#: `set` keys that are options, not field changes
_SET_OPTIONS = ("ref", "reason", "override_reason", "body_file")

#: the exact no-op `<ref> is already <status>; nothing to change` of model.check_transition
_ALREADY_SET = re.compile(r" is already .+; nothing to change$")


class OpFailure(Exception):
    """One op's own store/model error, named by its 1-based index and a short summary."""

    def __init__(self, index: int, summary: str, message: str) -> None:
        super().__init__(message)
        self.index = index
        self.summary = summary
        self.message = message


@dataclass(frozen=True)
class Op:
    """One validated plan operation: its 1-based index, its verb, its payload and alias."""

    index: int
    action: str
    payload: dict
    alias: Optional[str] = None


@dataclass
class Result:
    """What one ``run_ops`` pass did: the counts, the alias bindings and the output lines."""

    project: str
    op_count: int
    created: int = 0
    existing: int = 0
    linked: int = 0
    already_linked: int = 0
    unlinked: int = 0
    set_count: int = 0
    already_set: int = 0
    aliases: dict[str, str] = field(default_factory=dict)
    created_lines: list[str] = field(default_factory=list)
    existing_lines: list[str] = field(default_factory=list)
    lines: list[str] = field(default_factory=list)
    refs: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------- loading/parsing
def load_plan(path: str | Path) -> dict:
    """Read a YAML or JSON plan file; a missing or unparsable file names itself."""
    target = Path(str(path)).expanduser()
    try:
        text = target.read_text(encoding="utf-8")
    except OSError as exc:
        raise model.ValidationError(f"cannot read plan {target}: {exc}") from exc
    try:
        data = YAML(typ="safe").load(text)
    except YAMLError as exc:
        raise model.ValidationError(f"cannot parse plan {target}: {exc}") from exc
    if data is None:
        raise model.ValidationError(f"plan {target} is empty: it needs an `ops` list")
    return data


# ------------------------------------------------------------------ static validation
def _add_flag_fields() -> dict[str, str]:
    """``cli._ADD_FLAG_FIELDS`` (flag -> store field): the ONE copy of that map lives there.

    Imported lazily because ``cli`` imports this module: there is no cycle at import time
    and the data is the CLI's, never a second transcription here.
    """
    try:  # package submodule (Hermes plugin loader)
        from . import cli as cli_module
    except ImportError:  # top-level module (test suite)
        import cli as cli_module  # type: ignore[no-redef]
    return cli_module._ADD_FLAG_FIELDS


def allowed_add_fields(kind: str) -> set[str]:
    """The ``add`` keys (flag names) that kind accepts in a plan."""
    flags = {flag for flag, field_name in _add_flag_fields().items()
             if field_name in model.ADD_FIELDS[kind]}
    if kind in model.BODY_KINDS or kind == "doc":
        flags |= {"body", "body_file"}
    if kind == "doc":
        flags |= {"body_ref", "kind"}
    return flags


def _check_alias_use(index: int, value: Any, aliases: dict[str, int]) -> None:
    """A ``$alias`` must name an alias an EARLIER add defined."""
    if not isinstance(value, str) or not value.strip().startswith("$"):
        return
    name = value.strip()[1:]
    if not name:
        raise model.ValidationError(f"op {index}: empty alias reference {value!r}")
    if name not in aliases:
        raise model.ValidationError(
            f"op {index}: unknown alias ${name} (only an earlier `add ... as: {name}`"
            " defines it)"
        )


def validate_plan(plan: dict) -> list[Op]:
    """Check the whole plan statically, with no store access; returns its ops in order."""
    if not isinstance(plan, dict):
        raise model.ValidationError("plan must be a mapping with an `ops` list")
    raw_ops = plan.get("ops")
    if not isinstance(raw_ops, list):
        raise model.ValidationError("plan needs an `ops` list (top-level keys: project, ops)")
    project = plan.get("project")
    if project is not None and (not isinstance(project, str) or not project.strip()):
        raise model.ValidationError(f"plan project must be a slug, got {project!r}")
    ops: list[Op] = []
    aliases: dict[str, int] = {}
    for index, raw in enumerate(raw_ops, start=1):
        ops.append(_validate_op(index, raw, aliases))
    return ops


def _validate_op(index: int, raw: Any, aliases: dict[str, int]) -> Op:
    def fail(message: str) -> NoReturn:
        raise model.ValidationError(f"op {index}: {message}")

    if not isinstance(raw, dict):
        fail("expected a mapping with one of add, set, link, unlink")
    unknown = [key for key in raw if key not in OP_KEYS]
    if unknown:
        fail(f"unknown op key {unknown[0]!r} (one of add, set, link, unlink)")
    present = [key for key in OP_KEYS if key in raw]
    if len(present) != 1:
        fail("expected exactly one of add, set, link, unlink, got %d" % len(present))
    action = present[0]
    payload = raw[action]
    if not isinstance(payload, dict):
        fail(f"{action} needs a mapping of its fields")
    if action == "add":
        return _validate_add(index, payload, aliases)
    if "as" in payload:
        fail(f"'as' is only valid on add, not on {action}")
    if action == "set":
        return _validate_set(index, payload, aliases)
    return _validate_link(index, action, payload, aliases)


def _validate_add(index: int, payload: dict, aliases: dict[str, int]) -> Op:
    def fail(message: str) -> NoReturn:
        raise model.ValidationError(f"op {index}: {message}")

    given = payload.get("type")
    if not isinstance(given, str):
        fail("add needs a type (R, A, F, S or DOC)")
    try:
        kind = model.kind_for(given)
    except model.ValidationError as exc:
        fail(str(exc))
    allowed = allowed_add_fields(kind)
    for key in payload:
        if key in _ADD_SPECIAL:
            continue
        if key not in allowed:
            fail(f"{key!r} is not a field of add {model.PREFIX[kind]}"
                 f" (allowed: {', '.join(sorted(allowed))})")
    if "if_missing" in payload and not isinstance(payload["if_missing"], bool):
        fail("if_missing must be true or false")
    if "req" in payload:
        _check_alias_use(index, payload["req"], aliases)
    alias = payload.get("as")
    if alias is None:
        return Op(index, "add", payload, None)
    if not isinstance(alias, str) or not alias.strip() or alias.strip().startswith("$"):
        fail(f"alias must be a plain name, got {alias!r}")
    alias = alias.strip()
    if alias in aliases:
        fail(f"alias {alias!r} is already defined by op {aliases[alias]}")
    aliases[alias] = index
    return Op(index, "add", payload, alias)


def _validate_set(index: int, payload: dict, aliases: dict[str, int]) -> Op:
    def fail(message: str) -> NoReturn:
        raise model.ValidationError(f"op {index}: {message}")

    if "ref" not in payload:
        fail("set needs a ref (R3, F:slug or $alias)")
    ref = payload["ref"]
    if not isinstance(ref, str) or not ref.strip():
        fail(f"set ref must be a reference, got {ref!r}")
    _check_alias_use(index, ref, aliases)
    for key in ("reason", "override_reason", "body_file"):
        if payload.get(key) is not None and not isinstance(payload[key], str):
            fail(f"{key} must be text")
    changes = [key for key in payload if key not in _SET_OPTIONS]
    if not changes and "body_file" not in payload:
        fail("set needs at least one field to change (a ref alone changes nothing)")
    return Op(index, "set", payload, None)


def _validate_link(index: int, action: str, payload: dict,
                   aliases: dict[str, int]) -> Op:
    def fail(message: str) -> NoReturn:
        raise model.ValidationError(f"op {index}: {message}")

    if "a" not in payload:
        fail(f"{action} needs a (the first reference)")
    _check_alias_use(index, payload["a"], aliases)
    shapes = [key for key in ("b", "needs") if key in payload]
    if len(shapes) != 1:
        fail(f"{action} needs exactly one of b (a relation) or needs (a dependency)")
    _check_alias_use(index, payload[shapes[0]], aliases)
    if "optional" in payload and not isinstance(payload["optional"], bool):
        fail("optional must be true or false")
    return Op(index, action, payload, None)


# ------------------------------------------------------------------------ execution
def _resolve(value: Any, aliases: dict[str, str], index: int) -> Any:
    """Resolve ``$alias``; anything else passes through to the store's own ref checks."""
    if not isinstance(value, str) or not value.strip().startswith("$"):
        return value
    name = value.strip()[1:]
    if name not in aliases:
        raise model.ValidationError(f"op {index}: unknown alias ${name}")
    return aliases[name]


def _body_path(value: Any, base: Path) -> Path:
    """A plan-relative ``body_file`` resolves against the plan file's directory."""
    path = Path(str(value)).expanduser()
    return path if path.is_absolute() else (base / path)


def _add_fields(kind: str, payload: dict, base: Path) -> dict:
    """Turn one ``add`` payload into the field dict ``store.create_entity`` takes."""
    mapping = _add_flag_fields()
    accepted = model.ADD_FIELDS[kind]
    fields: dict[str, Any] = {}
    for key, value in payload.items():
        if key in _ADD_SPECIAL or value is None:
            continue
        name = mapping.get(key, key)                 # `req` -> requirement_id
        if name not in accepted and name not in _BODY_OPTIONS:
            continue                                  # refused by validate_plan already
        fields[name] = value
    if fields.get("body_file") is not None:
        fields["body_file"] = str(_body_path(fields["body_file"], base))
    return fields


def _do_add(project: str, op: Op, result: Result, base: Path) -> None:
    payload = op.payload
    kind = model.kind_for(str(payload["type"]))
    if "req" in payload:
        payload = {**payload, "req": _resolve(payload["req"], result.aliases, op.index)}
    fields = _add_fields(kind, payload, base)
    if payload.get("if_missing", True):
        found = store.find_existing(project, kind, fields)
        if found is not None:
            ref = store.ref_for(kind, found)
            result.aliases.update({op.alias: ref} if op.alias else {})
            result.existing_lines.append(f"{ref} exists (alias {op.alias})" if op.alias
                                         else f"{ref} exists")
            result.refs.append(ref)
            result.existing += 1
            return
    created = store.create_entity(project, kind, fields)
    ref = created["ref"]
    result.aliases.update({op.alias: ref} if op.alias else {})
    result.created_lines.append(f"{ref} created (alias {op.alias})" if op.alias
                                else f"{ref} created")
    result.refs.append(ref)
    result.created += 1


def _do_set(project: str, op: Op, result: Result, base: Path) -> None:
    payload = op.payload
    ref = _resolve(payload["ref"], result.aliases, op.index)
    changes = {key: value for key, value in payload.items() if key not in _SET_OPTIONS}
    body_file = payload.get("body_file")
    source = str(_body_path(body_file, base)) if body_file is not None else None
    try:
        store.update_entity(project, ref, changes, reason=payload.get("reason"),
                            override_reason=payload.get("override_reason"),
                            body_source=source)
    except model.ValidationError as exc:
        if _ALREADY_SET.search(str(exc)):        # a no-op is not a failed batch
            result.already_set += 1
            return
        raise
    result.set_count += 1


def _do_link(project: str, op: Op, result: Result) -> None:
    payload = op.payload
    first = _resolve(payload["a"], result.aliases, op.index)
    needs = (_resolve(payload["needs"], result.aliases, op.index)
             if "needs" in payload else None)
    second = (_resolve(payload["b"], result.aliases, op.index) if needs is None else None)
    outcome = store.link_entries(project, first, second, needs=needs,
                                 optional=bool(payload.get("optional", False)),
                                 remove=op.action == "unlink")
    if op.action == "unlink":
        if outcome["changed"]:
            result.unlinked += 1
    elif outcome["changed"]:
        result.linked += 1
    else:
        result.already_linked += 1


def _summary(op: Op) -> str:
    """The card's short op summary: `add S "<title>"`, `set R3 status=approved`, `link S4 R2`."""
    payload = op.payload
    if op.action == "add":
        raw = str(payload.get("type", "?"))
        try:
            prefix = model.PREFIX[model.kind_for(raw)]
        except model.ValidationError:
            prefix = raw
        title = " ".join(str(payload.get("title") or "").split())[:40]
        return f'add {prefix} "{title}"'
    if op.action == "set":
        parts = [f"{key}={value}" for key, value in payload.items()
                 if key not in ("ref", "reason", "override_reason")]
        return f"set {payload.get('ref')} {' '.join(parts) or 'body'}"
    if "needs" in payload:
        return f"{op.action} {payload.get('a')} needs {payload.get('needs')}"
    return f"{op.action} {payload.get('a')} {payload.get('b')}"


def summary_message(op: Op, aliases: dict[str, str]) -> str:
    """The op summary with every ``$alias`` resolved (used for failure messages)."""
    text = _summary(op)
    for name, ref in aliases.items():
        text = text.replace(f"${name}", ref)
    return text


def _summary_line(result: Result) -> str:
    return (f"apply {result.project}: {result.op_count} ops: {result.created} created,"
            f" {result.existing} existing, {result.linked} linked,"
            f" {result.already_linked} already linked, {result.unlinked} unlinked,"
            f" {result.set_count} set")


def run_ops(project: str, ops: list[Op], *, plan_dir: str | Path | None = None) -> Result:
    """Execute every op in order through the store functions; raises ``OpFailure`` on one.

    No transaction spans the batch: each store call keeps its own all-or-nothing
    transaction (the whole-batch guarantee is the rehearsal-then-write order in
    ``cli._cmd_apply``). A failure is named by the op's 1-based index, never swallowed.
    """
    base = Path(plan_dir).expanduser() if plan_dir is not None else Path.cwd()
    result = Result(project=project, op_count=len(ops))
    for op in ops:
        try:
            if op.action == "add":
                _do_add(project, op, result, base)
            elif op.action == "set":
                _do_set(project, op, result, base)
            else:
                _do_link(project, op, result)
        except model.ValidationError as exc:
            raise OpFailure(op.index, summary_message(op, result.aliases), str(exc)) from None
        except store.StoreError as exc:
            raise OpFailure(op.index, summary_message(op, result.aliases), str(exc)) from None
    result.lines = result.created_lines + result.existing_lines + [_summary_line(result)]
    return result
