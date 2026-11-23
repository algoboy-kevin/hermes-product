"""PRD/DOC reference scanning, pure (card HP-8a).

No sqlite, no store, no network: the caller reads a document's text, hands it in,
and supplies a resolver callback for what an id means. Four jobs:

  * ``find_references`` — every ``[R3]`` / ``[A21]`` / ``[F7]`` / ``[S12]`` /
    ``[DOC4]`` / ``[F:slug]`` mention, in document order, with line and column.
    Fenced code blocks (``` or ~~~), inline code spans (backticks) and escaped
    brackets (``\\[R3]``) are not references; a Markdown link whose text is a
    reference is; a bracket group holding several refs (``[R1, R2]``) is not.
  * ``check_references`` / ``scan_text`` — the resolver says ``"ok"``,
    ``"unknown"`` or ``"retired"``; ok refs stay silent and resolver errors
    propagate untouched (the module never guesses).
  * ``format_problem`` — the one-line lint rendering, exact spacing.
  * ``anchor_ids`` / ``prd_anchor_ok`` — ``PRD.md#anchor`` fragments: explicit
    ``{#id}`` anchors on heading lines plus GitHub-style heading slugs.

Columns are 1-based character positions (not bytes), so text before a reference
in another script does not shift it.
"""

from __future__ import annotations

import re
from typing import Callable, Iterable, NamedTuple, Sequence

#: A whole bracket group's content must match exactly one of these forms.
#: Numeric ids have no leading zero (``[R03]`` is not a reference); the slug form
#: is ``[a-z0-9]`` then up to 39 of ``[a-z0-9-]``.
_REFERENCE_RE = re.compile(
    r"(?:R|A|F|S|DOC)[1-9][0-9]*"
    r"|F:[a-z0-9][a-z0-9-]{0,39}"
)

#: ATX heading: 1-6 hashes at column 0, then whitespace.
_HEADING_RE = re.compile(r"^(#{1,6})[ \t]+(.*)$")

#: Explicit anchor at the end of a heading: ``## Goals {#goals}``.
_ANCHOR_RE = re.compile(r"[ \t]*\{#([^}\s]+)\}[ \t]*$")

_FENCE_MARKERS = ("```", "~~~")


class Ref(NamedTuple):
    """One reference mention: text without brackets, 1-based position, raw span.

    ``raw`` is the bracketed source text only (``[S1]``), not a following link
    target, so every reference has the same shape.
    """

    text: str
    line: int
    col: int
    raw: str


class Problem(NamedTuple):
    """One unresolved reference: ``kind`` is ``"unknown"`` or ``"retired"``."""

    kind: str
    ref: str
    line: int
    col: int


def _fence_marker(line: str) -> str | None:
    """The fence marker a line opens or closes with, if any (same rule as docs_text)."""
    stripped = line.strip()
    return next((marker for marker in _FENCE_MARKERS if stripped.startswith(marker)), None)


def _skip_backslash(line: str, index: int) -> int:
    """Advance past a backslash run, swallowing an escaped bracket group."""
    end = index
    while end < len(line) and line[end] == "\\":
        end += 1
    if (end - index) % 2 == 1 and end < len(line) and line[end] == "[":
        close = line.find("]", end + 1)
        return close + 1 if close != -1 else end + 1
    return end


def _skip_code_span(line: str, index: int, run: int) -> int | None:
    """Index just past a code span opened at *index* by *run* backticks, or None."""
    cursor = index + run
    while cursor < len(line):
        if line[cursor] != "`":
            cursor += 1
            continue
        end = cursor
        while end < len(line) and line[end] == "`":
            end += 1
        if end - cursor == run:
            return end
        cursor = end
    return None


def _references_in_line(line: str, number: int) -> list[Ref]:
    found: list[Ref] = []
    index = 0
    while index < len(line):
        char = line[index]
        if char == "\\":
            index = _skip_backslash(line, index)
            continue
        if char == "`":
            run = 1
            while index + run < len(line) and line[index + run] == "`":
                run += 1
            after = _skip_code_span(line, index, run)
            index = after if after is not None else index + run
            continue
        if char == "[":
            close = line.find("]", index + 1)
            if close == -1:
                break
            content = line[index + 1:close]
            if _REFERENCE_RE.fullmatch(content):
                found.append(Ref(content, number, index + 1, line[index:close + 1]))
            index = close + 1
            continue
        index += 1
    return found


def find_references(text: str) -> list[Ref]:
    """Every reference mention in *text*, in document order, duplicates kept.

    Fenced code blocks are skipped whole; inside other lines, inline code spans
    and escaped brackets are skipped character by character. Columns are
    1-based character offsets on their line.
    """
    found: list[Ref] = []
    fence: str | None = None
    for number, line in enumerate(text.splitlines(), start=1):
        marker = _fence_marker(line)
        if fence is not None:
            if marker == fence:
                fence = None
            continue
        if marker is not None:
            fence = marker
            continue
        found.extend(_references_in_line(line, number))
    return found


def check_references(refs: Iterable[Ref], resolver: Callable[[str], str]) -> list[Problem]:
    """Problems for refs whose resolver answer is ``"unknown"`` or ``"retired"``.

    The resolver is called once per ref, in order; an exception it raises is not
    caught here (a broken resolver is a caller bug, not a silent "ok").
    """
    problems: list[Problem] = []
    for ref in refs:
        status = resolver(ref.text)
        if status in ("unknown", "retired"):
            problems.append(Problem(status, ref.text, ref.line, ref.col))
    return problems


def scan_text(text: str, resolver: Callable[[str], str]) -> list[Problem]:
    """``check_references(find_references(text), resolver)`` in one call."""
    return check_references(find_references(text), resolver)


def format_problem(source: str, problem: Problem) -> str:
    """``<source>:<line>  [<ref>]  unknown|retired reference`` (two spaces)."""
    return f"{source}:{problem.line}  [{problem.ref}]  {problem.kind} reference"


def _slug(heading: str) -> str:
    """GitHub-style heading slug: lowercase, punctuation dropped, spaces to '-'.

    Unicode letters and digits survive (``str.isalnum``), as do ``-`` and ``_``;
    every other character is dropped and each whitespace character becomes '-'.
    """
    out: list[str] = []
    for char in heading.strip().lower():
        if char.isalnum() or char in "-_":
            out.append(char)
        elif char.isspace():
            out.append("-")
    return "".join(out).strip("-")


def anchor_ids(markdown: str) -> set[str]:
    """Explicit ``{#id}`` anchors and GitHub-style slugs of every heading.

    Headings inside ``` / ~~~ fences are ignored. An explicit anchor is added as
    written, and the heading without the anchor still contributes its slug.
    """
    ids: set[str] = set()
    fence: str | None = None
    for line in markdown.splitlines():
        marker = _fence_marker(line)
        if fence is not None:
            if marker == fence:
                fence = None
            continue
        if marker is not None:
            fence = marker
            continue
        match = _HEADING_RE.match(line)
        if match is None:
            continue
        text = match.group(2).strip()
        anchor = _ANCHOR_RE.search(text)
        if anchor is not None:
            ids.add(anchor.group(1))
            text = text[:anchor.start()].strip()
        if text:
            slug = _slug(text)
            if slug:
                ids.add(slug)
    return ids


def prd_anchor_ok(prd_ref: str, markdown: str) -> bool:
    """Does ``PRD.md#anchor`` name an anchor in *markdown*?

    A ref without ``#`` is accepted as-is (the file part is the caller's check);
    an empty ref, an empty fragment, or an unknown fragment is False.
    """
    ref = str(prd_ref or "").strip()
    if not ref:
        return False
    _file, separator, fragment = ref.partition("#")
    if not separator:
        return True
    return fragment in anchor_ids(markdown)
