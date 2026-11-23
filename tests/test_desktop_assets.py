"""HP-14: static guards on the desktop page asset (no node needed).

`desktop/plugin.js` is loaded UNCOMPILED by the Hermes desktop app, so the shape
of the file is itself a contract: the plugin may import only the SDK and react,
it must not reach for a browser API the host does not provide (localStorage), it
must not call the network directly (the host's `ctx.rest` is the one door), and
it must stay a single file of reasonable size because the app reads it from disk
on every start.

The behavioural half of the card lives in ``tests/ui/`` (node); these checks are
the ones that need nothing but Python, so `make test` still covers the asset.
"""

from __future__ import annotations

import re
from pathlib import Path

PLUGIN = Path(__file__).resolve().parent.parent / "desktop" / "plugin.js"

ALLOWED_IMPORTS = {"@hermes/plugin-sdk", "react", "react/jsx-runtime"}
MAX_BYTES = 80 * 1024


def source() -> str:
    assert PLUGIN.is_file(), "desktop/plugin.js is missing (the /product page would not load)"
    return PLUGIN.read_text(encoding="utf-8")


def test_plugin_file_exists_at_the_desktop_root() -> None:
    assert PLUGIN.is_file()


def test_plugin_file_stays_under_eighty_kilobytes() -> None:
    size = PLUGIN.stat().st_size
    assert size < MAX_BYTES, f"desktop/plugin.js is {size} bytes, over the {MAX_BYTES}-byte budget"


def test_only_sdk_and_react_imports() -> None:
    specifiers = re.findall(r"""from\s+['"]([^'"]+)['"]""", source())
    assert specifiers, "no imports found: plugin.js must import the SDK and react"
    illegal = [specifier for specifier in specifiers if specifier not in ALLOWED_IMPORTS]
    assert illegal == [], f"desktop/plugin.js imports {illegal}; only {sorted(ALLOWED_IMPORTS)} are allowed"


def test_no_dynamic_import_or_require() -> None:
    text = source()
    assert "import(" not in text, "a desktop plugin is loaded uncompiled: no dynamic import"
    assert "require(" not in text, "a desktop plugin is ESM: no require()"


def test_no_localstorage() -> None:
    assert "localStorage" not in source(), (
        "preferences go through ctx.storage (the host's door), never localStorage"
    )


def test_no_direct_network_calls() -> None:
    text = source()
    assert "fetch(" not in text, "the page talks to the backend through ctx.rest, never fetch()"
    assert "XMLHttpRequest" not in text
    assert "WebSocket" not in text


def test_no_eval() -> None:
    text = source()
    assert "eval(" not in text
    assert "new Function(" not in text


def test_no_external_urls() -> None:
    text = source()
    assert "http://" not in text, "no external URLs in the page asset"
    assert "https://" not in text, "no external URLs in the page asset"


def test_has_a_default_export_with_the_plugin_contract() -> None:
    text = source()
    assert re.search(r"export\s+default\s+plugin\b", text), "the default export must be the plugin object"
    assert re.search(r"""id:\s*["']product["']""", text), 'the plugin id must be "product"'
    assert re.search(r"register\s*\(\s*ctx\s*\)", text), "the plugin must expose register(ctx)"
    assert "registerMany" in text, "contributions are registered through ctx.registerMany"


def test_exports_the_pure_core_for_tests() -> None:
    text = source()
    assert re.search(r"export\s+const\s+core\s*=", text), (
        "the pure core must be a named export so tests can pin the page's logic"
    )
    for name in ("VIEWS", "STATE_ORDER", "sortEntries", "tabCounts", "filterByState",
                 "navReducer", "showStatusTabs", "showModeToggle", "layoutGraph",
                 "validProjectSlug", "newProjectPayload"):
        assert re.search(rf"\b{name}\b", text), f"core is missing {name}"


def test_the_page_route_and_nav_are_registered() -> None:
    text = source()
    assert '"/product"' in text or "'/product'" in text, "the route path is /product"
    assert "ROUTES_AREA" in text and "SIDEBAR_NAV_AREA" in text and "PALETTE_AREA" in text
    assert "WORKSPACE_PAGE_HEADER_AREA" in text, "the project switcher lives in the header band (A27)"


def test_the_core_section_stays_free_of_react_and_the_sdk() -> None:
    """The delimited core block is the testable half: it must not reach for h() or the SDK."""
    text = source()
    start = text.find("===== core (pure, exported for tests) =====")
    assert start != -1, "the delimited core section marker is missing"
    end = text.find("===== components", start)
    assert end != -1, "the core section is not closed by a components marker"
    block = text[start:end]
    for banned in ("createElement", "@hermes/plugin-sdk", "useState", "useEffect"):
        assert banned not in block, f"the pure core must not use {banned}"
