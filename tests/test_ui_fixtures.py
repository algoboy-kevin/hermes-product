"""The desktop page's render checks run against payloads the REAL API produced.

DOC5 section 2.10: the first page crash passed its smoke test because the fixture was
hand-shaped (`{features: [1], criteria: [1, 2]}`) while the API also sends two counters.
So the page tests read ``tests/ui/fixtures/api-entries.json``, generated here from a real
store through the real routes with a frozen clock. This test regenerates it and fails when
the committed file differs, so the fixture cannot drift from the API again.

Regenerate after an intended API change:  UPDATE_UI_FIXTURES=1 pytest tests/test_ui_fixtures.py
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import store  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
FIXTURE = ROOT / "tests" / "ui" / "fixtures" / "api-entries.json"
API_FILE = ROOT / "dashboard" / "plugin_api.py"
FROZEN = "2026-11-23T12:00:00+00:00"

SPEC_BODY = ("# Payments contract\n\nCharge a card with a PaymentIntent and 3DS.\n\n"
             "## Webhooks\n\nVerify the signature, then record the event once.\n\n"
             "## Limits\n\nNo retries after a hard decline.\n")


def _client() -> TestClient:
    spec = importlib.util.spec_from_file_location("product_plugin_api_fixture_gen", API_FILE)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    app = FastAPI()
    app.include_router(module.router)
    return TestClient(app)


def _build(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> dict:
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(store, "_now", lambda: FROZEN)
    store.create_project("shop", "Shop", root=tmp_path / "repo")
    store.create_entity("shop", "requirement", {
        "title": "Pay by card", "statement": "A buyer can pay by card at checkout",
        "priority": "must", "phase": "mvp", "body": "Context: cards only in the first release.\n"})
    store.create_entity("shop", "criterion", {"requirement_id": 1,
                                              "text": "Given a card, then it is charged"})
    store.create_entity("shop", "criterion", {"requirement_id": 1,
                                              "text": "Given a decline, then retry"})
    store.update_entity("shop", "A1", {"status": "passed", "evidence": "test_charge"})
    store.create_entity("shop", "feature", {"title": "Stripe", "layer": "core", "slug": "stripe",
                                            "description": "Card payments through Stripe"})
    store.create_entity("shop", "spec", {"title": "Payments", "summary": "How a card is charged.",
                                         "body": SPEC_BODY})
    store.create_entity("shop", "spec", {"title": "Saved cards", "summary": "How cards are saved."})
    store.create_entity("shop", "doc", {"title": "Run report", "kind": "report",
                                        "summary": "What the first run measured.",
                                        "body": "# Run 1\n\nCharged 31 cards.\n"})
    store.create_entity("shop", "doc", {"title": "v0.1 release notes", "kind": "release_notes",
                                        "summary": "Frozen notes for the v0.1 cut.",
                                        "body": "# v0.1\n\nFirst cut.\n"})
    store.create_entity("shop", "doc", {"title": "Tape load run", "kind": "run_log",
                                        "summary": "What the 2026-11-25 tape-load run did.",
                                        "body": "# Run\n\nLoaded the tape.\n"})
    store.link_entries("shop", "F1", "R1")
    store.link_entries("shop", "S1", "R1")
    store.link_entries("shop", "DOC1", "S1")

    client = _client()
    out: dict = {"entries": {}, "bodies": {}, "lists": {}}
    for ref in ("R1", "A1", "F1", "S1", "S2", "DOC1", "DOC2", "DOC3"):
        response = client.get(f"/project/shop/entry/{ref}")
        assert response.status_code == 200, (ref, response.text)
        out["entries"][ref] = response.json()
    for name, url in (("S1", "/project/shop/entry/S1/body"),
                      ("S1#Webhooks", "/project/shop/entry/S1/body?section=Webhooks"),
                      ("S1#cut", "/project/shop/entry/S1/body?max_chars=60"),
                      ("DOC1", "/project/shop/entry/DOC1/body")):
        response = client.get(url)
        assert response.status_code == 200, (name, response.text)
        out["bodies"][name] = response.json()
    # the Docs deck payload the page filters client-side (HP-29): one row per doc, each
    # carrying its `kind`, from the real list route
    response = client.get("/project/shop/entries?kind=doc")
    assert response.status_code == 200, response.text
    out["lists"]["doc"] = response.json()
    return out


def test_ui_fixture_matches_what_the_api_returns(monkeypatch, tmp_path: Path) -> None:
    generated = _build(monkeypatch, tmp_path)
    text = json.dumps(generated, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    if os.environ.get("UPDATE_UI_FIXTURES") == "1":
        FIXTURE.parent.mkdir(parents=True, exist_ok=True)
        FIXTURE.write_text(text, encoding="utf-8")
    assert FIXTURE.is_file(), "run with UPDATE_UI_FIXTURES=1 to create the fixture"
    assert FIXTURE.read_text(encoding="utf-8") == text, (
        "tests/ui/fixtures/api-entries.json is out of date with the API;"
        " regenerate with UPDATE_UI_FIXTURES=1")


def test_the_fixture_has_the_shapes_that_once_crashed_the_page(monkeypatch, tmp_path: Path) -> None:
    generated = _build(monkeypatch, tmp_path)
    requirement = generated["entries"]["R1"]
    # the two counters beside the id lists, which a hand-shaped fixture left out
    assert requirement["links"]["criteria_total"] == 2
    assert requirement["links"]["criteria_passed"] == 1
    assert isinstance(requirement["links"]["features"], list)
    assert requirement["body_chars"] > 0 and generated["entries"]["S2"]["body_chars"] == 0
    assert generated["bodies"]["S1#cut"]["truncated"] is True
    # HP-29: every doc row of the list route carries its `kind`, so the Docs kind filter
    # can be client-side with no API change
    docs = generated["lists"]["doc"]["entries"]
    assert all("kind" in row for row in docs)
    assert {row["kind"] for row in docs} == {"report", "release_notes", "run_log"}
