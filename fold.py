"""``fold-docs``: move spec DOC# text onto the S# it defines (DOC5 section 7).

Schema v1 stored a spec's text as a DOC of kind ``spec`` linked to the S# row. Schema
v2 keeps that text on the row (``spec.body``, S1 section 3.1) and reserves DOC for
context. This module is the one-time bridge for a store that already holds the old
shape. It plans first (nothing written) and applies only on request.

Rules (owner rulings of 2026-11-23, recorded in DOC5 section 7):

* A DOC is **foldable** when it is kind ``spec``, ``current``, has a stored body file
  and is linked to exactly one entry, a spec; it is not named by another DOC's
  ``superseded_by`` and carries no external link. Everything else is left alone and
  reported with its reason (a DOC cited by more than one entry is context, not text
  that defines one spec).
* A spec that already has a body is never overwritten: its DOCs stay.
* One foldable DOC -> the S# body is that text, byte for byte.
* Several foldable DOCs -> one body: for each DOC in id order ``## <doc title>``, a
  blank line, then the DOC text with every Markdown heading (outside code fences)
  pushed two levels down (clamped at level 6), so the DOC's own ``# H1`` becomes a
  ``###`` inside its ``##`` section and ``show --section`` reads either one.
* After the copy the DOC row, its links and its body file are deleted; ids are never
  reused (counters are untouched) and the fold is logged on the S# history as
  ``body folded from DOC<a>, DOC<b>``. The whole fold is ONE transaction and the body
  files are removed only after it commits.
"""
from __future__ import annotations

import re
import sqlite3
from typing import Any, Optional

try:  # package submodule (Hermes plugin loader)
    from . import model, store
except ImportError:  # top-level module (test suite)
    import model  # type: ignore[no-redef]
    import store  # type: ignore[no-redef]

_HEADING_RE = re.compile(r"^(#{1,6})([ \t]+\S.*)$")
_FENCES = ("```", "~~~")


def demote_headings(text: str, by: int) -> str:
    """``text`` with every ATX heading outside a code fence pushed ``by`` levels down.

    Levels clamp at 6. Fenced blocks (``` or ~~~, closed by the same marker) are copied
    untouched, so a ``# comment`` in a shell block never becomes a heading.
    """
    out: list[str] = []
    fence: Optional[str] = None
    for line in text.splitlines(keepends=True):
        stripped = line.strip()
        marker = next((candidate for candidate in _FENCES if stripped.startswith(candidate)),
                      None)
        if fence is not None:
            if marker == fence:
                fence = None
            out.append(line)
            continue
        if marker is not None:
            fence = marker
            out.append(line)
            continue
        body = line.rstrip("\r\n")
        match = _HEADING_RE.match(body)
        if match is None:
            out.append(line)
            continue
        level = min(len(match.group(1)) + by, 6)
        out.append("#" * level + match.group(2) + line[len(body):])
    return "".join(out)


def compose_body(docs: list[dict]) -> str:
    """The S# body for its foldable DOCs (each dict: ``title``, ``text``), id order."""
    if len(docs) == 1:
        return docs[0]["text"]
    parts = []
    for doc in docs:
        text = demote_headings(doc["text"], 2)
        if not text.endswith("\n"):
            text += "\n"
        parts.append(f"## {doc['title']}\n\n{text}")
    return "\n".join(parts)


def plan(project: str, con: Optional[sqlite3.Connection] = None) -> dict:
    """What a fold would do: ``{"items": [...], "skipped": [...]}``; writes nothing.

    Each item: ``spec`` (id), ``docs`` (id, title, chars, in id order), ``text`` (the
    composed body). Each skipped entry: ``doc`` (id), ``reason``; an S# left alone
    because it already has a body is one skipped entry per DOC.
    """
    own = con is None
    con = con or store.connect()
    try:
        if not store.project_exists(project, con):
            raise store.StoreError(f"project {project!r} does not exist")
        docs = [dict(row) for row in con.execute(
            "SELECT * FROM doc WHERE project = ? AND kind = 'spec' ORDER BY id", (project,))]
        links: dict[int, list[tuple[str, int]]] = {}
        for row in con.execute(
                "SELECT doc_id, entity_type, entity_id FROM doc_link WHERE project = ?"
                " ORDER BY doc_id", (project,)):
            links.setdefault(row["doc_id"], []).append((row["entity_type"], row["entity_id"]))
        superseding = {row["superseded_by"] for row in con.execute(
            "SELECT superseded_by FROM doc WHERE project = ? AND superseded_by IS NOT NULL",
            (project,))}
        external = {row["entity_id"] for row in con.execute(
            "SELECT entity_id FROM external_link WHERE project = ? AND entity_type = 'doc'",
            (project,))}
        spec_body = {row["id"]: row["body"] for row in con.execute(
            "SELECT id, body FROM spec WHERE project = ?", (project,))}

        skipped: list[dict] = []
        groups: dict[int, list[dict]] = {}
        for doc in docs:
            reason = _refusal(doc, links.get(doc["id"], []), superseding, external)
            text = None
            if reason is None:
                path = store.doc_body_path(project, doc)
                if not path.is_file():
                    reason = f"body file is missing ({doc['body_file']})"
                else:
                    text = path.read_text(encoding="utf-8")
                    if not text.strip():
                        reason = "body is empty"
            if reason is not None:
                skipped.append({"doc": doc["id"], "reason": reason})
                continue
            spec_id = links[doc["id"]][0][1]
            groups.setdefault(spec_id, []).append(
                {"id": doc["id"], "title": doc["title"], "text": text})

        items: list[dict] = []
        for spec_id in sorted(groups):
            members = groups[spec_id]
            if spec_body.get(spec_id):
                for member in members:
                    skipped.append({"doc": member["id"],
                                    "reason": f"S{spec_id} already has a body (never overwritten)"})
                continue
            items.append({
                "spec": spec_id,
                "docs": [{"id": m["id"], "title": m["title"], "chars": len(m["text"])}
                         for m in members],
                "text": compose_body(members),
            })
        skipped.sort(key=lambda entry: entry["doc"])
        return {"items": items, "skipped": skipped}
    finally:
        if own:
            con.close()


def _refusal(doc: dict, doc_links: list[tuple[str, int]], superseding: set,
             external: set) -> Optional[str]:
    if doc["status"] != "current":
        return f"status is {doc['status']}"
    if not doc["body_file"]:
        return "no stored body (body_ref only)"
    if not doc_links:
        return "linked to nothing"
    if len(doc_links) > 1:
        return f"linked to {len(doc_links)} entries (context, not one spec's text)"
    if doc_links[0][0] != "spec":
        return f"linked to a {doc_links[0][0]}, not a spec"
    if doc["id"] in superseding:
        return "another DOC names it in superseded_by"
    if doc["id"] in external:
        return "has an external link"
    return None


def apply(project: str, con: Optional[sqlite3.Connection] = None) -> dict:
    """Run the fold: ``{"specs": n, "documents": m, "skipped": k}``. One transaction."""
    own = con is None
    con = con or store.connect()
    try:
        found = plan(project, con)
        paths = []
        now = store._now()
        with con:
            for item in found["items"]:
                refs = ", ".join(f"DOC{doc['id']}" for doc in item["docs"])
                con.execute("UPDATE spec SET body = ?, updated_at = ? WHERE project = ? AND id = ?",
                            (item["text"], now, project, item["spec"]))
                con.execute(
                    "INSERT INTO history(project, entity_type, entity_id, change, reason, at)"
                    " VALUES (?,?,?,?,?,?)",
                    (project, "spec", item["spec"], f"body folded from {refs}", None, now))
                for doc in item["docs"]:
                    row = con.execute("SELECT * FROM doc WHERE project = ? AND id = ?",
                                      (project, doc["id"])).fetchone()
                    paths.append(store.doc_body_path(project, dict(row)))
                    con.execute("DELETE FROM doc_link WHERE project = ? AND doc_id = ?",
                                (project, doc["id"]))
                    con.execute("DELETE FROM doc WHERE project = ? AND id = ?",
                                (project, doc["id"]))
        for path in paths:                      # only after the commit: a failure keeps the files
            path.unlink(missing_ok=True)
        return {"specs": len(found["items"]),
                "documents": sum(len(item["docs"]) for item in found["items"]),
                "skipped": len(found["skipped"])}
    finally:
        if own:
            con.close()


def render(project: str, found: dict, *, applied: Optional[dict] = None) -> list[str]:
    """The compact lines `fold-docs` prints: one per spec, one per skipped DOC, a total."""
    lines: list[str] = []
    for item in found["items"]:
        docs = item["docs"]
        names = ",".join(f"DOC{doc['id']}" for doc in docs)
        how = "verbatim" if len(docs) == 1 else f"{len(docs)} sections"
        lines.append(f"S{item['spec']}  <- {names}  {len(item['text'])} chars  {how}")
    for entry in found["skipped"]:
        lines.append(f"skip DOC{entry['doc']}  {entry['reason']}")
    total_docs = sum(len(item["docs"]) for item in found["items"])
    if applied is not None:
        lines.append(f"folded {applied['documents']} documents into {applied['specs']}"
                     f" spec bodies in {project} ({applied['skipped']} skipped)")
    elif found["items"]:
        lines.append(f"fold-docs {project} (dry run): {total_docs} documents into"
                     f" {len(found['items'])} spec bodies, {len(found['skipped'])} skipped;"
                     " apply with --apply")
    else:
        lines.append(f"fold-docs {project}: nothing to fold ({len(found['skipped'])} skipped)")
    return lines
