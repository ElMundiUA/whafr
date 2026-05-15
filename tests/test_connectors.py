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
    """Trafilatura returns a real article body; the connector projects
    that into ``SourceDocument`` with ``title``/``author``/``date``
    metadata. We mock the trafilatura functions at module level so
    tests don't make HTTP calls."""
    import sys

    body_text = (
        "Given When Then\n21 August 2013\n"
        + "Given-When-Then is a style of representing tests. " * 20
    )

    class FakeMeta:
        title = "Given When Then"
        author = "Martin Fowler"
        date = "2013-08-21"

    fake_trafilatura = SimpleNamespace(
        fetch_url=lambda url: f"<html><body>{url}</body></html>",
        extract=lambda raw, **kwargs: body_text,
        extract_metadata=lambda raw: FakeMeta,
    )
    monkeypatch.setitem(sys.modules, "trafilatura", fake_trafilatura)

    from lighthouse.connectors.web import WebConnector

    urls = ["https://example.com/a", "https://example.com/b"]
    docs = [d async for d in WebConnector(urls).ingest()]
    assert len(docs) == 2
    assert docs[0].source_id == urls[0]
    assert docs[0].url == urls[0]
    assert docs[0].title == "Given When Then"
    assert "Given-When-Then" in docs[0].body
    assert docs[0].metadata["author"] == "Martin Fowler"
    assert docs[0].metadata["date"] == "2013-08-21"


async def test_web_connector_skips_short_extractions(monkeypatch) -> None:
    """Pages where trafilatura extracts <100 chars (likely paywall /
    cookie wall / login redirect) are dropped, not ingested as
    garbage. Regression fence: the previous BSWR version silently
    ingested page chrome and poisoned entity extraction."""
    import sys

    fake_trafilatura = SimpleNamespace(
        fetch_url=lambda url: "<html><body>tiny</body></html>",
        extract=lambda raw, **kwargs: "too short",
        extract_metadata=lambda raw: SimpleNamespace(title=None, author=None, date=None),
    )
    monkeypatch.setitem(sys.modules, "trafilatura", fake_trafilatura)

    from lighthouse.connectors.web import WebConnector

    docs = [d async for d in WebConnector(["https://x.example/short"]).ingest()]
    assert docs == []


async def test_web_connector_routes_pdf_to_docling(monkeypatch, respx_mock=None) -> None:
    """PDF URLs go through docling-serve (not trafilatura). We mock
    the HTTP POST and assert the body is what we expect, plus the
    returned markdown lands on the SourceDocument."""
    from lighthouse.core import config

    config.get_settings.cache_clear()
    monkeypatch.setenv("LIGHTHOUSE_DOCLING_URL", "http://docling.test")

    captured_request: dict[str, Any] = {}

    class FakeResponse:
        def __init__(self, json_body: dict[str, Any]) -> None:
            self._body = json_body

        def raise_for_status(self) -> None:
            pass

        def json(self) -> dict[str, Any]:
            return self._body

    class FakeClient:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        async def __aenter__(self) -> "FakeClient":
            return self

        async def __aexit__(self, *args: Any) -> None:
            return None

        async def post(self, url: str, *, json: dict[str, Any]) -> FakeResponse:
            captured_request["url"] = url
            captured_request["body"] = json
            return FakeResponse(
                {
                    "document": {
                        "md_content": (
                            "# Shape Up\n\n" + "Some really long content. " * 30
                        ),
                    }
                }
            )

    import sys
    fake_httpx = SimpleNamespace(
        AsyncClient=FakeClient,
        HTTPError=Exception,
    )
    monkeypatch.setitem(sys.modules, "httpx", fake_httpx)

    from lighthouse.connectors.web import WebConnector

    docs = [d async for d in WebConnector(["https://basecamp.com/shapeup/shape-up.pdf"]).ingest()]
    config.get_settings.cache_clear()

    assert captured_request["url"] == "http://docling.test/v1/convert/source"
    assert captured_request["body"]["sources"][0]["kind"] == "http"
    assert captured_request["body"]["sources"][0]["url"].endswith(".pdf")
    assert len(docs) == 1
    assert docs[0].url.endswith(".pdf")
    assert "Shape Up" in docs[0].body
    assert docs[0].metadata["extractor"] == "docling"


async def test_web_connector_skips_pdf_when_docling_disabled(monkeypatch) -> None:
    """Empty LIGHTHOUSE_DOCLING_URL means we have no PDF backend —
    drop the URL with a warning rather than try trafilatura, which
    would produce garbage on a PDF."""
    from lighthouse.core import config

    config.get_settings.cache_clear()
    monkeypatch.setenv("LIGHTHOUSE_DOCLING_URL", "")

    from lighthouse.connectors.web import WebConnector

    docs = [d async for d in WebConnector(["https://example.com/whitepaper.pdf"]).ingest()]
    config.get_settings.cache_clear()
    assert docs == []


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
