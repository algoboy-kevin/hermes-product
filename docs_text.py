"""Markdown section slicing and bounded reading, pure (card HP-4).

No sqlite, no store: ``cli`` reads the DOC text and hands it here. Three jobs:

  * ``headings`` — the outline of a Markdown body: level, text and the line span
    of the section it opens. Lines inside a ``` or ~~~ fence are code, never a
    heading, so a comment in a shell block does not split a document.
  * ``section`` — one section's text, matched on the heading text
    (case-insensitive, whitespace collapsed, first match wins).
  * ``bounded`` — the hard character cap `doc read` promises, cut at a character
    boundary, with the total so the caller can say how much it hid.

Line numbers in ``headings`` are 1-based and inclusive: ``start_line`` is the
heading line, ``end_line`` the last line of the section (the line before the next
heading of the same or a higher level, or the last line of the text).
"""

from __future__ import annotations

import re
from typing import Optional

#: 1-6 hashes at column 0, one or more spaces, then the heading text
_HEADING_RE = re.compile(r"^(#{1,6})[ \t]+(.*)$")

_FENCES = ("```", "~~~")


def _normalize(text: str) -> str:
    """Whitespace-collapsed, case-folded text used for heading matching."""
    return " ".join(str(text).split()).casefold()


def headings(markdown: str) -> list[tuple[int, str, int, int]]:
    """``[(level, text, start_line, end_line), ...]`` in document order.

    Fenced code blocks (``` or ~~~, closed by the same marker) are skipped whole,
    so ``# comment`` inside one is not an outline entry.
    """
    lines = markdown.splitlines(keepends=True)
    found: list[tuple[int, str, int]] = []
    fence: Optional[str] = None
    for index, line in enumerate(lines, start=1):
        stripped = line.strip()
        marker = next((candidate for candidate in _FENCES
                       if stripped.startswith(candidate)), None)
        if fence is not None:
            if marker == fence:
                fence = None
            continue
        if marker is not None:
            fence = marker
            continue
        match = _HEADING_RE.match(line.rstrip("\r\n"))
        if match is None:
            continue
        text = match.group(2).strip()
        if not text:
            continue
        found.append((len(match.group(1)), text, index))

    outline: list[tuple[int, str, int, int]] = []
    for position, (level, text, start) in enumerate(found):
        end = len(lines)
        for later_level, _later_text, later_start in found[position + 1:]:
            if later_level <= level:
                end = later_start - 1
                break
        outline.append((level, text, start, end))
    return outline


def section(markdown: str, heading_text: str) -> Optional[str]:
    """The text of the first section whose heading matches, or None.

    The match ignores case and collapses whitespace on both sides; the returned
    text starts at the heading line and keeps the source line endings.
    """
    wanted = _normalize(heading_text)
    if not wanted:
        return None
    lines = markdown.splitlines(keepends=True)
    for _level, text, start, end in headings(markdown):
        if _normalize(text) == wanted:
            return "".join(lines[start - 1:end])
    return None


def bounded(text: str, max_chars: int) -> tuple[str, int, bool]:
    """``(slice, total_chars, truncated)``; the slice never exceeds ``max_chars``.

    Python slices by code point, so a multi-byte character is never split in half.
    """
    limit = max(int(max_chars), 0)
    total = len(text)
    if total <= limit:
        return text, total, False
    return text[:limit], total, True
