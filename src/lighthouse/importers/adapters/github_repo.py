"""GitHub repository tree importer.

Wraps `GitHubTreeConnector` — enumerates a public (or PAT-authed)
repo's git tree, filters by extension + path, fetches each blob's
raw content. Good for indexing documentation that lives as markdown
in a repo (mdn/content, kubernetes/website, vendor changelogs).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from lighthouse.connectors.base import Connector
from lighthouse.connectors.github_tree import GitHubTreeConnector
from lighthouse.importers.base import DiscoveredItem, ImporterMeta, LighthouseImporter
from lighthouse.importers.registry import register


@register
class GithubRepoImporter(LighthouseImporter):
    supports_discovery = True
    meta = ImporterMeta(
        type="github_repo",
        display_name="GitHub repo (tree)",
        description=(
            "Index markdown / RST files from a public or PAT-authed "
            "GitHub repository. Tree walk, no submodules / LFS."
        ),
        config_schema={
            "type": "object",
            "required": ["owner", "repo"],
            "properties": {
                "owner": {
                    "type": "string",
                    "title": "Owner",
                    "description": "GitHub user or org, e.g. kubernetes.",
                },
                "repo": {
                    "type": "string",
                    "title": "Repo",
                    "description": "Repository name, e.g. website.",
                },
                "branch": {
                    "type": "string",
                    "title": "Branch",
                    "default": "main",
                },
                "file_extensions": {
                    "type": "string",
                    "title": "File extensions",
                    "description": "Comma-separated, e.g. .md,.mdx,.rst",
                    "default": ".md,.mdx,.rst,.txt",
                },
                "include_paths": {
                    "type": "string",
                    "title": "Include path prefixes (optional)",
                    "description": "One per line, e.g. content/en/docs/.",
                    "format": "textarea",
                },
                "max_files": {
                    "type": "integer",
                    "title": "Max files",
                    "default": 2000,
                    "minimum": 1,
                    "maximum": 50000,
                },
                "rate_limit_per_sec": {
                    "type": "number",
                    "title": "Rate limit (req/s)",
                    "default": 5.0,
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
        import httpx

        token = secrets.get("github_token") or ""
        if not token:
            raise ValueError("github_token required to list accessible repos")
        out: list[DiscoveredItem] = []
        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
        }
        # /user/repos returns repos the token can access (owned + member-of).
        # Cap at 5 pages × 100 = 500 repos — well above what one team typically
        # wants to import.
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
                            hint=repo.get("description") or repo.get("default_branch"),
                            config_patch={
                                "owner": owner,
                                "repo": name,
                                "branch": repo.get("default_branch") or "main",
                            },
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
        ext_raw = str(config.get("file_extensions") or ".md,.mdx,.rst,.txt")
        exts = tuple(p.strip() for p in ext_raw.split(",") if p.strip())
        include_raw = str(config.get("include_paths", "") or "").strip()
        include = [p for p in include_raw.splitlines() if p.strip()] or None
        return GitHubTreeConnector(
            owner=str(config["owner"]),
            repo=str(config["repo"]),
            branch=str(config.get("branch") or "main"),
            file_extensions=exts,
            include_paths=include,
            max_files=int(config.get("max_files", 2000)),
            rate_limit_per_sec=float(config.get("rate_limit_per_sec", 5.0)),
            github_token=secrets.get("github_token") or None,
        )
