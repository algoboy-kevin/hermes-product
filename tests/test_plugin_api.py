"""HP-14a: the read-only backend API for the desktop page (``dashboard/plugin_api.py``).

Every route DOC2/A26-A33 need is served from the Hermes store without writing:
the db is opened ``mode=ro`` and the only write route is ``POST /projects``
(A33), which goes through ``store.create_project``.

Skipped when FastAPI is absent; each test builds a throwaway HERMES_HOME through
the real store functions.
"""

from __future__ import annotations

import hashlib
import importlib.util
import sys
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import store  # noqa: E402

API_FILE = Path(__file__).resolve().parent.parent / "dashboard" / "plugin_api.py"
MANIFEST_FILE = Path(__file__).resolve().parent.parent / "plugin.yaml"


def _manifest_version() -> str:
    """The version the package ships (the /info route must never drift from the manifest)."""
    for line in MANIFEST_FILE.read_text().splitlines():
        if line.startswith("version:"):
            return line.split(":", 1)[1].strip()
    raise AssertionError("plugin.yaml carries no version line")

LONG_STATEMENT = (
    "A buyer can pay by card at checkout\nso that the order is placed immediately, "
    "with a receipt by email, a retry path for a declined card, a visible error "
    "message, and a clear audit line for support to read afterwards."
)


def _client() -> TestClient:
    spec = importlib.util.spec_from_file_location("product_plugin_api_under_test", API_FILE)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module       # pydantic resolves the body models by module
    spec.loader.exec_module(module)
    app = FastAPI()
    app.include_router(module.router)
    return TestClient(app)


@pytest.fixture()
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A throwaway HERMES_HOME; the real store is never touched."""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes-home"))
    return tmp_path


# ----------------------------------------------------------------------- fixtures
def seed(home: Path) -> str:
    """A project with every kind in several states, built through the store API."""
    store.create_project("shop", "Shop", root=home / "repo")
    project = "shop"

    # requirements: draft R1, R6; approved R2; in_progress R3; done R4; dropped R5
    store.create_entity(project, "requirement",
                        {"title": "Pay by card", "statement": LONG_STATEMENT,
                         "priority": "must", "phase": "mvp"})
    store.create_entity(project, "requirement",
                        {"title": "Refund a card", "statement": "A buyer can be refunded",
                         "priority": "should", "rationale": "Chargebacks cost money"})
    store.create_entity(project, "requirement",
                        {"title": "Save a card", "statement": "A buyer can save a card",
                         "priority": "could", "rationale": "Faster checkout"})
    store.create_entity(project, "requirement",
                        {"title": "Show the total", "statement": "A buyer sees the total",
                         "priority": "must", "rationale": "Trust"})
    store.create_entity(project, "requirement",
                        {"title": "Old idea", "statement": "A buyer can pay by invoice",
                         "priority": "could"})
    store.create_entity(project, "requirement",
                        {"title": "Second draft", "statement": "A buyer can use a wallet",
                         "priority": "should"})

    # features: F1 planned, F2 planned (needs F1), F3 cut, F4 in_progress
    store.create_entity(project, "feature",
                        {"title": "Stripe", "layer": "core", "slug": "stripe"})
    store.create_entity(project, "feature",
                        {"title": "Mobile pay", "layer": "growth", "slug": "mobile"})
    store.create_entity(project, "feature",
                        {"title": "Legacy checkout", "layer": "core", "slug": "legacy"})
    store.create_entity(project, "feature",
                        {"title": "Wallet rail", "layer": "foundation", "slug": "wallet"})
    store.link_entries(project, "F2", needs="F1")
    store.link_entries(project, "F3", needs="F1")
    store.link_entries(project, "F4", needs="F2", optional=True)
    store.update_entity(project, "F3", {"status": "cut", "status_reason": "superseded"})
    store.update_entity(project, "F4", {"status": "in_progress"})

    # specs: S1 approved, S2 draft, S3 implemented
    store.create_entity(project, "spec",
                        {"title": "Payments", "summary": "Use PaymentIntents with 3DS"})
    store.create_entity(project, "spec",
                        {"title": "Saved cards", "summary": "Store a token, never the PAN"})
    store.create_entity(project, "spec",
                        {"title": "Totals", "summary": "Sum lines, tax and shipping"})

    # documents: DOC1 design current (linked to R1), DOC2 prd current, DOC3 superseded
    store.create_entity(project, "doc",
                        {"title": "Webhook flow", "kind": "design",
                         "summary": "How Stripe webhooks are processed."})
    store.create_entity(project, "doc",
                        {"title": "Product brief", "kind": "prd",
                         "summary": "The product in one page.",
                         "body": "# Brief\n\nParagraph one.\n\nParagraph two.\n"})
    store.create_entity(project, "doc",
                        {"title": "Old design", "kind": "design",
                         "summary": "Superseded design notes."})

    # criteria: A1 not_run (R1), A2 failed (R1), A3 passed (R3), A4 passed (R4)
    store.create_entity(project, "criterion",
                        {"requirement_id": "R1", "text": "Given a card, then it is charged"})
    store.create_entity(project, "criterion",
                        {"requirement_id": "R1", "text": "Given a decline, then retry"})
    store.create_entity(project, "criterion",
                        {"requirement_id": "R3", "text": "Given a saved card, then it is used"})
    store.create_entity(project, "criterion",
                        {"requirement_id": "R4", "text": "Given a cart, then the total shows"})
    store.update_entity(project, "A2", {"status": "failed"})
    store.update_entity(project, "A3", {"status": "passed", "evidence": "test_saved_card",
                                        "evidence_level": "unit"})
    store.update_entity(project, "A4", {"status": "passed", "evidence": "test_total"})

    # links
    for ref in ("R1", "R2", "R3", "R4"):
        store.link_entries(project, "F1", ref)
    store.link_entries(project, "S1", "R1")
    store.link_entries(project, "S1", "R4")   # R4 needs a spec to reach in_progress
    store.link_entries(project, "S3", "R1")
    store.link_entries(project, "S2", "R3")
    store.link_entries(project, "DOC1", "R1")

    # status moves that need the links above
    store.update_entity(project, "R2", {"status": "approved"})
    store.update_entity(project, "R3", {"status": "approved"})
    store.update_entity(project, "R3", {"status": "in_progress"})
    store.update_entity(project, "R4", {"status": "approved"})
    store.update_entity(project, "R4", {"status": "in_progress"})
    store.update_entity(project, "R4", {"status": "done"})
    store.update_entity(project, "R5", {"status": "dropped", "status_reason": "no demand"})
    store.update_entity(project, "S1", {"status": "approved"})
    store.update_entity(project, "S3", {"status": "approved"})
    store.update_entity(project, "S3", {"status": "implemented", "notes": "shipped"})
    store.update_entity(project, "DOC3", {"status": "superseded", "superseded_by": "DOC1"})
    return project


@pytest.fixture()
def seeded(home: Path) -> str:
    return seed(home)


def _db() -> Path:
    return store.db_path()


# ------------------------------------------------------------------------- /info
def test_info_on_an_empty_home_creates_nothing(home: Path) -> None:
    payload = _client().get("/info").json()
    assert payload["plugin"] == "product"
    assert payload["version"] == _manifest_version()
    assert payload["db"] == str(home / "hermes-home" / "product" / "product.db")
    assert payload["schema"] == 0
    assert not (home / "hermes-home" / "product").exists()


def test_info_reports_the_schema_version(home: Path, seeded: str) -> None:
    payload = _client().get("/info").json()
    assert payload["schema"] == store.schema_info()["version"] == store.LATEST_VERSION
    assert payload["db"] == str(_db())


# --------------------------------------------------------------------- /projects
def test_projects_empty_on_a_missing_db(home: Path) -> None:
    assert _client().get("/projects").json() == {"projects": []}
    assert not (home / "hermes-home").exists()


def test_projects_lists_fields_and_the_active_flag(home: Path, seeded: str) -> None:
    rows = _client().get("/projects").json()["projects"]
    assert [row["slug"] for row in rows] == ["shop"]
    row = rows[0]
    assert row["name"] == "Shop"
    assert row["root"] == str((home / "repo").resolve())
    assert row["active"] is False
    store.set_active_project("shop")
    rows = _client().get("/projects").json()["projects"]
    assert rows[0]["active"] is True


def test_post_projects_creates_one(home: Path, seeded: str) -> None:
    response = _client().post("/projects", json={
        "slug": "mobile", "name": "Mobile app", "root": str(home / "mobile"),
    })
    assert response.status_code == 201
    assert response.json() == {
        "slug": "mobile", "name": "Mobile app",
        "root": str((home / "mobile").resolve()),
    }
    assert [row["slug"] for row in store.list_projects()] == ["mobile", "shop"]


def test_post_projects_duplicate_is_400_and_creates_nothing(home: Path, seeded: str) -> None:
    before = [row["slug"] for row in store.list_projects()]
    response = _client().post("/projects", json={"slug": "shop", "name": "Again"})
    assert response.status_code == 400
    assert "already exists" in response.json()["error"]
    assert [row["slug"] for row in store.list_projects()] == before


def test_post_projects_invalid_slug_is_400_and_creates_nothing(home: Path, seeded: str) -> None:
    before = [row["slug"] for row in store.list_projects()]
    response = _client().post("/projects", json={"slug": "Bad Name", "name": "Bad"})
    assert response.status_code == 400
    assert "invalid project slug" in response.json()["error"]
    assert [row["slug"] for row in store.list_projects()] == before


def test_post_projects_missing_fields_are_400(home: Path, seeded: str) -> None:
    client = _client()
    no_slug = client.post("/projects", json={"name": "No slug"})
    no_name = client.post("/projects", json={"slug": "noslugname"})
    assert no_slug.status_code == 400 and "error" in no_slug.json()
    assert no_name.status_code == 400 and "error" in no_name.json()
    assert [row["slug"] for row in store.list_projects()] == ["shop"]


# ------------------------------------------------------------------------- /prd
def test_prd_reads_the_stored_body_file(home: Path, seeded: str) -> None:
    payload = _client().get("/project/shop/prd").json()
    assert payload["doc"] == "DOC2"
    assert payload["title"] == "Product brief"
    assert payload["markdown"] == "# Brief\n\nParagraph one.\n\nParagraph two.\n"


def test_prd_reads_a_local_body_ref(home: Path, seeded: str) -> None:
    source = home / "brief.md"
    source.write_text("# External brief\n\nAll of it.\n", encoding="utf-8")
    store.create_project("refdoc", "Ref doc")
    store.create_entity("refdoc", "doc", {
        "title": "External brief", "kind": "prd", "summary": "From a file.",
        "body_ref": str(source),
    })
    payload = _client().get("/project/refdoc/prd").json()
    assert payload["doc"] == "DOC1"
    assert payload["markdown"] == "# External brief\n\nAll of it.\n"


def test_prd_url_body_ref_has_no_markdown(home: Path, seeded: str) -> None:
    store.create_project("urldoc", "URL doc")
    store.create_entity("urldoc", "doc", {
        "title": "Remote brief", "kind": "prd", "summary": "Fetched elsewhere.",
        "body_ref": "https://example.com/brief.md",
    })
    payload = _client().get("/project/urldoc/prd").json()
    assert payload["doc"] == "DOC1"
    assert payload["markdown"] == ""


def test_prd_without_a_body_has_no_markdown(home: Path, seeded: str) -> None:
    store.create_project("nobodydoc", "No body")
    store.create_entity("nobodydoc", "doc", {
        "title": "Empty brief", "kind": "prd", "summary": "No body yet.",
    })
    payload = _client().get("/project/nobodydoc/prd").json()
    assert payload["doc"] == "DOC1"
    assert payload["markdown"] == ""


def test_prd_absent_names_the_command_that_adds_one(home: Path, seeded: str) -> None:
    store.create_project("bare", "Bare")
    payload = _client().get("/project/bare/prd").json()
    assert payload["doc"] is None
    assert payload["markdown"] == ""
    assert "hermes product add DOC --kind prd" in payload["hint"]


def test_prd_unknown_project_is_404(home: Path, seeded: str) -> None:
    response = _client().get("/project/ghost/prd")
    assert response.status_code == 404
    assert response.json() == {"error": "no such project"}


# ---------------------------------------------------------------------- /states
def test_states_workflow_order_per_kind(home: Path, seeded: str) -> None:
    payload = _client().get("/project/shop/states").json()
    assert payload == {
        "requirement": ["draft", "approved", "in_progress", "done", "dropped"],
        "feature": ["planned", "in_progress", "shipped", "cut"],
        "spec": ["draft", "approved", "implemented", "verified", "superseded"],
        "criterion": ["failed", "not_run", "passed", "waived"],
        "doc": ["current", "superseded"],
    }
    assert payload["criterion"][0] == "failed"


def test_states_unknown_project_is_404(home: Path, seeded: str) -> None:
    assert _client().get("/project/ghost/states").status_code == 404


# --------------------------------------------------------------------- /entries
def test_entries_requirement_counts_ordering_and_extras(home: Path, seeded: str) -> None:
    payload = _client().get("/project/shop/entries", params={"kind": "requirement"}).json()
    assert payload["kind"] == "requirement"
    assert payload["counts"] == {
        "all": 6, "draft": 2, "approved": 1, "in_progress": 1, "done": 1, "dropped": 1,
    }
    assert [entry["ref"] for entry in payload["entries"]] == [
        "R1", "R6", "R2", "R3", "R4", "R5",
    ]
    first = payload["entries"][0]
    assert first["status"] == "draft"
    assert first["title"] == "Pay by card"
    assert first["priority"] == "must" and first["phase"] == "mvp"
    assert first["links"] == {"features": 1, "specs": 2, "criteria": 2}


def test_entries_summary_is_one_truncated_line(home: Path, seeded: str) -> None:
    entries = _client().get(
        "/project/shop/entries", params={"kind": "requirement"}
    ).json()["entries"]
    summary = entries[0]["summary"]
    assert len(summary) == 160
    assert summary.endswith("\u2026")
    assert "\n" not in summary


def test_entries_status_filter_keeps_full_counts(home: Path, seeded: str) -> None:
    payload = _client().get(
        "/project/shop/entries", params={"kind": "requirement", "status": "draft"}
    ).json()
    assert [entry["ref"] for entry in payload["entries"]] == ["R1", "R6"]
    assert payload["counts"]["all"] == 6
    assert payload["counts"]["done"] == 1


def test_entries_criterion_orders_failed_first(home: Path, seeded: str) -> None:
    payload = _client().get(
        "/project/shop/entries", params={"kind": "criterion"}
    ).json()
    assert [entry["ref"] for entry in payload["entries"]] == ["A2", "A1", "A3", "A4"]
    assert payload["counts"] == {
        "all": 4, "failed": 1, "not_run": 1, "passed": 2, "waived": 0,
    }
    first = payload["entries"][0]
    assert first["requirement"] == "R1"
    assert first["summary"] == "Given a decline, then retry"


def test_entries_feature_ordering_and_extras(home: Path, seeded: str) -> None:
    payload = _client().get("/project/shop/entries", params={"kind": "feature"}).json()
    assert [entry["ref"] for entry in payload["entries"]] == ["F1", "F2", "F4", "F3"]
    assert payload["counts"] == {
        "all": 4, "planned": 2, "in_progress": 1, "shipped": 0, "cut": 1,
    }
    rows = {entry["ref"]: entry for entry in payload["entries"]}
    assert rows["F2"]["layer"] == "growth"
    assert rows["F2"]["needs"] == [1]
    assert rows["F1"]["links"] == {"needs": 0, "requirements": 4, "specs": 0}


def test_entries_spec_ordering_and_link_counts(home: Path, seeded: str) -> None:
    payload = _client().get("/project/shop/entries", params={"kind": "spec"}).json()
    assert [entry["ref"] for entry in payload["entries"]] == ["S2", "S1", "S3"]
    assert payload["counts"] == {
        "all": 3, "draft": 1, "approved": 1, "implemented": 1,
        "verified": 0, "superseded": 0,
    }
    rows = {entry["ref"]: entry for entry in payload["entries"]}
    assert rows["S1"]["links"] == {"requirements": 2, "features": 0}
    assert rows["S2"]["links"] == {"requirements": 1, "features": 0}


def test_entries_doc_ordering_kind_and_link_counts(home: Path, seeded: str) -> None:
    payload = _client().get("/project/shop/entries", params={"kind": "doc"}).json()
    assert [entry["ref"] for entry in payload["entries"]] == ["DOC1", "DOC2", "DOC3"]
    assert payload["counts"] == {"all": 3, "current": 2, "superseded": 1}
    rows = {entry["ref"]: entry for entry in payload["entries"]}
    assert rows["DOC1"]["kind"] == "design"
    assert rows["DOC2"]["kind"] == "prd"
    assert rows["DOC1"]["links"] == {
        "requirement": 1, "criterion": 0, "feature": 0, "spec": 0,
    }
    assert rows["DOC1"]["summary"] == "How Stripe webhooks are processed."


def test_entries_bad_kind_and_bad_status_are_400(home: Path, seeded: str) -> None:
    client = _client()
    missing = client.get("/project/shop/entries")
    assert missing.status_code == 400 and "error" in missing.json()
    for kind in ("nope", "R", "requirements"):
        response = client.get("/project/shop/entries", params={"kind": kind})
        assert response.status_code == 400, kind
        assert "error" in response.json()
    bad_status = client.get(
        "/project/shop/entries", params={"kind": "requirement", "status": "shipped"}
    )
    assert bad_status.status_code == 400


def test_entries_unknown_project_is_404(home: Path, seeded: str) -> None:
    response = _client().get("/project/ghost/entries", params={"kind": "requirement"})
    assert response.status_code == 404
    assert response.json() == {"error": "no such project"}


# ----------------------------------------------------------------------- /entry
def test_entry_detail_requirement(home: Path, seeded: str) -> None:
    payload = _client().get("/project/shop/entry/R1").json()
    assert payload["ref"] == "R1" and payload["kind"] == "requirement"
    fields = payload["fields"]
    assert fields["title"] == "Pay by card"
    assert fields["statement"] == LONG_STATEMENT
    assert fields["priority"] == "must" and fields["phase"] == "mvp"
    assert fields["status"] == "draft"
    assert "status_reason" not in fields and "blocked_reason" not in fields
    assert payload["links"]["features"] == [1]
    assert payload["links"]["specs"] == [1, 3]
    assert payload["links"]["criteria"] == [1, 2]
    assert payload["docs"] == [{
        "ref": "DOC1", "kind": "design", "title": "Webhook flow",
        "summary": "How Stripe webhooks are processed.", "status": "current",
    }]
    assert payload["history"], "link additions are logged"
    assert set(payload["history"][0]) == {"at", "change", "reason"}
    assert any(event["change"].startswith("linked ") for event in payload["history"])
    assert payload["command"] == "hermes product set R1 key=value"


def test_entry_detail_criterion_and_spec_and_feature(home: Path, seeded: str) -> None:
    client = _client()
    criterion = client.get("/project/shop/entry/A3").json()
    assert criterion["fields"]["requirement_id"] == 3
    assert criterion["fields"]["evidence"] == "test_saved_card"
    assert criterion["fields"]["evidence_level"] == "unit"
    assert criterion["command"] == "hermes product set A3 key=value"

    spec = client.get("/project/shop/entry/S1").json()
    assert spec["fields"]["summary"] == "Use PaymentIntents with 3DS"
    assert spec["links"]["requirements"] == [1, 4]

    feature = client.get("/project/shop/entry/F2").json()
    assert feature["fields"]["layer"] == "growth"
    assert feature["links"]["needs"] == [1]
    assert feature["links"]["requirements"] == []


def test_entry_detail_doc_links_and_kind(home: Path, seeded: str) -> None:
    payload = _client().get("/project/shop/entry/DOC1").json()
    assert payload["kind"] == "doc"
    assert payload["fields"]["kind"] == "design"
    assert payload["links"] == {"requirement": [1], "criterion": [], "feature": [], "spec": []}
    assert payload["command"] == "hermes product set DOC1 key=value"


def test_entry_detail_history_is_the_last_20_newest_first(home: Path, seeded: str) -> None:
    con = store.connect()
    try:
        for index in range(25):
            con.execute(
                "INSERT INTO history(project, entity_type, entity_id, change, reason, at)"
                " VALUES (?,?,?,?,?,?)",
                ("shop", "requirement", 6, f"change {index}", None, f"2024-01-01T00:00:{index:02d}"),
            )
        con.commit()
    finally:
        con.close()
    history = _client().get("/project/shop/entry/R6").json()["history"]
    assert len(history) == 20
    assert history[0]["change"] == "change 24"
    assert history[-1]["change"] == "change 5"


def test_entry_detail_errors(home: Path, seeded: str) -> None:
    client = _client()
    missing = client.get("/project/shop/entry/R99")
    assert missing.status_code == 404 and "error" in missing.json()
    bad_ref = client.get("/project/shop/entry/XX")
    assert bad_ref.status_code == 400 and "error" in bad_ref.json()
    no_project = client.get("/project/ghost/entry/R1")
    assert no_project.status_code == 404
    assert no_project.json() == {"error": "no such project"}


# ----------------------------------------------------------------------- /graph
def test_graph_nodes_edges_and_critical_in_build_order(home: Path, seeded: str) -> None:
    payload = _client().get("/project/shop/graph").json()
    nodes = {node["ref"]: node for node in payload["nodes"]}
    assert sorted(nodes) == ["F1", "F2", "F4"]          # the cut feature is out
    assert nodes["F1"] == {
        "id": 1, "ref": "F1", "title": "Stripe", "status": "planned", "layer": "core",
    }
    assert payload["edges"] == [
        {"from": "F1", "to": "F2", "optional": False},
        {"from": "F2", "to": "F4", "optional": True},
    ]
    assert payload["critical"] == ["F1", "F2"]          # build order: needs first


def test_graph_on_an_empty_project(home: Path, seeded: str) -> None:
    store.create_project("bare", "Bare")
    assert _client().get("/project/bare/graph").json() == {
        "nodes": [], "edges": [], "critical": [],
    }


def test_graph_unknown_project_is_404(home: Path, seeded: str) -> None:
    assert _client().get("/project/ghost/graph").status_code == 404


# ------------------------------------------------------------------- read-only
def test_every_get_leaves_the_db_byte_identical(home: Path, seeded: str) -> None:
    client = _client()
    db = _db()
    before = (hashlib.sha256(db.read_bytes()).hexdigest(),
              db.stat().st_size, db.stat().st_mtime_ns,
              sorted(path.name for path in db.parent.iterdir()))
    urls = [
        "/info", "/projects", "/project/shop/prd", "/project/shop/states",
        "/project/shop/entries?kind=requirement", "/project/shop/entries?kind=criterion",
        "/project/shop/entries?kind=feature", "/project/shop/entries?kind=spec",
        "/project/shop/entries?kind=doc", "/project/shop/entry/R1",
        "/project/shop/entry/DOC1", "/project/shop/graph",
    ]
    for url in urls:
        assert client.get(url).status_code == 200, url
    after = (hashlib.sha256(db.read_bytes()).hexdigest(),
             db.stat().st_size, db.stat().st_mtime_ns,
             sorted(path.name for path in db.parent.iterdir()))
    assert after == before


def test_gets_on_a_missing_db_create_no_file(home: Path) -> None:
    client = _client()
    assert client.get("/projects").json() == {"projects": []}
    assert client.get("/project/shop/entries", params={"kind": "requirement"}).status_code == 404
    assert client.get("/project/shop/graph").status_code == 404
    assert client.get("/info").json()["schema"] == 0
    assert not (home / "hermes-home").exists()


def test_entry_payload_never_carries_a_body_text(home: Path, seeded: str) -> None:
    """Schema v2 bodies stay out of the page's JSON until the page asks for them."""
    store.update_entity(seeded, "S1", {"body": "SECRET-BODY-TEXT " * 50})
    response = _client().get(f"/project/{seeded}/entry/S1")
    assert response.status_code == 200
    assert "SECRET-BODY-TEXT" not in response.text
    assert "body" not in response.json()["fields"]
    listing = _client().get(f"/project/{seeded}/entries", params={"kind": "S"})
    assert "SECRET-BODY-TEXT" not in listing.text


# --------------------------------------------------------------- bodies on the page (v2)
_PAGE_BODY = "# Contract\n\nIntro line.\n\n## Parity\n\nLive rows equal research rows.\n\n## Limits\n\nNone.\n"


def test_entry_payload_reports_the_body_size(home: Path, seeded: str) -> None:
    store.update_entity(seeded, "S1", {"body": _PAGE_BODY})
    payload = _client().get(f"/project/{seeded}/entry/S1").json()
    assert payload["body_chars"] == len(_PAGE_BODY)
    assert _client().get(f"/project/{seeded}/entry/S2").json()["body_chars"] == 0
    assert _client().get(f"/project/{seeded}/entry/R1").json()["body_chars"] == 0


def test_body_route_reads_text_section_and_truncation(home: Path, seeded: str) -> None:
    store.update_entity(seeded, "S1", {"body": _PAGE_BODY})
    url = f"/project/{seeded}/entry/S1/body"
    full = _client().get(url).json()
    assert full["text"] == _PAGE_BODY and full["total"] == len(_PAGE_BODY)
    assert full["truncated"] is False and full["command"] == "hermes product show S1 --body"
    cut = _client().get(url, params={"max_chars": 10}).json()
    assert cut["text"] == _PAGE_BODY[:10] and cut["truncated"] is True
    assert "--max-chars 100000" in cut["command"]
    part = _client().get(url, params={"section": "Parity"}).json()
    assert part["text"].startswith("## Parity") and "Limits" not in part["text"]
    missing = _client().get(url, params={"section": "Nope"})
    assert missing.status_code == 400 and "no section 'Nope'" in missing.json()["error"]
    assert _client().get(url, params={"max_chars": 0}).status_code == 422


def test_body_route_for_docs_criteria_and_unknowns(home: Path, seeded: str) -> None:
    doc = store.create_entity(seeded, "doc", {"title": "Run report", "kind": "report",
                                               "summary": "What the run measured.",
                                               "body": "# Run\n\nmeasured things\n"})["ref"]
    assert _client().get(f"/project/{seeded}/entry/{doc}/body").json()["text"].startswith("# Run")
    assert _client().get(f"/project/{seeded}/entry/{doc}").json()["body_chars"] == len("# Run\n\nmeasured things\n")
    criterion = _client().get(f"/project/{seeded}/entry/A1/body")
    assert criterion.status_code == 400 and "has no body" in criterion.json()["error"]
    assert _client().get(f"/project/{seeded}/entry/S999/body").status_code == 404
    assert _client().get("/project/ghost/entry/S1/body").status_code == 404
    # no body: empty text, not an error
    assert _client().get(f"/project/{seeded}/entry/S2/body").json()["text"] == ""


def test_body_route_is_read_only(home: Path, seeded: str) -> None:
    store.update_entity(seeded, "S1", {"body": _PAGE_BODY})
    before = _db().read_bytes()
    _client().get(f"/project/{seeded}/entry/S1/body")
    assert _db().read_bytes() == before
