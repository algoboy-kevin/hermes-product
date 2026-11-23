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
