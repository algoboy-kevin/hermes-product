"""`list --json` (card HP-12, mechanism 1).

One object instead of one line per item: `ok`, `project`, `kind`, `count`,
`items`. Every item is the row `list` reads, minus the body text, with a DOC's
`doc_links` as ref strings. Without the flag the output is byte-identical to the
compact lines the same items already print.
"""

from __future__ import annotations

import json
from pathlib import Path

import cli as cli_module
import store
from _helpers import (  # `home` is a pytest fixture imported for discovery
    add_criterion,
    add_doc,
    add_feature,
    add_requirement,
    add_spec,
    cli,
    home,
    make_project,
)

PROJECT = "shop"


def build_store() -> None:
    """One R (with a body), one F, one S (with a body), one DOC linked to R, one A."""
    make_project()
    add_requirement(project=PROJECT, body="# Pay by card\n\nA buyer pays at checkout.\n")
    add_feature(project=PROJECT)
    add_spec(project=PROJECT, body="# Payments\n\nUse PaymentIntents.\n")
    add_criterion(project=PROJECT, requirement=1)
    add_doc(project=PROJECT)
    assert store.link_entries(PROJECT, "F1", "R1")["message"] == "F1 linked R1"
    assert store.link_entries(PROJECT, "DOC1", "R1")["message"] == "DOC1 linked R1"


def test_list_json_is_one_object_with_every_item(home: Path, capsys) -> None:
    build_store()
    rc, out, err = cli(capsys, "list", "--json")
    assert rc == 0, err
    payload = json.loads(out)
    assert set(payload) == {"ok", "project", "kind", "count", "items"}
    assert payload["ok"] is True
    assert payload["project"] == PROJECT
    assert payload["kind"] is None

    plain_rc, plain_out, plain_err = cli(capsys, "list")
    assert plain_rc == 0, plain_err
    assert payload["count"] == len(plain_out.splitlines())
    assert payload["count"] == len(payload["items"])

    for item in payload["items"]:
        assert item["ref"] and item["kind"] and item["status"]
    by_ref = {item["ref"]: item for item in payload["items"]}
    assert set(by_ref) == {"R1", "F1", "S1", "A1", "DOC1"}

    requirement = by_ref["R1"]
    assert requirement["links"]["features"] == [1]
    assert requirement["body_chars"] > 0
    assert "body" not in requirement


def test_list_json_of_one_kind_holds_only_that_kind(home: Path, capsys) -> None:
    build_store()
    rc, out, err = cli(capsys, "list", "S", "--json")
    assert rc == 0, err
    payload = json.loads(out)
    assert payload["kind"] == "S"
    assert payload["count"] == 1
    # item `kind` is the stored kind name, so an S item is a "spec" (outer `kind`
    # echoes the requested letter, "S")
    assert [item["kind"] for item in payload["items"]] == ["spec"]
    assert payload["items"][0]["ref"] == "S1"


def test_list_json_names_a_docs_links_as_refs(home: Path, capsys) -> None:
    build_store()
    rc, out, err = cli(capsys, "list", "DOC", "--json")
    assert rc == 0, err
    payload = json.loads(out)
    assert payload["count"] == 1
    assert payload["items"][0]["doc_links"] == ["R1"]


def test_plain_list_is_unchanged_by_the_json_flag(home: Path, capsys) -> None:
    build_store()
    rc, out, err = cli(capsys, "list")
    assert rc == 0, err
    expected = "".join(cli_module._render_item(item) + "\n"
                       for item in store.list_entities(PROJECT))
    assert out == expected
