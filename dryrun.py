"""``--dry-run`` and ``--json`` for the writing verbs (DOC5 sections 2.4 and 2.5).

``--dry-run`` runs the REAL verb against a throwaway copy of the product directory
(``<HERMES_HOME>/product``: the database through SQLite's backup API, plus every
project's body files), so validation, id allocation, link rules and error messages are
exactly the real ones and nothing can leak into the live store. The copy is deleted
when the verb returns. ``--json`` captures the verb's output lines and prints one JSON
object instead: ``{"ok", "exit", "dry_run", "ref", "action", "lines"}`` (or
``{"ok": false, "error", ...}`` when the verb refuses).
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import re
import shutil
import sqlite3
import tempfile
from pathlib import Path
from typing import Any, Callable, Iterator

try:  # package submodule (Hermes plugin loader)
    from . import store
except ImportError:  # top-level module (test suite)
    import store  # type: ignore[no-redef]

_REF_RE = re.compile(r"^(DOC\d+|[RAFS]\d+|F:[a-z0-9-]+)\b")
_ACTION_RE = re.compile(r"\b(created|exists|updated|unlinked|linked)\b")


@contextlib.contextmanager
def throwaway_home() -> Iterator[Path]:
    """Point ``HERMES_HOME`` at a copy of the product directory for the duration."""
    real_dir = store.db_path().parent
    tmp = Path(tempfile.mkdtemp(prefix="product-dry-run-"))
    previous = os.environ.get("HERMES_HOME")
    try:
        copy = tmp / "product"
        copy.mkdir()
        if real_dir.is_dir():
            for item in real_dir.iterdir():
                if item.name.startswith(store.DB_FILENAME):
                    continue
                if item.is_dir():
                    shutil.copytree(item, copy / item.name)
                elif item.is_file():
                    shutil.copy2(item, copy / item.name)
            source = real_dir / store.DB_FILENAME
            if source.is_file():
                src = sqlite3.connect(str(source))
                dst = sqlite3.connect(str(copy / store.DB_FILENAME))
                try:
                    src.backup(dst)
                finally:
                    dst.close()
                    src.close()
        os.environ["HERMES_HOME"] = str(tmp)
        yield copy
    finally:
        if previous is None:
            os.environ.pop("HERMES_HOME", None)
        else:
            os.environ["HERMES_HOME"] = previous
        shutil.rmtree(tmp, ignore_errors=True)


def run_with_options(handler: Callable[[Any], int], args: Any, *, dry_run: bool,
                     as_json: bool, errors: tuple, exit_error: int) -> int:
    """Run ``handler(args)`` honouring ``--dry-run`` / ``--json``; returns its exit code."""
    out = io.StringIO()
    code = exit_error
    failure = None
    try:
        with contextlib.ExitStack() as stack:
            if dry_run:
                stack.enter_context(throwaway_home())
            if as_json or dry_run:
                stack.enter_context(contextlib.redirect_stdout(out))
            code = handler(args)
    except errors as exc:
        failure = str(exc)
    lines = out.getvalue().splitlines()
    if as_json:
        payload: dict[str, Any] = {"ok": failure is None and code == 0,
                                   "exit": exit_error if failure is not None else code,
                                   "dry_run": bool(dry_run)}
        if failure is not None:
            payload["error"] = failure
        else:
            first = lines[0] if lines else ""
            match = _REF_RE.match(first)
            if match:
                payload["ref"] = match.group(1)
                found = _ACTION_RE.search(first[match.end():])
                payload["action"] = (found.group(1) if found
                                     else "status_changed" if "->" in first else None)
            payload["lines"] = lines
        print(json.dumps(payload, ensure_ascii=False))
        return payload["exit"]
    if failure is not None:
        raise _Refused(failure)
    for line in lines:                                   # dry run, human output
        print(f"(dry run) {line}")
    return code


class _Refused(Exception):
    """Re-raised for the human path so ``product_command`` prints it on stderr as before."""
