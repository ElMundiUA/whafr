"""GitHub Releases connector.

Pulls release notes (the body of each tagged release) from a repo
and yields one ``SourceDocument`` per release. This is the canonical
source of "what changed in version X" — the exact kind of post-
training-cutoff knowledge frontier models can't have.

Why not reuse the docs-oriented GithubConnector: that connector clones
the repo and filters by file extension. Releases live in GitHub's REST
API (``/repos/{owner}/{repo}/releases``), not in the tree. Different
endpoint, different rate limits, different shape — separate connector
is cleaner than overloading.

The token resolution order matches the docs connector: explicit arg
> ``GITHUB_TOKEN`` env. Unauthenticated calls hit the 60 req/hr limit
quickly; in production we expect the same token to be wired.
"""

from __future__ import annotations

import logging
import os
from collections.abc import AsyncIterator

from lighthouse.connectors.base import (
    Connector,
    SourceDocument,
    parse_publish_date,
)

logger = logging.getLogger(__name__)


class GitHubReleasesConnector(Connector):
    """Yield release notes from a GitHub repo."""

    name = "github_releases"

    def __init__(
        self,
        owner: str,
        repo: str,
        *,
        max_releases: int = 30,
        include_prereleases: bool = False,
        include_drafts: bool = False,
        github_token: str | None = None,
    ) -> None:
        """
        Args:
            owner: GitHub user/org.
            repo: Repository name.
            max_releases: Cap on releases fetched per run. GitHub API
                returns 30/page by default; we trade pages for cost.
            include_prereleases: Include releases flagged as pre-release.
                Default off — pre-releases churn and rarely carry
                stable knowledge.
            include_drafts: Include drafts. Default off; drafts are
                private and never appear unless the token has access.
            github_token: Auth token. Falls back to env GITHUB_TOKEN.
        """
        self._owner = owner
        self._repo = repo
        self._max = max_releases
        self._include_pre = include_prereleases
        self._include_drafts = include_drafts
        self._token = github_token or os.environ.get("GITHUB_TOKEN") or None

    async def ingest(self) -> AsyncIterator[SourceDocument]:
        import httpx

        url = f"https://api.github.com/repos/{self._owner}/{self._repo}/releases"
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "lighthouse-gh-releases/0.1",
        }
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"

        emitted = 0
        per_page = 30
        page = 1
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
            while emitted < self._max:
                try:
                    resp = await client.get(
                        url,
                        params={"per_page": per_page, "page": page},
                        headers=headers,
                    )
                except Exception:
                    logger.exception(
                        "github releases %s/%s page=%d fetch failed",
                        self._owner,
                        self._repo,
                        page,
                    )
                    return
                if resp.status_code == 404:
                    logger.warning(
                        "github releases %s/%s 404 — repo missing or "
                        "private without token",
                        self._owner,
                        self._repo,
                    )
                    return
                if resp.status_code == 403:
                    logger.warning(
                        "github releases %s/%s 403 — likely rate-limited "
                        "(supply GITHUB_TOKEN)",
                        self._owner,
                        self._repo,
                    )
                    return
                resp.raise_for_status()
                page_data = resp.json() or []
                if not page_data:
                    return
                for rel in page_data:
                    if rel.get("draft") and not self._include_drafts:
                        continue
                    if rel.get("prerelease") and not self._include_pre:
                        continue
                    body = (rel.get("body") or "").strip()
                    if not body:
                        # Bodyless releases (just a tag with no notes)
                        # carry no useful knowledge for retrieval.
                        continue
                    tag = rel.get("tag_name") or ""
                    name = rel.get("name") or tag or "(release)"
                    html_url = rel.get("html_url") or ""
                    published = parse_publish_date(
                        rel.get("published_at") or rel.get("created_at")
                    )
                    slug = f"{self._owner}/{self._repo}"
                    yield SourceDocument(
                        source_id=f"gh-release:{slug}:{tag}",
                        title=f"{slug} {tag}: {name}",
                        body=body,
                        url=html_url,
                        reference_time=published,
                        metadata={
                            "repo": slug,
                            "tag": str(tag),
                            "name": str(name),
                            "extractor": "github-releases",
                            "date": str(rel.get("published_at") or ""),
                            "prerelease": "true" if rel.get("prerelease") else "false",
                        },
                    )
                    emitted += 1
                    if emitted >= self._max:
                        return
                if len(page_data) < per_page:
                    return
                page += 1
