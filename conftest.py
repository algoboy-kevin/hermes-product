"""Make the plugin's top-level modules importable from tests.

The plugin's modules are loaded by Hermes via relative imports; plain pytest needs
the repo root on sys.path. This keeps `cli.py` testable with no Hermes install.
"""

from __future__ import annotations
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _never_touch_the_real_hermes_home(tmp_path_factory, monkeypatch):
    """Safety net: every test gets a throwaway HERMES_HOME, so a fixture that forgets
    the explicit `home` fixture can never write into the real ~/.hermes store.
    Tests that set their own HERMES_HOME (the `home` fixture) simply override this."""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path_factory.mktemp("hermes-home-guard")))
