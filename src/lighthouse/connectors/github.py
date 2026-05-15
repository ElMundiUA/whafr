"""GitHub repository connector.

Wraps LlamaIndex's ``GithubRepositoryReader`` so we can pull docs
straight out of a GitHub repo. The use case is open-source projects
that publish their canonical reference (READMEs, ``/docs``, RFCs) in
markdown alongside the code — those are exactly the "Documentation-
derived" facts the Librarian accepts without proposal-side review.

Default filter: ``.md`` and ``.rst`` files only. Repos contain a lot of
non-doc noise (build configs, generated lockfiles, fixtures) we don't
want polluting the graph. Callers can override by passing
``file_extensions=None``.

Authentication: GitHub's REST API is rate-limited heavily for
unauthenticated callers (60 req/hr) — for any non-trivial repo
supply a ``GITHUB_TOKEN`` via env. We never embed it.
"""

from __future__ import annotations

import logging
import os
from collections.abc import AsyncIterator, Sequence
from typing import Any

from lighthouse.connectors.base import Connector, SourceDocument

logger = logging.getLogger(__name__)

_DEFAULT_DOC_EXTS: Sequence[str] = (".md", ".rst", ".mdx", ".txt")


class GitHubConnector(Connector):
    name = "github"

    def __init__(
        self,
        owner: str,
        repo: str,
        *,
        branch: str = "main",
        file_extensions: Sequence[str] | None = _DEFAULT_DOC_EXTS,
        github_token: str | None = None,
    ) -> None:
        self._owner = owner
        self._repo = repo
        self._branch = branch
        self._file_extensions = (
            tuple(file_extensions) if file_extensions is not None else None
        )
        # Token resolution order: explicit arg > env. Env-only is the
        # common CI pattern; passing explicitly is useful for tests.
        self._github_token = github_token or os.environ.get("GITHUB_TOKEN") or None

    async def ingest(self) -> AsyncIterator[SourceDocument]:
        from llama_index.readers.github import (
            GithubClient,
            GithubRepositoryReader,
        )

        client = GithubClient(github_token=self._github_token)
        reader_kwargs: dict[str, Any] = {
            "github_client": client,
            "owner": self._owner,
            "repo": self._repo,
        }
        if self._file_extensions is not None:
            reader_kwargs["filter_file_extensions"] = (
                list(self._file_extensions),
                GithubRepositoryReader.FilterType.INCLUDE,
            )

        reader = GithubRepositoryReader(**reader_kwargs)

        # LlamaIndex's GithubRepositoryReader is synchronous internally
        # but exposes an async-aware ``load_data``. We pass branch via
        # parameter name the reader expects.
        docs: list[Any] = reader.load_data(branch=self._branch)
        slug = f"{self._owner}/{self._repo}"
        for doc in docs:
            meta = doc.metadata or {}
            file_path = str(meta.get("file_path") or meta.get("file_name") or doc.id_)
            yield SourceDocument(
                source_id=f"github:{slug}@{self._branch}:{file_path}",
                title=file_path,
                body=doc.text,
                url=f"https://github.com/{slug}/blob/{self._branch}/{file_path}",
                reference_time=None,
                metadata={
                    "repo": slug,
                    "branch": self._branch,
                    "file_path": file_path,
                    **{str(k): str(v) for k, v in meta.items()},
                },
            )
