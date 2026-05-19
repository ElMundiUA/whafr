"""Side-effect imports. Each module decorates a class with @register
at import time, so importing the package populates the registry."""

from __future__ import annotations

from lighthouse.importers.adapters import (  # noqa: F401
    github_releases,
    github_repo,
    llama_hub,
    llama_tier_a,
    rss,
    sitemap,
    url_list,
)
