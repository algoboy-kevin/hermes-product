"""The two DOC flags: `pinned` and `is_prd` (schema v4).

Pinned here: the migration that adds them (an existing document comes out unpinned and
not-a-PRD, and the version gate moves 3 -> 4), the field rules (`pinned=true|false`, a
plain bool — not a link, not text), the store roundtrip through `create_entity` /
`update_entity` / `list_entities`, and the card payload the desktop page draws from:
`pinned` and `is_prd` are booleans on a doc card, and they are in the detail fields only
when true.
"""

from __future__ import annotations

import importlib.util
import sqlite3
import sys
from pathlib import Path

import pytest

import model
import store

API_FILE = Path(__file__).resolve().parent.parent / "dashboard" / "plugin_api.py"


def _client():
    spec = importlib.util.spec_from_file_location("product_plugin_api_doc_flags", API_FILE)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    app = FastAPI()
    app.include_router(module.router)
    return TestClient(app)


@pytest.fixture()
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes-home"))
    return tmp_path


def _seed() -> None:
    store.create_project("shop", "Shop")
    store.create_entity("shop", "doc",
                        {"title": "Product brief", "kind": "prd", "summary": "The PRD."})
    store.create_entity("shop", "doc",
                        {"title": "Webhook flow", "kind": "design", "summary": "Webhooks."})


# --------------------------------------------------------------- model + migration
def test_the_flags_are_plain_booleans_on_a_doc() -> None:
    assert "pinned" in model.SET_FIELDS["doc"] and "is_prd" in model.SET_FIELDS["doc"]
    assert model.validate_field("doc", "pinned", "true") == 1
    assert model.validate_field("doc", "is_prd", 0) == 0
    with pytest.raises(model.ValidationError):
        model.validate_field("doc", "pinned", "DOC2")     # a ref is not a flag


def test_v3_database_migrates_and_flags_its_prd(home: Path) -> None:
    """A db written at v3 gains both columns, and the document whose kind is prd becomes THE
    PRD (flagged and pinned) — that is what fixes projects created before the flags existed."""
    path = store.db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(path))
    con.executescript(store.SCHEMA_V1)
    con.execute("INSERT INTO project(slug, name, created_at, updated_at)"
                " VALUES ('shop','Shop','now','now')")
    con.execute("INSERT INTO doc(project, id, title, kind, summary, status, updated_at)"
                " VALUES ('shop', 1, 'Old brief', 'prd', 'x', 'current', 'now')")
    con.execute("INSERT INTO doc(project, id, title, kind, summary, status, updated_at)"
                " VALUES ('shop', 2, 'Old note', 'design', 'x', 'current', 'now')")
    con.execute("PRAGMA user_version = 3")
    con.commit()
    con.close()

    assert store.latest_version() >= 5
    rows = store.list_entities("shop", "doc")
    prd = [row for row in rows if row["title"] == "Old brief"][0]
    note = [row for row in rows if row["title"] == "Old note"][0]
    assert (prd["is_prd"], prd["pinned"]) == (1, 1)
    assert (note["is_prd"], note["pinned"]) == (0, 0)


def test_a_new_project_is_seeded_with_its_prd(home: Path) -> None:
    created = store.create_project("shop", "Shop", with_prd=True)
    assert created["prd"] == "DOC1"
    rows = store.list_entities("shop", "doc")
    assert len(rows) == 1
    row = rows[0]
    assert (row["doc_kind"], row["is_prd"], row["pinned"], row["status"]) == ("prd", 1, 1, "current")


def test_a_plain_create_project_still_writes_no_document(home: Path) -> None:
    store.create_project("bare", "Bare")
    assert store.list_entities("bare", "doc") == []


# --------------------------------------------------------------------- store roundtrip
def test_pin_and_prd_flag_roundtrip(home: Path) -> None:
    _seed()
    store.update_entity("shop", "DOC1", {"pinned": True, "is_prd": True}, reason="flags")
    row = [one for one in store.list_entities("shop", "doc") if one["ref"] == "DOC1"][0]
    assert (row["pinned"], row["is_prd"], row["doc_kind"]) == (1, 1, "prd")

    store.update_entity("shop", "DOC1", {"pinned": False}, reason="unpin")
    row = [one for one in store.list_entities("shop", "doc") if one["ref"] == "DOC1"][0]
    assert (row["pinned"], row["is_prd"]) == (0, 1)

    # a new document starts with both flags off
    store.create_entity("shop", "doc",
                        {"title": "Later note", "kind": "report", "summary": "A report."})
    row = [one for one in store.list_entities("shop", "doc") if one["ref"] == "DOC3"][0]
    assert (row["pinned"], row["is_prd"]) == (0, 0)


# ------------------------------------------------------------------------ card payload
def test_the_card_carries_both_flags(home: Path) -> None:
    pytest.importorskip("fastapi")
    _seed()
    store.update_entity("shop", "DOC2", {"pinned": True, "is_prd": True}, reason="flags")
    client = _client()

    cards = client.get("/project/shop/entries?kind=doc").json()["entries"]
    by_ref = {card["ref"]: card for card in cards}
    assert by_ref["DOC2"]["pinned"] is True and by_ref["DOC2"]["is_prd"] is True
    assert by_ref["DOC1"]["pinned"] is False and by_ref["DOC1"]["is_prd"] is False

    fields = client.get("/project/shop/entry/DOC2").json()["fields"]
    assert fields["pinned"] is True and fields["is_prd"] is True
    plain = client.get("/project/shop/entry/DOC1").json()["fields"]
    assert "pinned" not in plain and "is_prd" not in plain


# --------------------------------------------------------------- pin endpoint + order
def test_pin_endpoint_toggles_and_reorders(home: Path) -> None:
    """The page's one write: POST .../pin flips the flag, and the list puts the PRD first,
    then pinned entries, then the rest."""
    # The flags are explicit by contract: only create_project(with_prd) and the v4 migration
    # set them for you, so the PRD here is created flagged and pinned.
    store.create_project("shop", "Shop")
    store.create_entity("shop", "doc",
                        {"title": "Product brief", "kind": "prd", "summary": "The PRD.",
                         "is_prd": True, "pinned": True})
    store.create_entity("shop", "doc",
                        {"title": "Webhook flow", "kind": "design", "summary": "Webhooks."})
    store.create_entity("shop", "doc",
                        {"title": "Scratch notes", "kind": "design", "summary": "Notes."})
    client = _client()

    refs = [row["ref"] for row in client.get("/project/shop/entries", params={"kind": "doc"}).json()["entries"]]
    assert refs == ["DOC1", "DOC2", "DOC3"]   # PRD first, then id order

    pinned = client.post("/project/shop/entry/DOC3/pin", params={"pinned": "true"})
    assert pinned.status_code == 200 and pinned.json() == {"ref": "DOC3", "pinned": True}

    rows = client.get("/project/shop/entries", params={"kind": "doc"}).json()["entries"]
    assert [row["ref"] for row in rows] == ["DOC1", "DOC3", "DOC2"]
    assert [row["pinned"] for row in rows] == [True, True, False]

    unpinned = client.post("/project/shop/entry/DOC3/pin", params={"pinned": "false"})
    assert unpinned.status_code == 200
    rows = client.get("/project/shop/entries", params={"kind": "doc"}).json()["entries"]
    assert [row["ref"] for row in rows] == ["DOC1", "DOC2", "DOC3"]


def test_prd_outranks_a_pinned_non_prd(home: Path) -> None:
    """Priority is PRD, then pinned, then the rest — an unpinned PRD still leads."""
    store.create_project("shop", "Shop")
    store.create_entity("shop", "doc",
                        {"title": "Brief", "kind": "prd", "summary": "x", "is_prd": True, "pinned": True})
    store.create_entity("shop", "doc", {"title": "Note A", "kind": "design", "summary": "x"})
    store.update_entity("shop", "DOC2", {"pinned": 1})          # pinned, not the PRD
    store.update_entity("shop", "DOC1", {"pinned": 0})          # PRD, explicitly unpinned

    rows = _client().get("/project/shop/entries", params={"kind": "doc"}).json()["entries"]
    assert [row["ref"] for row in rows] == ["DOC1", "DOC2"]


def test_pin_unknown_ref_is_400(home: Path) -> None:
    _seed()
    response = _client().post("/project/shop/entry/DOC9/pin", params={"pinned": "true"})
    assert response.status_code == 400
    assert "error" in response.json()
