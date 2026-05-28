"""GitHub release-notes importer.

Wraps `GitHubReleasesConnector` — yields the body of each tagged
release on a repository. Indexed alongside docs, these surface in
search as "what changed when" — useful for grounding agents in
recent vendor behaviour without paying for full-repo ingest.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from lighthouse.connectors.base import Connector
from lighthouse.connectors.github_releases import GitHubReleasesConnector
from lighthouse.importers.base import DiscoveredItem, ImporterMeta, LighthouseImporter
from lighthouse.importers.registry import register


@register
class GithubReleasesImporter(LighthouseImporter):
    supports_discovery = True
    meta = ImporterMeta(
        type="github_releases",
        display_name="GitHub releases",
        description=(
            "Index release notes from a GitHub repo's Releases page. "
            "Surfaces 'what changed when' in search."
        ),
        config_schema={
            "type": "object",
            "required": ["owner", "repo"],
            "properties": {
                "owner": {
                    "type": "string",
                    "title": "Owner",
                    "description": "GitHub user or org.",
                },
                "repo": {
                    "type": "string",
                    "title": "Repo",
                },
                "max_releases": {
                    "type": "integer",
                    "title": "Max releases",
                    "default": 30,
                    "minimum": 1,
                    "maximum": 500,
                },
                "include_prereleases": {
                    "type": "boolean",
                    "title": "Include pre-releases",
                    "default": False,
                },
                "include_drafts": {
                    "type": "boolean",
                    "title": "Include drafts",
                    "default": False,
                },
                "github_token": {
                    "type": "string",
                    "title": "GitHub PAT (optional)",
                    "description": (
                        "Required for private repos. Falls back to env "
                        "GITHUB_TOKEN if blank."
                    ),
                    "format": "password",
                },
            },
        },
        secret_keys=("github_token",),
        discovery_required=("github_token",),
    )

    def discover(
        self,
        config: Mapping[str, Any],
        secrets: Mapping[str, str],
    ) -> list[DiscoveredItem]:
        """List repos the token can see. Picking one sets ``owner`` +
        ``repo`` — the importer then indexes that repo's releases."""
        import httpx

        token = secrets.get("github_token") or ""
        if not token:
            raise ValueError("github_token required to list accessible repos")
        out: list[DiscoveredItem] = []
        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
        }
        with httpx.Client(timeout=15.0) as client:
            for page in range(1, 6):
                r = client.get(
                    "https://api.github.com/user/repos",
                    headers=headers,
                    params={"per_page": 100, "page": page, "sort": "updated"},
                )
                r.raise_for_status()
                rows = r.json()
                if not rows:
                    break
                for repo in rows:
                    owner = repo.get("owner", {}).get("login", "")
                    name = repo.get("name", "")
                    full = f"{owner}/{name}"
                    out.append(
                        DiscoveredItem(
                            id=full,
                            name=full,
                            kind="repo",
                            hint=repo.get("description"),
                            config_patch={"owner": owner, "repo": name},
                        )
                    )
                if len(rows) < 100:
                    break
        return out

    def build_connector(
        self,
        config: Mapping[str, Any],
        secrets: Mapping[str, str],
    ) -> Connector:
        return GitHubReleasesConnector(
            owner=str(config["owner"]),
            repo=str(config["repo"]),
            max_releases=int(config.get("max_releases", 30)),
            include_prereleases=bool(config.get("include_prereleases", False)),
            include_drafts=bool(config.get("include_drafts", False)),
            github_token=secrets.get("github_token") or None,
        )
