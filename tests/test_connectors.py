"""Connector unit tests.

For web + github we don't hit real services — that would make the
suite slow, flaky, and rate-limited. Instead we monkey-patch the
underlying LlamaIndex reader to return a canned list of
``llama_index.core.schema.Document`` instances and verify the
connector projects them into our :class:`SourceDocument` shape with
the right metadata mapping.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest


# --- WebConnector --------------------------------------------------------


async def test_web_connector_projects_metadata_and_url(monkeypatch) -> None:
    from lighthouse.connectors import web as web_module
    from lighthouse.connectors.web import WebConnector

    class FakeReader:
        def load_data(self, urls):
            # Mimic BeautifulSoupWebReader: returns Documents whose
            # metadata has ``URL`` (uppercase) and a ``title`` if the
            # page provided one.
            return [
                SimpleNamespace(
                    id_=f"doc-{i}",
                    text=f"body of {url}",
                    metadata={"URL": url, "title": f"Page {i}"},
                )
                for i, url in enumerate(urls)
            ]

    monkeypatch.setattr(
        web_module, "BeautifulSoupWebReader", FakeReader, raising=False
    )
    # Monkey-patch the lazy-imported symbol too.
    import sys

    fake_mod = SimpleNamespace(BeautifulSoupWebReader=FakeReader)
    monkeypatch.setitem(sys.modules, "llama_index.readers.web", fake_mod)

    urls = ["https://example.com/a", "https://example.com/b"]
    docs = [d async for d in WebConnector(urls).ingest()]
    assert len(docs) == 2
    assert docs[0].source_id == urls[0]
    assert docs[0].url == urls[0]
    assert docs[0].title == "Page 0"
    assert "body of" in docs[0].body
    assert docs[0].metadata["URL"] == urls[0]


async def test_web_connector_empty_url_list_yields_nothing() -> None:
    from lighthouse.connectors.web import WebConnector

    docs = [d async for d in WebConnector([]).ingest()]
    assert docs == []


# --- GitHubConnector -----------------------------------------------------


async def test_github_connector_projects_repo_metadata(monkeypatch) -> None:
    from lighthouse.connectors.github import GitHubConnector

    class FakeReader:
        class FilterType:
            INCLUDE = "include"
            EXCLUDE = "exclude"

        def __init__(self, **kwargs: Any) -> None:
            self.kwargs = kwargs

        def load_data(self, branch: str):
            return [
                SimpleNamespace(
                    id_="docs/intro.md",
                    text="# Intro\n\nWelcome.",
                    metadata={"file_path": "docs/intro.md", "file_name": "intro.md"},
                )
            ]

    class FakeClient:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            self.kwargs = kwargs

    import sys
    fake_mod = SimpleNamespace(
        GithubRepositoryReader=FakeReader,
        GithubClient=FakeClient,
    )
    monkeypatch.setitem(sys.modules, "llama_index.readers.github", fake_mod)

    connector = GitHubConnector(owner="fastapi", repo="fastapi", branch="master")
    docs = [d async for d in connector.ingest()]

    assert len(docs) == 1
    d = docs[0]
    assert d.source_id == "github:fastapi/fastapi@master:docs/intro.md"
    assert d.url == "https://github.com/fastapi/fastapi/blob/master/docs/intro.md"
    assert d.title == "docs/intro.md"
    assert d.metadata["repo"] == "fastapi/fastapi"
    assert d.metadata["branch"] == "master"


@pytest.mark.parametrize(
    "ext, expects_filter",
    [
        (None, False),       # caller opts out of filtering
        ([".md"], True),     # explicit filter passed through
    ],
)
async def test_github_filter_is_optional(
    monkeypatch, ext, expects_filter
) -> None:
    """The reader should only see ``filter_file_extensions`` when the
    connector was constructed with a non-None ext list. Regression
    fence so default doc-only filtering doesn't leak into a user
    who explicitly asked for all files."""
    from lighthouse.connectors.github import GitHubConnector

    seen_kwargs: dict[str, Any] = {}

    class FakeReader:
        class FilterType:
            INCLUDE = "include"
            EXCLUDE = "exclude"

        def __init__(self, **kwargs: Any) -> None:
            seen_kwargs.update(kwargs)

        def load_data(self, branch: str):
            return []

    class FakeClient:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

    import sys
    fake_mod = SimpleNamespace(
        GithubRepositoryReader=FakeReader,
        GithubClient=FakeClient,
    )
    monkeypatch.setitem(sys.modules, "llama_index.readers.github", fake_mod)

    conn = GitHubConnector(owner="o", repo="r", file_extensions=ext)
    [d async for d in conn.ingest()]
    assert ("filter_file_extensions" in seen_kwargs) is expects_filter
