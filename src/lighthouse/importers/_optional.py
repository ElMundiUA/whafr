"""Friendly errors for missing optional-dep importers.

Tier-A adapters wrap `llama-index-readers-*` packages that aren't in
the engine's base install — they live behind extras groups like
``[importers-notion]``. If an operator picks "Notion" in the admin
UI on an engine where the package isn't installed, we don't want a
raw ``ModuleNotFoundError`` — we want a one-liner telling them what
to install.
"""

from __future__ import annotations

import importlib
from typing import Any


class MissingExtraError(RuntimeError):
    """An importer's llama-hub backing package isn't installed.

    The admin router maps this to a 500 with the message body."""


def import_reader(module: str, attr: str, extras_group: str) -> Any:
    """Import ``attr`` from ``module``; raise ``MissingExtraError`` with
    a `pip install` hint when the package isn't there.

    Use at `make_reader()` call-time, not module-import time, so adding
    a Tier-A adapter doesn't make the importers package fail to import
    on a slim build."""
    try:
        m = importlib.import_module(module)
    except ImportError as exc:
        raise MissingExtraError(
            f"This importer needs the optional package "
            f"'{module}'. Install with: pip install "
            f"'lighthouse[{extras_group}]' "
            f"(or 'lighthouse[importers-all]' for everything)."
        ) from exc
    try:
        return getattr(m, attr)
    except AttributeError as exc:
        raise MissingExtraError(
            f"Loaded '{module}' but it has no attribute '{attr}'. "
            f"Likely a version skew — bump the package to a release "
            f"that ships {attr}."
        ) from exc
