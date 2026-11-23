"""``verify``: every ``passed`` criterion's evidence must be real (DOC5 section 2.6).

The store accepts any non-empty string as evidence. The grammar the product skill
requires is ``"<verbatim quote>" -- <path>:<line>``; this module checks that a quote
written in that grammar actually occurs in the cited file, read from a git revision
(default ``HEAD``) so uncommitted text, which proves nothing about what shipped, does
not count.

Per ``passed`` criterion exactly one verdict:

* ``ok``      the quote occurs in the file (whitespace collapsed, case ignored);
* ``drift``   it occurs, but not near the cited line (still a pass, reported);
* ``fail``    the quote is shorter than ``min_quote``, the file or the revision is
              missing, or the quote does not occur;
* ``free``    the evidence is free text (a test name, a link): it cannot be checked, so
              it is counted and listed, and only ``strict`` turns it into a failure.

``failed``, ``not_run`` and ``waived`` criteria are not touched. Pure helpers
(``parse_evidence``, ``locate_quote``, ``judge``) take text and return verdicts; only
``read_source`` touches git or the disk.
"""
from __future__ import annotations

import bisect
import re
import subprocess
from pathlib import Path
from typing import Any, Optional

try:  # package submodule (Hermes plugin loader)
    from . import model, store
except ImportError:  # top-level module (test suite)
    import model  # type: ignore[no-redef]
    import store  # type: ignore[no-redef]

QUOTE_RE = re.compile(r'^\s*"(.+)"\s*--\s*(\S+?):(\d+)\s*$', re.S)
DEFAULT_MIN_QUOTE = 25
#: a quote found this many lines away from the cited line is reported as drift
LINE_SLACK = 3


def norm(text: str) -> str:
    """Whitespace collapsed, case folded: what a quote is compared on."""
    return re.sub(r"\s+", " ", text).strip().casefold()


def parse_evidence(evidence: Any) -> Optional[tuple[str, str, int]]:
    """``(quote, path, line)`` when ``evidence`` is in the quote grammar, else None."""
    match = QUOTE_RE.match(str(evidence or ""))
    if match is None:
        return None
    return match.group(1), match.group(2), int(match.group(3))


def locate_quote(text: str, quote: str) -> Optional[tuple[int, int]]:
    """``(first_line, last_line)`` (1-based) of the quote in ``text``, or None.

    The comparison collapses whitespace across line breaks, so a quote that wraps lines
    still matches; the returned lines are where it starts and ends in the file.
    """
    wanted = norm(quote)
    if not wanted:
        return None
    pieces: list[str] = []
    starts: list[int] = []          # offset in `joined` where each source line begins
    offset = 0
    for line in text.splitlines():
        flat = re.sub(r"\s+", " ", line).strip().casefold()
        starts.append(offset)
        pieces.append(flat)
        offset += len(flat) + 1     # the joining space
    joined = " ".join(pieces)
    index = joined.find(wanted)
    if index < 0:
        return None
    first = bisect.bisect_right(starts, index)
    last = bisect.bisect_right(starts, index + len(wanted) - 1)
    return first, max(first, last)


def judge(evidence: Any, text: Optional[str], *, min_quote: int = DEFAULT_MIN_QUOTE) -> tuple[str, str]:
    """``(verdict, note)`` for one passed criterion, given the cited file's text (or None)."""
    parsed = parse_evidence(evidence)
    if parsed is None:
        return "free", "evidence is free text, not a quote with path:line"
    quote, path, cited = parsed
    if len(norm(quote)) < min_quote:
        return "fail", f"quote is {len(norm(quote))} chars; at least {min_quote} are needed to prove anything"
    if text is None:
        return "fail", f"{path} cannot be read at the chosen revision"
    found = locate_quote(text, quote)
    if found is None:
        return "fail", f"quote not found in {path}"
    first, last = found
    if first - LINE_SLACK <= cited <= last + LINE_SLACK:
        return "ok", f"{path}:{first}"
    return "drift", f"{path}: found at line {first}, cited {cited}"


def read_source(root: Path, path: str, rev: Optional[str]) -> Optional[str]:
    """The cited file as text: from ``git show <rev>:<path>`` when ``rev`` is given, else disk."""
    if rev:
        result = subprocess.run(["git", "show", f"{rev}:./{path}"], cwd=str(root),
                                capture_output=True, check=False)
        if result.returncode != 0:
            return None
        return result.stdout.decode("utf-8", errors="replace")
    target = (root / path)
    try:
        return target.read_text(encoding="utf-8") if target.is_file() else None
    except (OSError, UnicodeDecodeError):
        return None


def is_git_repo(root: Path) -> bool:
    result = subprocess.run(["git", "rev-parse", "--is-inside-work-tree"], cwd=str(root),
                            capture_output=True, check=False)
    return result.returncode == 0 and result.stdout.strip() == b"true"


def verify_project(project: str, *, rev: Optional[str] = None, repo: Optional[str] = None,
                   min_quote: int = DEFAULT_MIN_QUOTE) -> dict:
    """Judge every ``passed`` criterion of ``project``; returns ``{"rows", "rev", "root"}``.

    ``rows`` are ``{"ref", "verdict", "note"}`` in criterion id order. ``rev`` defaults to
    ``HEAD`` when the root is a git work tree and to the working tree otherwise.
    """
    info = store.get_project(project)
    if info is None:
        raise store.StoreError(f"project {project!r} does not exist")
    root_text = repo or info.get("root")
    if not root_text:
        raise store.StoreError(
            f"verify needs the project's root directory (project set {project} root=DIR)"
            " or --repo DIR")
    root = Path(root_text).expanduser()
    if not root.is_dir():
        raise store.StoreError(f"verify: {root} is not a directory")
    repo_ok = is_git_repo(root)
    if rev and not repo_ok:
        raise store.StoreError(f"--rev {rev} needs a git work tree; {root} is not one")
    chosen = rev or ("HEAD" if repo_ok else None)
    rows = []
    for item in store.list_entities(project, "criterion", status="passed"):
        parsed = parse_evidence(item.get("evidence"))
        text = read_source(root, parsed[1], chosen) if parsed else None
        verdict, note = judge(item.get("evidence"), text, min_quote=min_quote)
        rows.append({"ref": item["ref"], "verdict": verdict, "note": note})
    return {"rows": rows, "rev": chosen, "root": str(root)}


def render(project: str, result: dict, *, strict: bool = False) -> tuple[list[str], int]:
    """The compact lines and the exit code: 1 when anything failed (or, strict, was free text)."""
    rows = result["rows"]
    counts = {name: sum(1 for row in rows if row["verdict"] == name)
              for name in ("ok", "drift", "fail", "free")}
    lines = [f"{row['ref']}  {row['verdict']}  {row['note']}"
             for row in rows if row["verdict"] != "ok"]
    where = f"@{result['rev']}" if result["rev"] else " (working tree)"
    lines.append(f"verify ({project}){where}: {len(rows)} passed criteria,"
                 f" {counts['ok']} ok, {counts['drift']} drift, {counts['fail']} fail,"
                 f" {counts['free']} free text")
    failed = counts["fail"] + (counts["free"] if strict else 0)
    return lines, (1 if failed else 0)
