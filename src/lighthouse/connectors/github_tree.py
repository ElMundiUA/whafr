"""GitHub tree connector — direct REST API, no llama-index.

Replaces the previous llama-index-backed ``GitHubConnector`` for
large repos. llama-index's ``GithubRepositoryReader`` blows up on
huge trees with ``KeyError: 'commit'`` / ``KeyError: 'sha'`` —
observed on mdn/content (~50 K files), GoogleChromeLabs/web.dev,
reactjs/react.dev, kubernetes/website. Symptoms suggest its tree-
walk assumes a response shape that the GitHub API doesn't always
return for paginated giants.

This connector hits ``/repos/{owner}/{repo}/git/trees/{branch}?recursive=1``
once, filters the resulting blob list by extension + optional path
prefix, then fetches each blob's raw content via the standard
``raw.githubusercontent.com`` URL. Single auth token. Simple to
debug, no transitive deps.

What it doesn't do: full submodule walk, LFS, branch comparison.
For canonical-doc ingest those are out of scope — we want article
markdown, not artefacts.
"""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import AsyncIterator, Sequence

from lighthouse.connectors.base import (
    Connector,
    SourceDocument,
    parse_publish_date,
)

logger = logging.getLogger(__name__)


# Default — match what the old llama-index connector treated as docs.
_DEFAULT_EXTS: Sequence[str] = (".md", ".mdx", ".rst", ".txt")


class GitHubTreeConnector(Connector):
    """Yield markdown / RST files from a public GitHub repo tree."""

    name = "github_tree"

    def __init__(
        self,
        owner: str,
        repo: str,
        *,
        branch: str = "main",
        file_extensions: Sequence[str] | None = _DEFAULT_EXTS,
        include_paths: Sequence[str] | None = None,
        max_files: int = 2000,
        github_token: str | None = None,
        rate_limit_per_sec: float = 5.0,
    ) -> None:
        """
        Args:
            owner: GitHub user/org.
            repo: Repository name.
            branch: Branch ref. Resolved to the commit SHA for the
                tree walk so a force-push doesn't break a mid-flight
                pass.
            file_extensions: Tuple of extensions to keep. Pass ``None``
                to take everything (rarely useful).
            include_paths: Optional list of path prefixes ("docs/",
                "content/web/articles/") — keeps only blobs whose path
                starts with one of them. Useful for monorepos where
                the docs subtree is small.
            max_files: Hard cap on emitted documents. Default 2000 is
                comfortable for mid-sized repos; raise for monsters
                like mdn/content (consider 8000+ if you want full
                coverage).
            github_token: PAT. Falls back to env GITHUB_TOKEN. The
                unauthenticated rate limit (60/hr) would exhaust on
                the first batch.
            rate_limit_per_sec: Polite throttle for the raw-content
                fetches. 5/s is well below GitHub's 5000/hr
                authenticated cap.
        """
        self._owner = owner
        self._repo = repo
        self._branch = branch
        self._exts = tuple(file_extensions) if file_extensions else None
        self._include_paths = tuple(include_paths or ())
        self._max_files = max_files
        self._token = github_token or os.environ.get("GITHUB_TOKEN") or None
        self._rate_limit = max(0.5, rate_limit_per_sec)

    async def ingest(self) -> AsyncIterator[SourceDocument]:
        import httpx

        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "lighthouse-gh-tree/0.1",
        }
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"

        async with httpx.AsyncClient(
            timeout=60.0, follow_redirects=True
        ) as client:
            # 1. Resolve branch ref → commit SHA. The tree endpoint
            # works with either, but pinning the SHA makes a long
            # ingest reproducible.
            sha = await self._resolve_ref(client, headers)
            if sha is None:
                logger.warning(
                    "github_tree %s/%s: branch %r unresolved — abort",
                    self._owner,
                    self._repo,
                    self._branch,
                )
                return

            # 2. Walk the tree (single recursive call).
            tree = await self._fetch_tree(client, headers, sha)
            if not tree:
                return

            # 3. Filter to blobs we care about, capped.
            picks = self._filter(tree)
            logger.info(
                "github_tree %s/%s@%s: %d blobs selected (of %d total)",
                self._owner,
                self._repo,
                sha[:7],
                len(picks),
                len(tree),
            )

            # 4. Fetch raw content for each blob, polite throttle.
            delay = 1.0 / self._rate_limit
            slug = f"{self._owner}/{self._repo}"
            for blob in picks:
                path = blob.get("path") or ""
                raw_url = (
                    f"https://raw.githubusercontent.com/{slug}/{sha}/{path}"
                )
                try:
                    resp = await client.get(raw_url)
                    if resp.status_code != 200:
                        continue
                    body = resp.text
                except Exception:
                    logger.exception(
                        "github_tree fetch failed for %s — skipping", raw_url
                    )
                    continue
                if not body or len(body.strip()) < 100:
                    continue
                yield SourceDocument(
                    source_id=f"github-tree:{slug}@{sha[:7]}:{path}",
                    title=path,
                    body=body,
                    url=f"https://github.com/{slug}/blob/{sha}/{path}",
                    reference_time=parse_publish_date(blob.get("date")),
                    metadata={
                        "repo": slug,
                        "branch": self._branch,
                        "sha": sha,
                        "path": path,
                        "extractor": "github-tree",
                    },
                )
                await asyncio.sleep(delay)

    # ----- helpers --------------------------------------------------------

    async def _resolve_ref(self, client, headers) -> str | None:
        url = (
            f"https://api.github.com/repos/{self._owner}/"
            f"{self._repo}/branches/{self._branch}"
        )
        try:
            resp = await client.get(url, headers=headers)
            if resp.status_code != 200:
                logger.warning(
                    "github_tree branch lookup %s/%s/%s -> %d",
                    self._owner,
                    self._repo,
                    self._branch,
                    resp.status_code,
                )
                return None
            data = resp.json() or {}
            return ((data.get("commit") or {}).get("sha")) or None
        except Exception:
            logger.exception("github_tree branch lookup failed")
            return None

    async def _fetch_tree(self, client, headers, sha: str) -> list[dict]:
        url = (
            f"https://api.github.com/repos/{self._owner}/"
            f"{self._repo}/git/trees/{sha}?recursive=1"
        )
        try:
            resp = await client.get(url, headers=headers)
            if resp.status_code != 200:
                logger.warning(
                    "github_tree tree fetch %s/%s -> %d",
                    self._owner,
                    self._repo,
                    resp.status_code,
                )
                return []
            data = resp.json() or {}
        except Exception:
            logger.exception("github_tree tree fetch failed")
            return []
        if data.get("truncated"):
            logger.warning(
                "github_tree %s/%s tree was TRUNCATED — partial ingest "
                "(repo is huge; raise max_files or path-filter)",
                self._owner,
                self._repo,
            )
        return [b for b in (data.get("tree") or []) if b.get("type") == "blob"]

    def _filter(self, tree: list[dict]) -> list[dict]:
        out: list[dict] = []
        for b in tree:
            path = (b.get("path") or "").lower()
            if self._exts and not any(path.endswith(e) for e in self._exts):
                continue
            if self._include_paths and not any(
                path.startswith(p) for p in self._include_paths
            ):
                continue
            out.append(b)
            if len(out) >= self._max_files:
                break
        return out
