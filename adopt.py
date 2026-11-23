"""``adopt DIR``: bring an existing repository's markdown into a product project (DOC5 2.1).

The first real adoption (go-mm-driver) took about 300 CLI calls, a throwaway importer and
12 minutes because no verb could take a repository that was never written in this
plugin's dialect. ``adopt`` is that verb. It is deterministic (no model call): it walks
``DIR`` for Markdown, decides by path and name what each file is, proposes one row per
file with the file text as its body, prints the proposal and writes only with ``--apply``.

What it proposes (and what it will not invent):

* a spec file (under a ``specs`` directory, or named ``S<n>-...``) becomes an **S#** whose
  ``body`` is the file text;
* a PRD / requirements file becomes the project's ``prd`` **DOC** (``context`` when the
  project already has one); runbooks, design/architecture/plan files, findings and
  reports, API notes and anything else become DOCs of the matching kind, body = the text;
* README, CHANGELOG, LICENSE, AGENTS, CLAUDE, CONTRIBUTING and empty or oversized files
  are skipped and listed;
* **no requirements, features or links**: those are decisions, not file contents. The row
  count of R#/F# stays 0 and the closing line says what to link next.

The one-line ``summary`` is the first prose paragraph (metadata lines such as ``Status:``,
``Repo:``, ``TIER:`` skipped), cut to at most two sentences / 400 characters. When a file has
no such paragraph the summary is ``Imported from <path>``, which the ``thin_summary`` lint
counts, so a hollow row never looks migrated.

Idempotent: each created row carries ``adopted: <relative path>`` in ``notes``; a rerun
finds it and reports ``exists`` (text unchanged) or ``changed`` (text differs; ``--update``
refreshes the body). Nothing is ever deleted.
"""
from __future__ import annotations

import fnmatch
import hashlib
import os
import re
import tempfile
from pathlib import Path
from typing import Any, Optional

try:  # package submodule (Hermes plugin loader)
    from . import lint, model, store
except ImportError:  # top-level module (test suite)
    import lint  # type: ignore[no-redef]
    import model  # type: ignore[no-redef]
    import store  # type: ignore[no-redef]

MARKER = "adopted: "
MAX_BYTES = 1_000_000
#: dependency, build and run-output directories: generated files are not product text
SKIP_DIRS = {"node_modules", "vendor", "dist", "build", "__pycache__", "venv", "runs", "run",
             "output", "outputs", "artifacts", "tmp", "logs", "coverage"}
SKIP_NAMES = ("readme", "changelog", "license", "licence", "agents", "claude", "contributing",
              "code_of_conduct", "code-of-conduct", "security", "notice", "authors")

_SPEC_DIRS = {"specs", "spec"}
_SPEC_NAME = re.compile(r"^(s\d+|rec\d+)[-_ .]", re.IGNORECASE)
#: filename keyword -> DOC kind, first match wins (order matters)
_KIND_RULES = (
    (re.compile(r"\b(prd|product[-_ ]requirements|requirements)\b"), "prd"),
    (re.compile(r"(runbook|deploy|deployment|pre-deploy|operations|\bops\b)"), "runbook"),
    (re.compile(r"(findings|results|defects|report|audit|progress|acceptance)"), "report"),
    (re.compile(r"\b(review|reviews|ux)\b"), "design"),
    (re.compile(r"(architecture|design|\bplan\b|policy|adaptive|quoting|budget)"), "design"),
    (re.compile(r"(\bapi\b|reference|openapi)"), "api"),
)
_METADATA_LINE = re.compile(
    r"^\s*[*_>\-\s]*(status|tier|repo|owner|card|cards|version|date|updated|created|author|"
    r"parent|read first|study|depends|supersedes)\b[^:\n]{0,30}[:\uff1a]", re.IGNORECASE)
_HEADING = re.compile(r"^#{1,6}\s")
_FENCE = ("```", "~~~")


# ----------------------------------------------------------------------- pure pieces
def classify(rel: str, *, has_prd: bool = False) -> tuple[str, Optional[str], str]:
    """``(target, doc_kind, why)``: target ``"S"``, ``"DOC"`` or ``"skip"`` for one path."""
    path = Path(rel)
    stem = path.stem.lower()
    if any(stem == name or stem.startswith(name + "-") or stem.startswith(name + "_")
           for name in SKIP_NAMES):
        return "skip", None, "not product text (readme, license, agent notes, changelog)"
    parts = [part.lower() for part in path.parts[:-1]]
    if any(part in _SPEC_DIRS for part in parts) or _SPEC_NAME.match(path.name):
        return "S", None, "spec"
    lowered = stem.replace("_", "-")
    for pattern, kind in _KIND_RULES:
        if pattern.search(lowered) or (kind == "design" and any(p == "reviews" for p in parts)):
            if kind == "prd" and has_prd:
                return "DOC", "context", "second PRD-like file: kept as context"
            return "DOC", kind, f"{kind} by file name"
    if any(p in ("reviews", "review") for p in parts):
        return "DOC", "design", "design by directory"
    return "DOC", "context", "context (no spec or kind hint in the path)"


def _clean_inline(text: str) -> str:
    text = re.sub(r"`([^`]*)`", r"\1", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"[*_]{1,3}([^*_]+)[*_]{1,3}", r"\1", text)
    return " ".join(text.split())


def propose_title(text: str, rel: str) -> str:
    """The first H1 (markup removed), else the file name humanised; always 3-80 characters."""
    title = ""
    fence: Optional[str] = None
    for line in text.splitlines():
        stripped = line.strip()
        marker = next((f for f in _FENCE if stripped.startswith(f)), None)
        if fence is not None:
            fence = None if marker == fence else fence
            continue
        if marker:
            fence = marker
            continue
        if line.startswith("# "):
            title = _clean_inline(line[2:])
            break
    if len(title) < 3:
        title = Path(rel).stem.replace("-", " ").replace("_", " ").strip()
    if len(title) < 3:
        title = f"{title} doc".strip()
    return title if len(title) <= 80 else title[:79].rstrip() + "\u2026"


_SENTENCE_END = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9`\"(])")


def propose_summary(text: str, rel: str) -> str:
    """One or two sentences (at most 400 chars): the first real prose paragraph of ``text``."""
    paragraph: list[str] = []
    fence: Optional[str] = None
    in_comment = False
    for line in text.splitlines():
        stripped = line.strip()
        marker = next((f for f in _FENCE if stripped.startswith(f)), None)
        if fence is not None:
            fence = None if marker == fence else fence
            continue
        if marker:
            fence = marker
            continue
        if "<!--" in stripped:
            in_comment = "-->" not in stripped
            continue
        if in_comment:
            in_comment = "-->" not in stripped
            continue
        if (not stripped or _HEADING.match(stripped) or stripped.startswith(("|", "---", "===", "- [", "* ["))
                or _METADATA_LINE.match(line)):
            if len(" ".join(paragraph).split()) >= 8:
                break
            paragraph = []
            continue
        if stripped.startswith(("- ", "* ", "+ ")) or re.match(r"\d+[.)]\s", stripped):
            if len(" ".join(paragraph).split()) >= 8:
                break
            paragraph = []
            continue
        paragraph.append(stripped)
    flat = _clean_inline(" ".join(paragraph))
    if len(flat.split()) < 8:
        return f"Imported from {rel}"
    sentences = _SENTENCE_END.split(flat)
    summary = sentences[0]
    if len(sentences) > 1 and len(summary) + 1 + len(sentences[1]) <= model.DOC_SUMMARY_MAX:
        summary = f"{summary} {sentences[1]}"
    if len(summary) > model.DOC_SUMMARY_MAX:
        cut = summary[: model.DOC_SUMMARY_MAX - 1]
        summary = cut[: cut.rfind(" ")].rstrip(" ,;:") + "\u2026"
    return summary


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------- scan
def scan_directory(root: Path) -> list[Path]:
    """Every ``*.md`` under ``root`` except hidden and vendor directories, sorted by path."""
    found: list[Path] = []
    for current, dirs, files in os.walk(root):
        dirs[:] = sorted(d for d in dirs if not d.startswith(".") and d not in SKIP_DIRS)
        for name in sorted(files):
            if name.lower().endswith(".md") and not name.startswith("."):
                found.append(Path(current) / name)
    return sorted(found)


def _existing(con, project: str) -> dict[str, tuple[str, int, Optional[str]]]:
    """``marker path -> (kind, id, body digest)`` for rows adopted earlier."""
    rows: dict[str, tuple[str, int, Optional[str]]] = {}
    for row in con.execute("SELECT id, notes, body FROM spec WHERE project = ? AND notes LIKE ?",
                           (project, MARKER + "%")):
        rows[row["notes"][len(MARKER):].strip()] = ("spec", row["id"],
                                                    _digest(row["body"]) if row["body"] else None)
    for row in con.execute("SELECT id, notes, body_file FROM doc WHERE project = ? AND notes LIKE ?",
                           (project, MARKER + "%")):
        digest = None
        if row["body_file"]:
            path = store.doc_body_path(project, dict(row))
            digest = _digest(path.read_text(encoding="utf-8")) if path.is_file() else None
        rows[row["notes"][len(MARKER):].strip()] = ("doc", row["id"], digest)
    return rows


def plan(project: str, root: str | Path, con=None, only: Optional[list[str]] = None) -> dict:
    """Propose rows for ``root``: ``{"rows": [...], "skipped": [...]}``; writes nothing.

    ``only`` limits the pass to files whose relative path matches one of the glob
    patterns (``docs/ARCHITECTURE.md``, ``docs/*.md``); the rest are not even listed.
    """
    base = Path(root).expanduser().resolve()
    if not base.is_dir():
        raise store.StoreError(f"adopt: {base} is not a directory")
    own = con is None
    con = con or store.connect()
    try:
        if not store.project_exists(project, con):
            raise store.StoreError(f"project {project!r} does not exist")
        already = _existing(con, project)
        prd_notes = [row["notes"] for row in con.execute(
            "SELECT notes FROM doc WHERE project = ? AND kind = 'prd' AND status = 'current'",
            (project,))]
        planned_prd: Optional[str] = None            # the file this pass will make the PRD
        rows: list[dict] = []
        skipped: list[dict] = []
        for path in scan_directory(base):
            rel = path.relative_to(base).as_posix()
            if only and not any(fnmatch.fnmatchcase(rel, pattern) for pattern in only):
                continue
            # a PRD this very file created on an earlier run is not "another PRD"
            has_prd = (any(note != MARKER + rel for note in prd_notes)
                       or (planned_prd is not None and planned_prd != rel))
            target, doc_kind, why = classify(rel, has_prd=has_prd)
            if target == "skip":
                skipped.append({"path": rel, "reason": why})
                continue
            if path.stat().st_size > MAX_BYTES:
                skipped.append({"path": rel, "reason": f"larger than {MAX_BYTES} bytes"})
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                skipped.append({"path": rel, "reason": "not readable as UTF-8 text"})
                continue
            if not text.strip():
                skipped.append({"path": rel, "reason": "empty"})
                continue
            if doc_kind == "prd":
                planned_prd = rel
            summary = propose_summary(text, rel)
            row = {
                "path": rel, "target": target, "doc_kind": doc_kind, "why": why,
                "title": propose_title(text, rel), "summary": summary,
                "thin": lint.thin_reason(propose_title(text, rel), summary) is not None,
                "chars": len(text), "text": text,
            }
            known = already.get(rel)
            if known is None:
                row["action"] = "new"
            else:
                row["ref"] = model.format_ref(known[0], known[1])
                row["action"] = "exists" if known[2] == _digest(text) else "changed"
            rows.append(row)
        return {"rows": rows, "skipped": skipped, "root": str(base)}
    finally:
        if own:
            con.close()


def apply(project: str, found: dict, *, update: bool = False, con=None) -> dict:
    """Write the ``new`` rows (and, with ``update``, refresh ``changed`` bodies).

    Each row is its own transaction; a refusal on one row is recorded and the rest go on.
    Returns the counts and the per-row outcome (``ref``, ``error``).
    """
    own = con is None
    con = con or store.connect()
    counts = {"created": 0, "updated": 0, "skipped": 0, "failed": 0}
    try:
        for row in found["rows"]:
            row.pop("error", None)
            try:
                if row["action"] == "new":
                    fields = {"title": row["title"], "summary": row["summary"],
                              "notes": MARKER + row["path"]}
                    if row["target"] == "S":
                        fields["body"] = row["text"]
                        made = store.create_entity(project, "spec", fields, con=con)
                    else:
                        fields.update(kind=row["doc_kind"], body=row["text"])
                        made = store.create_entity(project, "doc", fields, con=con)
                    row["ref"] = made["ref"]
                    row["applied"] = "created"
                    counts["created"] += 1
                elif row["action"] == "changed" and update:
                    if row["target"] == "S":
                        store.update_entity(project, row["ref"], {"body": row["text"]},
                                            reason="adopt --update", con=con)
                    else:
                        with tempfile.TemporaryDirectory() as tmp:
                            source = Path(tmp) / "body.md"
                            source.write_text(row["text"], encoding="utf-8")
                            store.update_entity(project, row["ref"], {}, body_source=str(source),
                                                reason="adopt --update", con=con)
                    row["applied"] = "updated"
                    counts["updated"] += 1
                else:
                    row["applied"] = "skipped"
                    counts["skipped"] += 1
            except (store.StoreError, model.ValidationError) as exc:
                row["applied"] = "failed"
                row["error"] = str(exc)
                counts["failed"] += 1
        return counts
    finally:
        if own:
            con.close()


# ------------------------------------------------------------------------- mentions
def _mentions(text: str, rel: str) -> bool:
    """Does ``text`` name the file: its relative path, or its file name as a whole token?"""
    if not text:
        return False
    name = Path(rel).name
    for needle in {rel, name}:
        start = 0
        while True:
            at = text.find(needle, start)
            if at < 0:
                break
            before = text[at - 1] if at > 0 else " "
            if not (before.isalnum() or before in "_-."):
                return True
            start = at + 1
    return False


def link_mentions(project: str, rows: list[dict], con=None) -> dict:
    """Link each adopted DOC to every live R#/F#/S# whose summary, notes or body names its file.

    A path written in an entry's text is a reference; registering the document without
    the link leaves the entry pointing at a file nobody can follow from the board. Existing
    links are kept and counted, retired entries are skipped, nothing is ever unlinked.
    Returns ``{"linked": [(doc, entry)...], "existing": n, "refused": [(doc, entry, why)...]}``.
    """
    own = con is None
    con = con or store.connect()
    out: dict[str, Any] = {"linked": [], "existing": 0, "refused": []}
    try:
        entries: list[tuple[str, str]] = []
        for table, prefix, retired, one_line in (("requirement", "R", "dropped", "statement"),
                                                 ("feature", "F", "cut", "description"),
                                                 ("spec", "S", "superseded", "summary")):
            for row in con.execute(
                    f"SELECT id, status, {one_line} AS line, notes, body FROM {table}"
                    " WHERE project = ? ORDER BY id", (project,)):
                if row["status"] == retired:
                    continue
                text = "\n".join(str(row[key] or "") for key in ("line", "notes", "body"))
                entries.append((f"{prefix}{row['id']}", text))
        for row in rows:
            doc = row.get("ref")
            if not doc or not str(doc).startswith("DOC"):
                continue
            already = {(kind, ident) for kind, ident in store.doc_link_refs(con, project,
                                                                          int(doc[3:]))}
            for ref, text in entries:
                if not _mentions(text, row["path"]):
                    continue
                kind, ident = model.parse_ref(ref)
                if (kind, ident) in already:
                    out["existing"] += 1
                    continue
                try:
                    store.link_entries(project, doc, ref, con=con)
                    out["linked"].append((doc, ref))
                except (store.StoreError, model.ValidationError) as exc:
                    out["refused"].append((doc, ref, str(exc)))
        return out
    finally:
        if own:
            con.close()


# ---------------------------------------------------------------------------- output
def _kb(chars: int) -> str:
    return f"{chars / 1024:.1f} KB"


def render(project: str, found: dict, *, counts: Optional[dict] = None, update: bool = False) -> list[str]:
    """One line per file (``+`` new, ``=`` exists, ``~`` changed), the skipped, a total."""
    lines: list[str] = []
    mark = {"new": "+", "exists": "=", "changed": "~"}
    for row in found["rows"]:
        label = "S" if row["target"] == "S" else f"DOC {row['doc_kind']}"
        ref = f" {row['ref']}" if row.get("ref") else ""
        applied = f" -> {row['applied']}" if row.get("applied") and row["applied"] != "skipped" else ""
        thin = "  [summary needs a human]" if row["thin"] and row["action"] == "new" else ""
        lines.append(f"{mark[row['action']]} {label:<12} {row['path']}  \"{row['title']}\""
                     f"  {_kb(row['chars'])}{ref}{applied}{thin}")
        if row.get("error"):
            lines.append(f"  ! {row['error']}")
    for entry in found["skipped"]:
        lines.append(f"- skip {entry['path']}  {entry['reason']}")
    tally = {name: sum(1 for r in found["rows"] if r["action"] == name)
             for name in ("new", "exists", "changed")}
    if counts is None:
        lines.append(
            f"adopt {project} from {found['root']} (plan): {tally['new']} new, {tally['exists']}"
            f" unchanged, {tally['changed']} changed on disk, {len(found['skipped'])} skipped;"
            " apply with --apply" + (" (--update refreshes changed bodies)" if tally["changed"] else ""))
    else:
        lines.append(
            f"adopted {project}: created {counts['created']}, updated {counts['updated']},"
            f" skipped {counts['skipped'] + len(found['skipped'])}, failed {counts['failed']}")
        linked = found.get("linked")
        if linked is not None:
            lines.append(f"linked {len(linked['linked'])} mentions ({linked['existing']} already linked,"
                         f" {len(linked['refused'])} refused)")
            for doc, ref in linked["linked"]:
                lines.append(f"  + {doc} <-> {ref}")
            for doc, ref, why in linked["refused"]:
                lines.append(f"  ! {doc} <-> {ref}: {why}")
        if counts["created"]:
            lines.append("next: link each new S# to its requirement (`link S<n> R<n>`) and feature;"
                         " rows marked [summary needs a human] still carry a placeholder summary")
    return lines
