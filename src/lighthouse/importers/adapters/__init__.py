"""Side-effect imports. Each module decorates a class with @register
at import time, so importing the package populates the registry."""

from __future__ import annotations

from lighthouse.importers.adapters import (  # noqa: F401
    github_repo,
    llama_hub,
    sitemap,
    url_list,
)
