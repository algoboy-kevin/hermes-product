"""Shared fixtures and row helpers for the entity, status, output and link tests.

The direct-insert helpers (``link_*``, ``add_external_link``) exist for the HP-2
tests that needed link rows before the link verbs existed; the HP-3 tests drive
``link``/``unlink`` through the CLI and read the tables back with ``table_rows``.
Nothing here reads docs/.
"""

from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path
from typing import Any, Iterator

import pytest

import store
from cli import product_command, register_cli


# --------------------------------------------------------------------- fixtures
@pytest.fixture()
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A throwaway HERMES_HOME; the real ~/.hermes store is never touched."""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes-home"))
    return tmp_path


@pytest.fixture()
def con(home: Path) -> Iterator[sqlite3.Connection]:
    """An open store connection on the throwaway db (for direct link rows)."""
    connection = store.connect()
    yield connection
    connection.close()


# ------------------------------------------------------------------- CLI plumbing
def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="hermes product")
    register_cli(p)
    return p


def run(argv: list[str]) -> int:
    """Parse argv the way the Hermes CLI does and run the verb; returns the exit code."""
    return product_command(parser().parse_args(argv))


def make_project(slug: str = "shop", name: str | None = None) -> str:
    store.create_project(slug, name or slug.title())
    return slug


# --------------------------------------------------------------- entity factories
# The factories go through the store API (not the CLI) so a CLI failure is never
# hidden behind a helper that already succeeded.
def add_requirement(project: str = "shop", title: str = "Pay by card",
                    statement: str = "A buyer can pay by card at checkout",
                    priority: str = "must", **fields: Any) -> int:
    return store.create_entity(
        project, "requirement",
        {"title": title, "statement": statement, "priority": priority, **fields},
    )["id"]


def add_criterion(project: str = "shop", requirement: int = 1,
                  text: str = "Given a valid card, when paid, then the order is placed",
                  **fields: Any) -> int:
    return store.create_entity(
        project, "criterion", {"requirement_id": requirement, "text": text, **fields},
    )["id"]


def add_feature(project: str = "shop", title: str = "Stripe integration",
                layer: str = "core", **fields: Any) -> int:
    return store.create_entity(
        project, "feature", {"title": title, "layer": layer, **fields},
    )["id"]


def add_spec(project: str = "shop", title: str = "Payments",
             summary: str = "Use PaymentIntents with 3DS", **fields: Any) -> int:
    return store.create_entity(
        project, "spec", {"title": title, "summary": summary, **fields},
    )["id"]


def add_doc(project: str = "shop", title: str = "Webhook flow", kind: str = "design",
            summary: str = "How webhooks are processed.", **fields: Any) -> int:
    """Create a DOC# through the store API; body options ride along in ``fields``."""
    return store.create_entity(
        project, "doc", {"title": title, "kind": kind, "summary": summary, **fields},
    )["id"]


def row(project: str, kind: str, entity_id: int) -> dict:
    """The raw stored row as a plain dict (no links, no history)."""
    return store.get_entity(project, f"{store.ref_for(kind, entity_id)}")["row"]


# -------------------------------------------------------------------- link rows
def link_feature_requirement(con: sqlite3.Connection, project: str,
                             feature_id: int, requirement_id: int) -> None:
    con.execute(
        "INSERT OR IGNORE INTO feature_requirement(project, feature_id, requirement_id) VALUES (?,?,?)",
        (project, feature_id, requirement_id),
    )
    con.commit()


def link_spec_requirement(con: sqlite3.Connection, project: str,
                          spec_id: int, requirement_id: int) -> None:
    con.execute(
        "INSERT OR IGNORE INTO spec_requirement(project, spec_id, requirement_id) VALUES (?,?,?)",
        (project, spec_id, requirement_id),
    )
    con.commit()


def link_feature_spec(con: sqlite3.Connection, project: str,
                      feature_id: int, spec_id: int) -> None:
    con.execute(
        "INSERT OR IGNORE INTO feature_spec(project, feature_id, spec_id) VALUES (?,?,?)",
        (project, feature_id, spec_id),
    )
    con.commit()


def link_feature_dependency(con: sqlite3.Connection, project: str,
                            feature_id: int, needs_id: int, optional: int = 0) -> None:
    con.execute(
        "INSERT OR IGNORE INTO feature_dependency(project, feature_id, needs_id, optional) VALUES (?,?,?,?)",
        (project, feature_id, needs_id, optional),
    )
    con.commit()


def add_external_link(con: sqlite3.Connection, project: str, entity_type: str,
                      entity_id: int, *, system: str = "github",
                      external_id: str = "1", url: str | None = None,
                      role: str | None = None) -> None:
    con.execute(
        "INSERT INTO external_link(project, entity_type, entity_id, system, external_id, url, role)"
        " VALUES (?,?,?,?,?,?,?)",
        (project, entity_type, entity_id, system, external_id, url, role),
    )
    con.commit()


# ------------------------------------------------------------------------ reading
def history_rows(project: str, kind: str, entity_id: int) -> list[dict]:
    connection = store.connect()
    try:
        return [
            {"change": r["change"], "reason": r["reason"], "at": r["at"]}
            for r in connection.execute(
                "SELECT change, reason, at FROM history"
                " WHERE project = ? AND entity_type = ? AND entity_id = ? ORDER BY id",
                (project, kind, entity_id),
            )
        ]
    finally:
        connection.close()


def status_of(project: str, kind: str, entity_id: int) -> str:
    return store.get_entity(project, store.ref_for(kind, entity_id))["row"]["status"]


# ------------------------------------------------------------------- set + capture
def set_field(capsys, ref: str, *pairs: str, reason: str | None = None,
              project: str | None = None,
              override_reason: str | None = None) -> tuple[int, str, str]:
    """Run `set <REF> key=value ...`; returns (exit code, stdout, stderr)."""
    argv = ["set", ref, *pairs]
    if reason is not None:
        argv += ["--reason", reason]
    if override_reason is not None:
        argv += ["--override-reason", override_reason]
    if project is not None:
        argv += ["--project", project]
    rc = run(argv)
    captured = capsys.readouterr()
    return rc, captured.out, captured.err


def move(capsys, ref: str, target: str, *pairs: str, reason: str | None = None,
         project: str | None = None,
         override_reason: str | None = None) -> tuple[int, str, str]:
    """`set <REF> status=<target> ...` with the extra key=value pairs applied too."""
    return set_field(capsys, ref, f"status={target}", *pairs, reason=reason,
                     project=project, override_reason=override_reason)


def cli(capsys, *argv: str) -> tuple[int, str, str]:
    """Run any verb; returns (exit code, stdout, stderr)."""
    rc = run(list(argv))
    captured = capsys.readouterr()
    return rc, captured.out, captured.err


# ------------------------------------------------------------------- link tables
#: the columns of each link table, in the order ``table_rows`` returns them
LINK_COLUMNS: dict[str, tuple[str, ...]] = {
    "feature_requirement": ("feature_id", "requirement_id"),
    "spec_requirement": ("spec_id", "requirement_id"),
    "feature_spec": ("feature_id", "spec_id"),
    "feature_dependency": ("feature_id", "needs_id", "optional"),
    "requirement_dependency": ("requirement_id", "needs_id"),
    "spec_dependency": ("spec_id", "needs_id"),
    "doc_link": ("doc_id", "entity_type", "entity_id"),
}


def table_rows(project: str, table: str) -> list[tuple]:
    """Every row of one link table for ``project``, ordered by its columns.

    Used to prove A6: a refused link leaves the table byte-for-byte unchanged.
    """
    columns = LINK_COLUMNS[table]
    sql = (f"SELECT {', '.join(columns)} FROM {table}"
           f" WHERE project = ? ORDER BY {', '.join(columns)}")
    connection = store.connect()
    try:
        return [tuple(row) for row in connection.execute(sql, (project,))]
    finally:
        connection.close()


def pass_criterion(capsys, entity_id: int, *, evidence: str = "test_pay_by_card",
                   project: str = "shop", level: str | None = None
                   ) -> tuple[int, str, str]:
    """Move an A# to `passed` with evidence (the only way it can pass)."""
    pairs = [f"evidence={evidence}"]
    if level is not None:
        pairs.append(f"evidence_level={level}")
    return move(capsys, f"A{entity_id}", "passed", *pairs, project=project)


def finish_requirement(capsys, con, entity_id: int, *, project: str = "shop",
                       spec_id: int = 1, feature_id: int = 1) -> None:
    """Drive R<entity_id> to `done` legally (F1/S1 must exist): link, add a criterion, pass it."""
    link_feature_requirement(con, project, feature_id, entity_id)
    add_criterion(project=project, requirement=entity_id,
                  text="Given a card, then it succeeds")
    link_spec_requirement(con, project, spec_id, entity_id)
    set_field(capsys, f"R{entity_id}", "rationale=Revenue", "status=approved", project=project)
    move(capsys, f"R{entity_id}", "in_progress", project=project)
    criteria = store.get_entity(project, f"R{entity_id}")["links"]["criteria"]
    for criterion_id in criteria:
        pass_criterion(capsys, criterion_id, project=project)
    rc, _out, err = move(capsys, f"R{entity_id}", "done", project=project)
    assert rc == 0, err

# ------------------------------------------------------- raw rows (HP-5 round-trip)
def raw_update(con: sqlite3.Connection, project: str, kind: str, entity_id: int,
               **cols: Any) -> None:
    """Overwrite columns verbatim, bypassing validation (values the API strips)."""
    assignments = ", ".join(f"{name} = ?" for name in cols)
    con.execute(
        f"UPDATE {store.ENTITY_TABLE[kind]} SET {assignments} WHERE project = ? AND id = ?",
        (*cols.values(), project, entity_id),
    )
    con.commit()

# ------------------------------------------------- export fixture (HP-5, HP-6 tests)
def build_export_fixture(con: sqlite3.Connection, base: Path) -> dict:
    """The full HP-5 export fixture: every entity kind, every link table, history
    rows, one body_file DOC, one body_ref DOC, a multi-line statement with trailing
    spaces and a trailing newline (impossible through the validating API), and a
    unicode title. Returns small facts the tests assert on.
    """
    project = "shop"
    store.create_project(project, "Shop", root=base)

    add_requirement(project, title="Pay by card",
                    statement="A buyer can pay by card at checkout", priority="must")
    add_requirement(project, title="Tricky whitespace",
                    statement="A shopper sees the total before paying", priority="should")
    # values the API can never store (it strips text): trailing space + trailing newline
    raw_update(con, project, "requirement", 2,
               statement="First line \nsecond line\n", notes="line one\nline two\n")

    add_criterion(project, requirement=1,
                  text="Given a valid card, when paid, then the order is placed")
    add_criterion(project, requirement=1,
                  text="Given a stolen card, when paid, then the payment is refused")
    add_criterion(project, requirement=2,
                  text="Given the cart, when shown, then the total is itemized")

    add_feature(project, title="Stripe integration", layer="core", slug="stripe-pay",
                phase="mvp", description="Card payments through Stripe.",
                paths=["src/pay/stripe.py", "src/pay"])
    add_feature(project, title="Auth plumbing", layer="foundation", slug="auth", infra=1)

    add_spec(project, title="PaymentIntents", summary="Use PaymentIntents with 3DS",
             paths=["docs/payments.md"])
    add_spec(project, title="Webhook signatures", summary="Verify webhook signatures")

    body_ref_file = base / "external-note.md"
    body_ref_file.write_text("# External note\n\nKept outside the store.\n", encoding="utf-8")
    add_doc(project, title="Ünïcödé Guide", kind="prd",
            summary="The product requirements.", body="# Goals\n\nPay by card.\n")
    add_doc(project, title="Webhook flow", kind="design",
            summary="How webhooks are processed.", body="# Webhook flow\n\nStep one.\n")
    add_doc(project, title="Research notes", kind="research",
            summary="What we learned.", body_ref=str(body_ref_file))

    store.link_entries(project, "F1", "R1", con=con)
    store.link_entries(project, "F2", "R2", con=con)
    store.link_entries(project, "S1", "R1", con=con)
    store.link_entries(project, "S2", "R2", con=con)
    store.link_entries(project, "F1", "S1", con=con)
    store.link_entries(project, "F1", needs="F2", con=con)
    store.link_entries(project, "R2", needs="R1", con=con)
    store.link_entries(project, "S2", needs="S1", con=con)
    store.link_entries(project, "DOC2", "R1", con=con)
    add_external_link(con, project, "requirement", 1, system="kanban",
                      external_id="SHOP-3", url="https://kanban.example/SHOP-3",
                      role="tracks")

    set_fields = store.update_entity  # the fixture drives the store API, not the CLI
    set_fields(project, "F2", {"blocked": 1, "blocked_reason": "vendor API token"}, con=con)
    set_fields(project, "R1", {"rationale": "Revenue at checkout", "phase": "mvp",
                               "status_reason": "on track", "superseded_by": 2,
                               "hypothesis_ref": "claim-17", "prd_ref": "PRD.md#goals",
                               "notes": "keep the provider small"}, con=con)
    set_fields(project, "R1", {"status": "approved"}, con=con)
    set_fields(project, "R1", {"status": "in_progress"}, con=con)
    set_fields(project, "A1", {"status": "passed", "evidence": "test_pay_by_card",
                               "evidence_level": "unit"}, con=con)
    set_fields(project, "A3", {"status": "failed"}, con=con)
    set_fields(project, "S1", {"status": "approved"}, con=con)
    set_fields(project, "F2", {"status": "in_progress"}, con=con)
    set_fields(project, "DOC3", {"status": "superseded", "superseded_by": 1},
               reason="absorbed by the PRD", con=con)
    return {"project": project, "body_ref_file": body_ref_file}
