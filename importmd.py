"""``import-md``: the S6 markdown documents become the first project (card HP-10).

The documents phase of this repository keeps the product model in plain markdown
(``docs/specs/S6-markdown-conventions.md``). This module is the mechanical bridge
from that text into the store, and it reuses the S3 importer for every rule that
already exists there (validation, id preservation, counters, atomicity, merge).
Nothing here writes a row itself: it parses the docs into the S3 export layout,
stages that layout in a temp directory, hands it to ``export.import_export``,
and removes the stage afterwards.

Division of labour:

* one pure function per file shape, each taking TEXT (no sqlite): the R blocks,
  the A table, the F table, an S file, a DOC file and the PRD;
* ``build_documents`` reads a directory and returns every parsed entry;
* ``cross_check`` (A25) compares the entries with ``index.md`` and with each
  other, and returns ``problem: <file>: <what>`` lines — any problem means
  nothing is imported;
* ``import_markdown`` is the one entry point the CLI calls.

Ids are preserved exactly as written and never renumbered; the PRD's DOC id is
the next free one above the highest ``DOC<n>`` found, because ids are never
reused. Byte-exactness matters for DOC bodies (they are committed to git), so
bodies are sliced out of the decoded text and re-encoded with no rewriting.
"""

from __future__ import annotations

import io
import re
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Optional, Union

from ruamel.yaml import YAML

try:  # package submodule (Hermes plugin loader)
    from . import export, model, store
except ImportError:  # top-level module (test suite)
    import export  # type: ignore[no-redef]
    import model  # type: ignore[no-redef]
    import store  # type: ignore[no-redef]

#: display prefix -> entity type, for every id the documents use
KIND_BY_PREFIX = {"R": "requirement", "A": "criterion", "F": "feature", "S": "spec",
                  "DOC": "doc"}
PREFIX = {kind: prefix for prefix, kind in KIND_BY_PREFIX.items()}

#: the entity types a ``doc_link`` row may point at (a DOC never links a DOC)
DOC_LINK_KINDS = ("requirement", "criterion", "feature", "spec")

#: where a kind's entries live, for the cross-check messages
KIND_FILES = {"requirement": "requirements.md", "criterion": "acceptance.md",
              "feature": "features.md", "spec": "specs/", "doc": "supporting/"}

# ------------------------------------------------------------------ parsed entry
@dataclass
class RequirementEntry:
    """One ``## R<n> <title>`` block of requirements.md."""

    id: int
    title: str
    status: str
    priority: str
    phase: Optional[str]
    features: list[int] = field(default_factory=list)
    specs: list[int] = field(default_factory=list)
    criteria: list[int] = field(default_factory=list)
    statement: str = ""
    source: str = "requirements.md"


@dataclass
class CriterionEntry:
    """One row of the acceptance.md table."""

    id: int
    requirement_id: int
    status: str
    text: str
    source: str = "acceptance.md"


@dataclass
class FeatureEntry:
    """One row of the features.md table."""

    id: int
    title: str
    layer: str
    status: str
    needs: list[int] = field(default_factory=list)
    requirements: list[int] = field(default_factory=list)
    source: str = "features.md"


@dataclass
class SpecEntry:
    """One ``specs/S<n>-<slug>.md`` file: the header line and the summary."""

    id: int
    title: str
    status: str
    requirements: list[int] = field(default_factory=list)
    features: list[int] = field(default_factory=list)
    summary: str = ""
    source: str = ""


@dataclass
class DocEntry:
    """One DOC#: a supporting document, or the PRD."""

    id: Optional[int]
    title: str
    kind: str
    status: str
    links: list[tuple[str, int]] = field(default_factory=list)
    ignored: list[str] = field(default_factory=list)
    note: Optional[str] = None
    summary: str = ""
    body: bytes = b""
    source: str = ""


@dataclass
class Documents:
    """Everything the documents directory holds, parsed and cross-checkable."""

    requirements: list[RequirementEntry]
    criteria: list[CriterionEntry]
    features: list[FeatureEntry]
    specs: list[SpecEntry]
    docs: list[DocEntry]          # supporting documents, then the PRD last
    index_text: str
    prd: Optional[DocEntry] = None

    def counts(self) -> dict[str, int]:
        return {"requirements": len(self.requirements), "criteria": len(self.criteria),
                "features": len(self.features), "specs": len(self.specs),
                "docs": len(self.docs)}


# --------------------------------------------------------------------- primitives
_FENCE_RE = re.compile(r"^\s*(```|~~~)")
_BACKTICK_LINE_RE = re.compile(r"^\s*`([^`]*)`\s*$")
_HEADING_R_RE = re.compile(r"^##\s+R(\d+)\s+(.+?)\s*$")
_SPEC_TITLE_RE = re.compile(r"^#\s+S(\d+)\s+(.+?)\s*$")
_DOC_TITLE_RE = re.compile(r"^#\s+DOC(\d+)\s+(.+?)\s*$")
_STATEMENT_RE = re.compile(r"^\s*\*\*Statement\.\*\*\s*(.*)$")
_SUMMARY_RE = re.compile(r"^\s*\*\*Summary\.\*\*\s*(.*)$")
_ROW_ID_RE = re.compile(r"^(R|A|F|S|DOC)(\d+)$")
_A_RANGE_RE = re.compile(r"A(\d+)\s+to\s+A(\d+),\s*all\s+`([a-z_]+)`")
_PRD_LINE_RE = re.compile(r"^\s*[-*]\s*\[PRD\]\([^)]*\)\s*\|\s*([^|]*)\|\s*(.*?)\s*$")
_LINK_TEXT_RE = re.compile(r"^\[([^\]]*)\]\([^)]*\)$")
_WHITESPACE_RE = re.compile(r"\s+")


def _collapse(text: Any) -> str:
    """One line: every run of whitespace becomes a single space."""
    return _WHITESPACE_RE.sub(" ", str(text or "")).strip()


def _table_cells(line: str) -> Optional[list[str]]:
    """The stripped cells of a markdown table row, or None for any other line."""
    stripped = line.strip()
    if not stripped.startswith("|"):
        return None
    parts = stripped.split("|")
    return [part.strip() for part in parts[1:-1]] if len(parts) >= 3 else None


def _cell_text(cell: str) -> str:
    """A cell's text: a markdown link renders as its link text."""
    match = _LINK_TEXT_RE.match(cell.strip())
    return match.group(1).strip() if match else cell.strip()


def _ids(text: Any, prefix: str) -> list[int]:
    """The ids written in one header cell (``F1,F2``), in order, ``-``/empty = none."""
    cleaned = str(text or "").strip()
    if cleaned in ("", "-", "none", "null"):
        return []
    return [int(number) for number in
            re.findall(rf"(?<![A-Za-z]){prefix}(\d+)", cleaned)]


def _fmt_ids(cids: Iterable[int], prefix: str) -> str:
    listed = ",".join(f"{prefix}{value}" for value in cids)
    return listed or "none"


def _check_enum(value: str, kind: str, name: str, where: str) -> str:
    """A status/layer/kind value the schema accepts, else a StoreError naming it."""
    allowed = model.FIELD_RULES[kind].get(name, {}).get("values")
    if allowed is None:
        return value
    if value not in allowed:
        raise store.StoreError(
            f"{where}: {name} must be one of {'|'.join(allowed)}, got {value!r}")
    return value


def _blocks(lines: list[str]) -> list[list[str]]:
    """Blank-line separated blocks of a file, fenced code blocks dropped whole."""
    blocks: list[list[str]] = []
    current: list[str] = []
    fence: Optional[str] = None
    for line in lines:
        marker = _FENCE_RE.match(line)
        if fence is not None:
            if marker is not None and marker.group(1) == fence:
                fence = None
            continue
        if marker is not None:
            if current:
                blocks.append(current)
                current = []
            fence = marker.group(1)
            continue
        if not line.strip():
            if current:
                blocks.append(current)
                current = []
            continue
        current.append(line)
    if current:
        blocks.append(current)
    return blocks


def _first_prose(lines: list[str]) -> str:
    """The first non-heading, non-table, non-list paragraph, on one line."""
    for block in _blocks(lines):
        first = block[0].strip()
        if first.startswith(("#", "|", ">")):
            continue
        if re.match(r"^[-*+]\s", first) or re.match(r"^\d+[.)]\s", first):
            continue
        if _STATEMENT_RE.match(first) or _SUMMARY_RE.match(first):
            continue
        text = _collapse(" ".join(part.strip() for part in block))
        if text:
            return text
    return ""


def _paragraph_after(lines: list[str], pattern: re.Pattern) -> str:
    """The paragraph that starts with ``pattern`` (its label removed), as one line."""
    for position, line in enumerate(lines):
        match = pattern.match(line)
        if match is None:
            continue
        parts = [match.group(1).strip()]
        for following in lines[position + 1:]:
            if not following.strip() or following.lstrip().startswith("#"):
                break
            parts.append(following.strip())
        return _collapse(" ".join(part for part in parts if part))
    return ""


def _text_after_line(text: str, line_index: int) -> bytes:
    """The bytes after line ``line_index`` (0-based), leading blank lines stripped."""
    kept = text.splitlines(keepends=True)
    offset = sum(len(line) for line in kept[: line_index + 1])
    return text[offset:].lstrip("\r\n").encode("utf-8")


# ------------------------------------------------------------------- the R parser
def parse_requirements(text: str) -> list[RequirementEntry]:
    """Every ``## R<n> <title>`` block: title, the header line's 6 fields, statement."""
    lines = text.splitlines()
    entries: list[RequirementEntry] = []
    position = 0
    while position < len(lines):
        match = _HEADING_R_RE.match(lines[position])
        if match is None:
            position += 1
            continue
        entity_id, title = int(match.group(1)), match.group(2).strip()
        block: list[str] = []
        position += 1
        while position < len(lines) and not lines[position].startswith("## "):
            block.append(lines[position])
            position += 1
        entries.append(_requirement_block(entity_id, title, block))
    return entries


def _requirement_block(entity_id: int, title: str, block: list[str]) -> RequirementEntry:
    where = f"requirements.md R{entity_id}"
    header_at: Optional[int] = None
    entry = RequirementEntry(id=entity_id, title=title, status="", priority="",
                             phase=None)
    for position, line in enumerate(block):
        match = _BACKTICK_LINE_RE.match(line)
        if match is None:
            continue
        fields = [part.strip() for part in match.group(1).split("|")]
        if len(fields) != 6:
            raise store.StoreError(
                f"{where}: the header line needs 6 fields"
                " (status | priority | phase | features | specs | criteria),"
                f" got {len(fields)}")
        entry.status = _check_enum(fields[0], "requirement", "status", where)
        entry.priority = _check_enum(fields[1], "requirement", "priority", where)
        entry.phase = None if fields[2] in ("", "-") else fields[2]
        entry.features = _ids(fields[3], "F")
        entry.specs = _ids(fields[4], "S")
        entry.criteria = _ids(fields[5], "A")
        header_at = position
        break
    if header_at is None:
        raise store.StoreError(f"{where}: no backticked header line")
    entry.statement = _paragraph_after(block[header_at + 1:], _STATEMENT_RE)
    return entry


# ------------------------------------------------------------------- the A parser
def parse_acceptance(text: str) -> list[CriterionEntry]:
    """One criterion per table row ``| A<n> | R<m> | status | text |``."""
    entries: list[CriterionEntry] = []
    for line in text.splitlines():
        cells = _table_cells(line)
        if cells is None or len(cells) < 4:
            continue
        id_match = re.fullmatch(r"A(\d+)", cells[0])
        req_match = re.fullmatch(r"R(\d+)", cells[1])
        if id_match is None or req_match is None:
            continue
        status = _check_enum(cells[2], "criterion", "status", f"acceptance.md A{cells[0][1:]}")
        entries.append(CriterionEntry(int(id_match.group(1)), int(req_match.group(1)),
                                      status, cells[3]))
    return entries


# ------------------------------------------------------------------- the F parser
def parse_features(text: str) -> list[FeatureEntry]:
    """The first ``ID | Title | Layer | Status | Needs | Requirements`` table."""
    entries: list[FeatureEntry] = []
    in_table = False
    for line in text.splitlines():
        cells = _table_cells(line)
        if cells is None:
            if in_table:
                break                     # the first table ended
            continue
        if not in_table:
            if cells and cells[0] == "ID" and "Needs" in cells:
                in_table = True
            continue
        match = re.fullmatch(r"F(\d+)", cells[0])
        if match is None or len(cells) < 6:
            continue
        entity_id = int(match.group(1))
        where = f"features.md F{entity_id}"
        entries.append(FeatureEntry(
            id=entity_id, title=cells[1],
            layer=_check_enum(cells[2], "feature", "layer", where),
            status=_check_enum(cells[3], "feature", "status", where),
            needs=_ids(cells[4], "F"), requirements=_ids(cells[5], "R")))
    return entries


# ------------------------------------------------------------------- the S parser
def parse_spec(text: str) -> SpecEntry:
    """``# S<n> <title>``, the backticked header line, then the first prose paragraph."""
    lines = text.splitlines()
    match = _SPEC_TITLE_RE.match(lines[0].strip()) if lines else None
    if match is None:
        raise store.StoreError("specs: the first line must be '# S<n> <title>'")
    entity_id, title = int(match.group(1)), match.group(2).strip()
    where = f"specs/S{entity_id}"
    entry = SpecEntry(id=entity_id, title=title, status="draft")
    header_at: Optional[int] = None
    for position in range(1, len(lines)):
        header = _BACKTICK_LINE_RE.match(lines[position])
        if header is None:
            continue
        fields = [part.strip() for part in header.group(1).split("|")]
        entry.status = _check_enum(fields[0], "spec", "status", where)
        for field_text in fields[1:]:
            lowered = field_text.lower()
            if lowered.startswith("requirements"):
                entry.requirements = _ids(field_text[len("requirements"):], "R")
            elif lowered.startswith("features"):
                entry.features = _ids(field_text[len("features"):], "F")
        header_at = position
        break
    if header_at is None:
        raise store.StoreError(f"{where}: no backticked header line")
    entry.summary = _first_prose(lines[header_at + 1:]) or entry.title
    return entry


# ----------------------------------------------------------------- the DOC parser
def parse_supporting(text: str) -> DocEntry:
    """``# DOC<n> <title>``, the ``kind: ... | status: ... | links: ...`` header, body."""
    lines = text.splitlines()
    match = _DOC_TITLE_RE.match(lines[0].strip()) if lines else None
    if match is None:
        raise store.StoreError("supporting: the first line must be '# DOC<n> <title>'")
    entity_id, title = int(match.group(1)), match.group(2).strip()
    where = f"supporting DOC{entity_id}"
    fields: dict[str, str] = {}
    header_at: Optional[int] = None
    for position in range(1, len(lines)):
        header = _BACKTICK_LINE_RE.match(lines[position])
        if header is None:
            continue
        for part in header.group(1).split("|"):
            if ":" not in part:
                continue
            name, value = part.split(":", 1)
            fields[name.strip().lower()] = value.strip()
        header_at = position
        break
    if header_at is None:
        raise store.StoreError(f"{where}: no backticked header line")
    kind = _check_enum(fields.get("kind", "other"), "doc", "kind", where)
    status = _check_enum(fields.get("status", "current"), "doc", "status", where)
    links: list[tuple[str, int]] = []
    ignored: list[str] = []
    for token in re.split(r"[,\s]+", fields.get("links", "")):
        if not token or token == "-":
            continue
        ref = re.fullmatch(r"([RAFS])(\d+)", token)
        if ref is not None:
            links.append((KIND_BY_PREFIX[ref.group(1)], int(ref.group(2))))
        else:
            ignored.append(token)
    summary = (_paragraph_after(lines[header_at + 1:], _SUMMARY_RE)
               or _collapse(title))
    return DocEntry(id=entity_id, title=title, kind=kind, status=status, links=links,
                    ignored=ignored, note=fields.get("note") or None,
                    summary=summary[:model.DOC_SUMMARY_MAX],
                    body=_text_after_line(text, header_at), source="")


# ----------------------------------------------------------------- the PRD parser
def parse_prd(text: str, summary: str = "") -> DocEntry:
    """The PRD document: its heading (without ``PRD:``), kind prd, the whole file."""
    title = ""
    for line in text.splitlines():
        if line.startswith("# "):
            title = _collapse(re.sub(r"^PRD\s*:\s*", "", line[2:].strip()))
            break
    if not title:
        raise store.StoreError("PRD.md: no heading line")
    cleaned = _collapse(summary) or title
    return DocEntry(id=None, title=title, kind="prd", status="current",
                    summary=cleaned[:model.DOC_SUMMARY_MAX],
                    body=text.encode("utf-8"), source="PRD.md")


def prd_summary_from_index(index_text: str) -> str:
    """The summary column of the index's ``- [PRD](...) | status | summary`` line."""
    for line in index_text.splitlines():
        match = _PRD_LINE_RE.match(line)
        if match is not None:
            return match.group(2).strip()
    return ""


# ------------------------------------------------------------------- the registry
def parse_index(text: str) -> dict[str, dict]:
    """Every id index.md registers, with its status and title.

    Tables are read by their shape (the id prefix decides the columns); the
    acceptance section registers a whole range in prose (``A1 to A33, all
    `not_run` ``), which is read too. Only ``status``/``title`` are kept.
    """
    registry: dict[str, dict] = {}
    for line in text.splitlines():
        cells = _table_cells(line)
        if cells is None or not cells:
            continue
        match = _ROW_ID_RE.fullmatch(cells[0])
        if match is None:
            continue
        prefix, number = match.group(1), int(match.group(2))
        key = f"{prefix}{number}"
        if prefix == "R":
            status = cells[1] if len(cells) > 1 else None
            title = _cell_text(cells[4]) if len(cells) > 4 else (
                _cell_text(cells[2]) if len(cells) > 2 else None)
        elif prefix == "A":
            status = cells[2] if len(cells) > 3 else (cells[1] if len(cells) > 1 else None)
            title = None
        elif prefix in ("F", "S"):
            status = cells[1] if len(cells) > 1 else None
            title = _cell_text(cells[2]) if len(cells) > 2 else None
        else:                                   # DOC
            status = cells[2] if len(cells) > 2 else None
            title = _cell_text(cells[-1]) if len(cells) > 3 else None
        registry[key] = {"status": status, "title": title}
    for match in _A_RANGE_RE.finditer(text):
        first, last, status = int(match.group(1)), int(match.group(2)), match.group(3)
        for number in range(first, last + 1):
            registry.setdefault(f"A{number}", {"status": status, "title": None})
    return registry


# ------------------------------------------------------------------- the directory
def _read(path: Path, what: str) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        raise store.StoreError(f"{what}: {exc}") from exc


def build_documents(dir_path: Union[str, Path]) -> Documents:
    """Read a docs directory: the four main files, ``specs/``, ``supporting/``."""
    root = Path(dir_path).expanduser()
    if not root.is_dir():
        raise store.StoreError(f"{root}: not a directory")
    for name in ("requirements.md", "acceptance.md", "features.md", "index.md", "PRD.md"):
        if not (root / name).is_file():
            raise store.StoreError(f"{name}: missing (the docs directory holds it)")
    index_text = _read(root / "index.md", "index.md")
    requirements = parse_requirements(_read(root / "requirements.md", "requirements.md"))
    criteria = parse_acceptance(_read(root / "acceptance.md", "acceptance.md"))
    features = parse_features(_read(root / "features.md", "features.md"))
    specs: list[SpecEntry] = []
    for path in sorted((root / "specs").glob("S*-*.md")):
        spec = parse_spec(_read(path, f"specs/{path.name}"))
        spec.source = f"specs/{path.name}"
        specs.append(spec)
    specs.sort(key=lambda spec: spec.id)
    docs: list[DocEntry] = []
    for path in sorted((root / "supporting").glob("DOC*-*.md")):
        doc = parse_supporting(_read(path, f"supporting/{path.name}"))
        doc.source = f"supporting/{path.name}"
        docs.append(doc)
    docs.sort(key=lambda doc: doc.id or 0)
    prd = parse_prd(_read(root / "PRD.md", "PRD.md"), prd_summary_from_index(index_text))
    prd.id = max([doc.id or 0 for doc in docs] + [0]) + 1
    return Documents(requirements=requirements, criteria=criteria, features=features,
                     specs=specs, docs=[*docs, prd], index_text=index_text, prd=prd)


# ------------------------------------------------------------------ the cross-check
def cross_check(documents: Documents) -> list[str]:
    """A25: every id, reference and link agrees across the files and index.md.

    Returns ``problem: <file>: <what>`` lines, sorted. Any problem means nothing
    is imported: the documents disagree with each other and a guess would be
    silent data loss.
    """
    problems: list[str] = []
    registry = parse_index(documents.index_text)

    requirement_ids = {entry.id for entry in documents.requirements}
    criterion_ids = {entry.id for entry in documents.criteria}
    feature_ids = {entry.id for entry in documents.features}
    spec_ids = {entry.id for entry in documents.specs}
    doc_ids = {doc.id for doc in documents.docs if doc.id is not None}
    everything = {"requirement": requirement_ids, "criterion": criterion_ids,
                  "feature": feature_ids, "spec": spec_ids, "doc": doc_ids}

    # -- no id appears twice in the sources
    for kind, entries, filename in (
            ("requirement", documents.requirements, "requirements.md"),
            ("criterion", documents.criteria, "acceptance.md"),
            ("feature", documents.features, "features.md"),
            ("spec", documents.specs, "specs/"),
            ("doc", documents.docs, "supporting/")):
        seen: dict[int, None] = {}
        for entry in entries:
            if entry.id is None:
                continue
            if entry.id in seen:
                problems.append(
                    f"{entry.source or filename}: {PREFIX[kind]}{entry.id} appears twice")
            seen[entry.id] = None

    # -- every id in a file is registered, with the same status and title
    # (the PRD is registered by the index's `[PRD](PRD.md)` line, not by an id)
    for kind, entries in (("requirement", documents.requirements),
                          ("criterion", documents.criteria),
                          ("feature", documents.features),
                          ("spec", documents.specs),
                          ("doc", documents.docs)):
        for entry in entries:
            if entry.id is None or getattr(entry, "kind", None) == "prd":
                continue
            key = f"{PREFIX[kind]}{entry.id}"
            file = entry.source or KIND_FILES.get(kind, kind)
            known = registry.get(key)
            if known is None:
                problems.append(f"{file}: {key} is not in index.md")
                continue
            status = known.get("status")
            if status is not None and entry.status != status:
                problems.append(
                    f"{file}: {key} status is {entry.status} but index.md says {status}")
            title = known.get("title")
            entry_title = getattr(entry, "title", None)
            if title is not None and entry_title is not None and entry_title != title:
                problems.append(
                    f"{file}: {key} title is {entry_title!r} but index.md says {title!r}")

    # -- every index id exists in a file
    for key in sorted(registry):
        match = _ROW_ID_RE.fullmatch(key)
        if match is None:                     # pragma: no cover - parse_index only adds these
            continue
        kind = KIND_BY_PREFIX[match.group(1)]
        if int(match.group(2)) not in everything[kind]:
            problems.append(f"index.md: {key} is not in {KIND_FILES.get(kind, kind)}")

    # -- every reference in the headers, links and tables resolves
    for entry in documents.requirements:
        for feature_id in entry.features:
            if feature_id not in feature_ids:
                problems.append(
                    f"{entry.source}: R{entry.id} links F{feature_id} which does not exist")
        for spec_id in entry.specs:
            if spec_id not in spec_ids:
                problems.append(
                    f"{entry.source}: R{entry.id} links S{spec_id} which does not exist")
        for criterion_id in entry.criteria:
            if criterion_id not in criterion_ids:
                problems.append(
                    f"{entry.source}: R{entry.id} lists A{criterion_id} which does not exist")
    for entry in documents.criteria:
        if entry.requirement_id not in requirement_ids:
            problems.append(
                f"{entry.source}: A{entry.id} names R{entry.requirement_id}"
                " which does not exist")
    for entry in documents.features:
        for needs_id in entry.needs:
            if needs_id not in feature_ids:
                problems.append(
                    f"{entry.source}: F{entry.id} needs F{needs_id} which does not exist")
        for requirement_id in entry.requirements:
            if requirement_id not in requirement_ids:
                problems.append(
                    f"{entry.source}: F{entry.id} links R{requirement_id}"
                    " which does not exist")
    for entry in documents.specs:
        for requirement_id in entry.requirements:
            if requirement_id not in requirement_ids:
                problems.append(
                    f"{entry.source}: S{entry.id} links R{requirement_id}"
                    " which does not exist")
        for feature_id in entry.features:
            if feature_id not in feature_ids:
                problems.append(
                    f"{entry.source}: S{entry.id} links F{feature_id} which does not exist")
    for doc in documents.docs:
        for kind, entity_id in doc.links:
            if kind not in DOC_LINK_KINDS:
                continue
            if entity_id not in everything[kind]:
                problems.append(
                    f"{doc.source}: DOC{doc.id} links {PREFIX[kind]}{entity_id}"
                    " which does not exist")

    # -- the R header's criteria list is exactly acceptance.md's rows for it
    for entry in documents.requirements:
        actual = sorted(criterion.id for criterion in documents.criteria
                        if criterion.requirement_id == entry.id)
        if sorted(entry.criteria) != actual:
            problems.append(
                f"{entry.source}: R{entry.id} lists criteria {_fmt_ids(sorted(entry.criteria), 'A')}"
                f" but acceptance.md has {_fmt_ids(actual, 'A')}")

    # -- the R<->F and R<->S links agree between the two sources
    from_requirements_f = {(entry.id, feature_id) for entry in documents.requirements
                           for feature_id in entry.features}
    from_features = {(requirement_id, feature.id) for feature in documents.features
                     for requirement_id in feature.requirements}
    for requirement_id, feature_id in sorted(from_requirements_f - from_features):
        problems.append(
            f"requirements.md: R{requirement_id} links F{feature_id}"
            f" but features.md F{feature_id} does not link R{requirement_id}")
    for requirement_id, feature_id in sorted(from_features - from_requirements_f):
        problems.append(
            f"features.md: F{feature_id} links R{requirement_id}"
            f" but requirements.md R{requirement_id} does not link F{feature_id}")

    from_requirements_s = {(entry.id, spec_id) for entry in documents.requirements
                           for spec_id in entry.specs}
    from_specs = {(requirement_id, spec.id) for spec in documents.specs
                  for requirement_id in spec.requirements}
    spec_sources = {spec.id: spec.source or f"specs/S{spec.id}" for spec in documents.specs}
    for requirement_id, spec_id in sorted(from_requirements_s - from_specs):
        problems.append(
            f"requirements.md: R{requirement_id} links S{spec_id}"
            f" but {spec_sources[spec_id]} does not link R{requirement_id}")
    for requirement_id, spec_id in sorted(from_specs - from_requirements_s):
        problems.append(
            f"{spec_sources[spec_id]}: S{spec_id} links R{requirement_id}"
            f" but requirements.md R{requirement_id} does not link S{spec_id}")

    return sorted({f"problem: {problem}" for problem in problems})


# ---------------------------------------------------------------- staged S3 export
def _yaml_bytes(structure: Any) -> bytes:
    """Deterministic YAML (no folding), parsed back by ``export.load_export``."""
    buffer = io.StringIO()
    yaml = YAML()
    yaml.width = 10 ** 6
    yaml.dump(structure, buffer)
    return buffer.getvalue().encode("utf-8")


def to_export(documents: Documents, project: str) -> tuple[dict[str, bytes],
                                                           dict[str, bytes]]:
    """The parsed documents as the S3 export layout: ``(files, docs bodies)``."""
    requirement_rows = []
    for entry in documents.requirements:
        row: dict[str, Any] = {"id": entry.id, "title": entry.title,
                               "statement": entry.statement, "priority": entry.priority}
        if entry.phase is not None:
            row["phase"] = entry.phase
        row["status"] = entry.status
        row["blocked"] = False
        row["legacy"] = False
        requirement_rows.append(row)
    criterion_rows = [{"id": entry.id, "requirement_id": entry.requirement_id,
                       "text": entry.text, "status": entry.status}
                      for entry in documents.criteria]
    feature_rows = [{"id": entry.id, "title": entry.title, "layer": entry.layer,
                     "status": entry.status, "infra": False, "blocked": False}
                    for entry in documents.features]
    spec_rows = [{"id": entry.id, "title": entry.title, "summary": entry.summary,
                  "status": entry.status, "blocked": False}
                 for entry in documents.specs]

    bodies: dict[str, bytes] = {}
    doc_rows = []
    for doc in documents.docs:
        assert doc.id is not None
        name = f"DOC{doc.id}-{export.doc_slugify(doc.title)}.md"
        bodies[name] = doc.body
        row = {"id": doc.id, "title": doc.title, "kind": doc.kind,
               "summary": doc.summary, "body": name, "status": doc.status}
        if doc.note:
            row["notes"] = doc.note
        doc_rows.append(row)

    feature_requirement = sorted(
        {(feature_id, entry.id) for entry in documents.requirements
         for feature_id in entry.features}
        | {(feature.id, requirement_id) for feature in documents.features
           for requirement_id in feature.requirements})
    spec_requirement = sorted(
        {(spec_id, entry.id) for entry in documents.requirements
         for spec_id in entry.specs}
        | {(spec.id, requirement_id) for spec in documents.specs
           for requirement_id in spec.requirements})
    feature_spec = sorted({(feature_id, spec.id) for spec in documents.specs
                           for feature_id in spec.features})
    feature_dependency = sorted({(feature.id, needs_id) for feature in documents.features
                                 for needs_id in feature.needs})
    doc_link = sorted({(doc.id, kind, entity_id) for doc in documents.docs
                       for kind, entity_id in doc.links if doc.id is not None})
    links = {
        "feature_requirement": [[feature_id, requirement_id]
                                for feature_id, requirement_id in feature_requirement],
        "spec_requirement": [[spec_id, requirement_id]
                             for spec_id, requirement_id in spec_requirement],
        "feature_spec": [[feature_id, spec_id] for feature_id, spec_id in feature_spec],
        "feature_dependency": [[feature_id, needs_id, False]
                               for feature_id, needs_id in feature_dependency],
        "requirement_dependency": [],
        "spec_dependency": [],
        "doc_link": [[doc_id, kind, entity_id] for doc_id, kind, entity_id in doc_link],
        "external_link": [],
    }

    counters = {}
    for prefix, entries in (
            ("R", requirement_rows), ("A", criterion_rows), ("F", feature_rows),
            ("S", spec_rows), ("DOC", doc_rows)):
        highest = max((row["id"] for row in entries), default=0)
        counters[prefix] = highest + 1
    project_row = store.get_project(project) or {}
    project_doc = {"slug": project, "name": project_row.get("name") or project,
                   "counters": counters}
    if project_row.get("root") is not None:
        project_doc["root"] = project_row["root"]

    files = {
        "project.yaml": _yaml_bytes(project_doc),
        "requirements.yaml": _yaml_bytes(requirement_rows),
        "criteria.yaml": _yaml_bytes(criterion_rows),
        "features.yaml": _yaml_bytes(feature_rows),
        "specs.yaml": _yaml_bytes(spec_rows),
        "docs.yaml": _yaml_bytes(doc_rows),
        "links.yaml": _yaml_bytes(links),
        "history.yaml": _yaml_bytes([]),
    }
    return files, bodies


def _write_stage(directory: Path, files: dict[str, bytes],
                 bodies: dict[str, bytes]) -> None:
    for name, payload in files.items():
        (directory / name).write_bytes(payload)
    (directory / "docs").mkdir()
    for name, payload in bodies.items():
        (directory / "docs" / name).write_bytes(payload)


# ---------------------------------------------------------------------- the verb
def notes(documents: Documents) -> list[str]:
    """One line per link token a DOC names that is not an entry (reported, not fatal)."""
    return [f"note: DOC{doc.id} link {token} is not an entry"
            for doc in documents.docs for token in doc.ignored]


def import_markdown(dir_path: Union[str, Path], project: str, *,
                    dry_run: bool = False) -> dict:
    """Import the docs directory into an EXISTING project through the S3 importer.

    Returns the counts of every kind plus ``problems`` (A25, nothing imported),
    ``notes`` and, unless ``dry_run``, the importer's ``inserted``/``skipped``/
    ``conflicts``/``conflict_lines``.
    """
    if not store.project_exists(project):
        raise store.StoreError(
            f"project {project} does not exist"
            f" (create it with: project add {project} <name> --root DIR)")
    documents = build_documents(dir_path)
    problems = cross_check(documents)
    result: dict[str, Any] = {**documents.counts(), "problems": problems,
                              "notes": notes(documents), "dry_run": dry_run,
                              "inserted": 0, "skipped": 0, "conflicts": 0,
                              "conflict_lines": []}
    if problems or dry_run:
        return result
    files, bodies = to_export(documents, project)
    stage = Path(tempfile.mkdtemp(prefix="product-import-md-"))
    try:
        _write_stage(stage, files, bodies)
        imported = export.import_export(stage, as_slug=project)
    finally:
        shutil.rmtree(stage, ignore_errors=True)
    result.update({"inserted": imported["inserted"], "skipped": imported["skipped"],
                   "conflicts": imported["conflicts"],
                   "conflict_lines": imported["conflict_lines"]})
    return result
