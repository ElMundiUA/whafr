"""Built-in admin UI — static SPA served at /ui.

No build step: plain ES modules + hand-rolled SVG charts, so the wheel
ships the UI as-is and a self-hosted engine gets an admin panel with
zero extra infrastructure. The SPA talks to the same /v1 surface the
SDK uses (Bearer admin token + X-Workspace header, both set in the UI
and persisted to localStorage).
"""

from __future__ import annotations

from pathlib import Path

STATIC_DIR = Path(__file__).parent / "static"
