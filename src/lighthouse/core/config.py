"""Runtime configuration.

Single Pydantic Settings model so every subsystem reads from the same place.
Env values overlay file-loaded `.env`; nothing else has access to ``os.environ``.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # --- Graph backend ---
    falkordb_host: str = "localhost"
    falkordb_port: int = 6379
    falkordb_database: str = "lighthouse"

    # --- Anthropic (Librarian agent) ---
    anthropic_api_key: str = ""
    lighthouse_model_main: str = "claude-sonnet-4-6"
    lighthouse_model_fast: str = "claude-haiku-4-5-20251001"

    # --- OpenAI (Graphiti entity extraction + embeddings) ---
    # Graphiti needs both an LLM (for entity/relationship extraction
    # during ingest) and an embedder (for vector retrieval). We use
    # OpenAI for both today; Graphiti supports Gemini/Voyage/Anthropic
    # variants if we want to swap later.
    openai_api_key: str = ""
    openai_model: str = "gpt-4o-mini"
    openai_small_model: str = "gpt-4o-mini"
    openai_embedding_model: str = "text-embedding-3-small"
    openai_embedding_dim: int = 1024

    # --- API auth ---
    # Single shared key gates the proposal endpoint. Empty string means
    # "no auth" — fine for local dev, never for a public deployment.
    lighthouse_proposal_api_key: str = ""

    # --- Sources ---
    lighthouse_markdown_source: str = "./data/sources/markdown"

    # --- Docling (PDF / DOCX extraction sidecar) ---
    # When the WebConnector encounters a PDF URL it routes through
    # this service instead of trafilatura. Empty string disables
    # the PDF path — those URLs get skipped with a warning rather
    # than ingested as garbage.
    lighthouse_docling_url: str = "http://localhost:5001"

    # --- Firecrawl (whole-site crawl backend) ---
    # The CrawlConnector uses Firecrawl's cloud API to crawl an
    # entire domain or subtree. Handles SPA rendering, robots.txt,
    # and rate limits for us. Empty string disables the crawl path.
    firecrawl_api_key: str = ""
    firecrawl_base_url: str = "https://api.firecrawl.dev"

    # --- Relevance gate (cheap LLM filter before ingest) ---
    # Before paying for entity extraction + embeddings on every
    # crawled page, run a cheap classifier to decide if the page is
    # actually relevant to a role's knowledge surface. Set to "" to
    # disable; in that case every doc that passes the connector
    # makes it to add_episode.
    relevance_gate_model: str = "gpt-4o-mini"
    relevance_gate_enabled: bool = False

    # --- Proposal store ---
    # Local directory where the git-backed proposal store lives. One
    # markdown file per proposal; the directory is git-init'd on first
    # use so every state change becomes a commit.
    lighthouse_proposals_dir: str = "./data/proposals"

    # --- Source runner ---
    # YAML file listing sources to keep up to date (connector + args +
    # schedule). See README for the schema; default location keeps
    # ``lighthouse runner`` zero-arg if the file is at the conventional
    # path.
    lighthouse_runner_config: str = "./sources.yaml"
    # JSON file where the runner records each source's last run state
    # (timestamp, ok/err, doc count). Atomic-rename writes survive a
    # crash mid-flush.
    lighthouse_runner_state: str = "./data/runner-state.json"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Cached accessor — pydantic-settings re-parses env on every instantiation
    otherwise, which is wasteful for a value that's effectively immutable
    between process restarts."""
    return Settings()
