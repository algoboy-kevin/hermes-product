"""Code-path coverage, pure (card HP-13a).

The reverse of "every path an entry names exists": which packages and spec files
does no F#/S# entry's ``paths`` cover? Nothing here reads a store; entries arrive
as ``{"F3": ["pkg/a"], "S2": ["docs/specs/S2.md"]}``.

  * ``walk_repo`` — the only filesystem touch: sorted relative POSIX paths under
    a root, skipping VCS/dependency/vendor trees, build output and test dirs.
  * ``owner_of`` — which entry ids cover a path. A directory claim covers its
    children on a path-segment boundary (``pkg/a`` covers ``pkg/a/x.go`` but never
    ``pkg/ab/x.go``); a file claim covers only itself; ``.`` covers every relative
    path. Absolute claims and claims containing ``..`` are ignored.
  * ``coverage_report`` — full counts plus lists cut to ``max_items`` and a
    per-list hidden count. A package is a directory that directly holds a counted
    source file; a parent of a claimed package counts as covered.
  * ``format_report`` — the plain-text rendering.

Advisory only: empty inputs and entries without paths yield a zeroed report, not
an error. Copied method and skip sets from the feature-graph coverage check
(``_repo_files``, ``coverage_report``, ``_coverage_*``), with entries' ``paths``
replacing that graph's ``packages``/``spec`` fields.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

#: A counted source file has one of these extensions (copy of the feature-graph set).
_COVERAGE_SOURCE_EXTS = frozenset({
    ".go", ".py", ".rs", ".ts", ".tsx", ".js", ".java", ".kt", ".swift",
    ".c", ".cc", ".cpp", ".h",
})

#: Directories no coverage or walk needs to account for (feature-graph's coverage
#: set plus its artifact skip set: VCS, dependency and vendor trees).
_SKIP_DIRS = frozenset({
    "testdata", "tests", "test", "examples", "scripts", "__pycache__", "dist",
    "build", "target", "vendor", "node_modules", ".git",
})

_SPEC_DIRS = ("docs/specs",)


def _norm_path(value: object) -> str:
    """Posix comparison form: forward slashes, no ``./`` prefix, no trailing ``/``."""
    if not isinstance(value, str):
        return ""
    path = value.replace("\\", "/").strip()
    while path.startswith("./"):
        path = path[2:]
    while "//" in path:
        path = path.replace("//", "/")
    if path not in ("", "."):
        path = path.rstrip("/")
    return path


def _claim_path(value: object) -> str:
    """A usable relative claim/target, or ``""`` for absolute and ``..`` paths."""
    path = _norm_path(value)
    if not path:
        return ""
    if path.startswith("/") or Path(path).is_absolute():
        return ""
    if ".." in path.split("/"):
        return ""
    return path


def _seg_prefix_len(prefix: str, path: str) -> int:
    """``len(prefix)`` when it prefixes *path* on a segment boundary, else -1."""
    if not prefix:
        return -1
    if path == prefix:
        return len(prefix)
    if path.startswith(prefix + "/"):
        return len(prefix)
    return -1


def _under(child: str, parent: str) -> bool:
    """Is *child* at or below *parent*? ``.`` is every relative path."""
    if parent == ".":
        return bool(child) and not (child.startswith("/") or child == ".."
                                   or child.startswith("../"))
    return _seg_prefix_len(parent, child) >= 0


def _iter_paths(paths: object) -> tuple[object, ...]:
    """A claim sequence as a tuple; a single string is one path, None is empty."""
    if paths is None:
        return ()
    if isinstance(paths, str):
        return (paths,)
    try:
        return tuple(paths)  # type: ignore[arg-type]
    except TypeError:
        return ()


def _dir_skipped(rel: str) -> bool:
    """True for a directory no coverage check accounts for (root ``.`` always counts)."""
    if rel in ("", "."):
        return False
    return any(
        segment.startswith(".") or segment in _SKIP_DIRS
        for segment in rel.split("/")
    )


def _source_file(name: str) -> bool:
    _, dot, ext = name.rpartition(".")
    return bool(dot) and f".{ext}" in _COVERAGE_SOURCE_EXTS


def _test_file(name: str) -> bool:
    return (
        name.endswith("_test.go")
        or name.startswith("test_")
        or name.endswith(".test.ts")
        or name.endswith(".test.js")
        or name.endswith(".spec.ts")
    )


def walk_repo(root: str | Path) -> list[str]:
    """Every file under *root* as a sorted posix-relative path.

    Skips hidden directories, VCS/dependency trees (``.git``, ``node_modules``,
    ``vendor``) and the coverage skip dirs. Unreadable directories and races are
    skipped rather than raised: coverage is advisory and can never fail a run.
    """
    base = Path(root)
    found: list[str] = []
    stack = [base]
    while stack:
        directory = stack.pop()
        try:
            entries = list(directory.iterdir())
        except OSError:
            continue
        for entry in entries:
            try:
                if entry.is_dir():
                    if entry.name.startswith(".") or entry.name in _SKIP_DIRS:
                        continue
                    stack.append(entry)
                elif entry.is_file():
                    found.append(entry.relative_to(base).as_posix())
            except (OSError, ValueError):  # races and symlink loops never fail the walk
                continue
    return sorted(found)


def owner_of(path: str, entries: Mapping[str, Sequence[str]]) -> list[str]:
    """Entry ids whose ``paths`` cover *path*, sorted for determinism.

    A directory claim covers its children on a segment boundary, a file claim
    covers itself, ``.`` covers every relative path. Absolute claims and claims
    containing ``..`` are ignored; several matching claims on one entry add its id
    once.
    """
    target = _claim_path(path)
    if not target:
        return []
    owners: set[str] = set()
    for entry_id, paths in (entries or {}).items():
        for raw in _iter_paths(paths):
            claim = _claim_path(raw)
            if not claim:
                continue
            if claim == "." or _seg_prefix_len(claim, target) >= 0:
                owners.add(entry_id)
                break
    return sorted(owners)


def coverage_report(
    files: Iterable[str],
    entries: Mapping[str, Sequence[str]],
    *,
    spec_dirs: Sequence[str] = _SPEC_DIRS,
    max_items: int = 50,
) -> dict[str, Any]:
    """Counted packages and spec files, covered or not, with bounded lists.

    ``files`` are repo-relative paths (``walk_repo`` or a test list); ``entries``
    maps an id to its ``paths``. Packages are directories directly holding at
    least one non-test source file; a package is covered when a claim covers it or
    a parent of it (a claim deeper inside it counts too, as in feature-graph).
    Spec files are ``*.md`` under *spec_dirs* and are covered by an exact file
    claim or a directory claim above them. ``*_total``/``*_covered`` are always
    full numbers; ``truncated`` holds how many items each list hid.
    """
    rel_files = [path for path in (_claim_path(item) for item in (files or ())) if path]

    package_dirs: set[str] = set()
    for rel in rel_files:
        name = rel.rsplit("/", 1)[-1]
        parent = rel.rsplit("/", 1)[0] if "/" in rel else "."
        if _test_file(name) or not _source_file(name):
            continue
        if _dir_skipped(parent):
            continue
        package_dirs.add(parent)

    claims = sorted({
        claim
        for paths in (entries or {}).values()
        for claim in (_claim_path(raw) for raw in _iter_paths(paths))
        if claim
    })

    def package_covered(directory: str) -> bool:
        return any(
            _under(directory, claim) or _under(claim, directory)
            for claim in claims
        )

    uncovered_packages = sorted(directory for directory in package_dirs
                                if not package_covered(directory))

    spec_files: set[str] = set()
    for rel in rel_files:
        if not rel.endswith(".md"):
            continue
        if any(segment.startswith(".") for segment in rel.split("/")[:-1]):
            continue
        for raw_dir in spec_dirs or ():
            spec_dir = _claim_path(raw_dir)
            if not spec_dir:
                continue
            if spec_dir == "." or _seg_prefix_len(spec_dir, rel) >= 0:
                spec_files.add(rel)
                break

    uncovered_specs = sorted(
        rel for rel in spec_files if not owner_of(rel, entries or {})
    )

    try:
        limit = max(0, int(max_items))
    except (TypeError, ValueError):
        limit = 50

    packages_total = len(package_dirs)
    specs_total = len(spec_files)
    return {
        "packages_total": packages_total,
        "packages_covered": packages_total - len(uncovered_packages),
        "uncovered_packages": uncovered_packages[:limit],
        "specs_total": specs_total,
        "specs_covered": specs_total - len(uncovered_specs),
        "uncovered_specs": uncovered_specs[:limit],
        "truncated": {
            "uncovered_packages": max(0, len(uncovered_packages) - limit),
            "uncovered_specs": max(0, len(uncovered_specs) - limit),
        },
    }


def format_report(report: Mapping[str, Any]) -> list[str]:
    """``coverage: packages <covered>/<total>, specs <covered>/<total>`` plus items.

    One ``  uncovered package:`` / ``  uncovered spec:`` line per listed item, and
    ``  … and <k> more`` after each list that ``truncated`` says was cut.
    """
    lines = [
        "coverage: packages "
        f"{report.get('packages_covered', 0)}/{report.get('packages_total', 0)}, "
        "specs "
        f"{report.get('specs_covered', 0)}/{report.get('specs_total', 0)}"
    ]
    truncated = report.get("truncated") or {}
    for package in report.get("uncovered_packages") or ():
        lines.append(f"  uncovered package: {package}")
    hidden_packages = int(truncated.get("uncovered_packages", 0) or 0)
    if hidden_packages > 0:
        lines.append(f"  … and {hidden_packages} more")
    for spec in report.get("uncovered_specs") or ():
        lines.append(f"  uncovered spec: {spec}")
    hidden_specs = int(truncated.get("uncovered_specs", 0) or 0)
    if hidden_specs > 0:
        lines.append(f"  … and {hidden_specs} more")
    return lines
