"""The dashboard API must not depend on bare top-level module names.

In the Hermes server process several plugins share one interpreter; feature-graph also
ships top-level ``store`` / ``graph`` modules and loads first. A plain ``import store``
in our plugin_api then returned ITS module (`has no attribute 'list_projects'`, HTTP 500).
Regression: pre-populate sys.modules with look-alike modules, then load the API.
"""
from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

import pytest

API_FILE = Path(__file__).resolve().parent.parent / "dashboard" / "plugin_api.py"
IMPORT_NAMES = ("store", "graph", "model", "cli", "plan", "export", "docs_text", "scan")


def _imposter(name: str) -> types.ModuleType:
    module = types.ModuleType(name)
    module.__file__ = f"/elsewhere/feature-graph/{name}.py"   # another plugin's module
    return module


@pytest.fixture()
def api_with_imposters(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    pytest.importorskip("fastapi")
    for name in IMPORT_NAMES:
        monkeypatch.setitem(sys.modules, name, _imposter(name))
    # the plugin root must NOT rescue us through sys.path either
    root = str(API_FILE.parent.parent)
    monkeypatch.setattr(sys, "path", [p for p in sys.path if p != root])
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))
    name = "hermes_dashboard_plugin_product"                  # the host's module name
    spec = importlib.util.spec_from_file_location(name, API_FILE)
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, name, module)
    spec.loader.exec_module(module)
    return module


def test_api_loads_and_serves_with_foreign_store_and_graph_modules(api_with_imposters) -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    app = FastAPI()
    app.include_router(api_with_imposters.router, prefix="/api/plugins/product")
    client = TestClient(app)
    assert client.get("/api/plugins/product/info").status_code == 200
    created = client.post("/api/plugins/product/projects", json={"slug": "shop", "name": "Shop"})
    assert created.status_code == 201, created.text
    listed = client.get("/api/plugins/product/projects")
    assert listed.status_code == 200, listed.text
    assert [p["slug"] for p in listed.json()["projects"]] == ["shop"]
    graph = client.get("/api/plugins/product/project/shop/graph")
    assert graph.status_code == 200, graph.text


def test_foreign_modules_are_left_untouched(api_with_imposters) -> None:
    for name in IMPORT_NAMES:
        assert sys.modules[name].__file__.startswith("/elsewhere/"), name
