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

    # --- Graph backend (Neo4j 5.26 Community Edition) ---
    # Neo4j replaced FalkorDB as the default backend in v0.2 for
    # licensing reasons: Neo4j CE is GPLv3-licensed, FalkorDB is BSL
    # (source-available, not OSS). Graphiti's Neo4jDriver speaks Bolt
    # over the bolt:// URI scheme.
    neo4j_uri: str = "bolt://localhost:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: str = "neo4j_dev_password"
    neo4j_database: str = "neo4j"

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

    # --- Flat-RAG (Postgres + pgvector) ---
    # Dedicated Neon project. Empty string = flat path disabled;
    # legacy Graphiti via NEO4J_* keeps running. Holding both is
    # explicit so the A/B comparison runs against parallel
    # backends, not a half-migrated state.
    lighthouse_pg_url: str = ""

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

    # --- OpenRouter (multi-provider gateway) ---
    # Lets the OpenAI-compatible bench hit Anthropic / Google / DeepSeek
    # etc. through a single endpoint. Empty disables OpenRouter routing.
    openrouter_api_key: str = ""
    openrouter_base_url: str = "https://openrouter.ai/api/v1"

    # --- Relevance gate (cheap LLM filter before ingest) ---
    # Before paying for entity extraction + embeddings on every
    # crawled page, run a cheap classifier to decide if the page is
    # actually relevant to a role's knowledge surface. Set to "" to
    # disable; in that case every doc that passes the connector
    # makes it to add_episode.
    relevance_gate_model: str = "gpt-4o-mini"
    relevance_gate_enabled: bool = False

    # --- Sitemap-crawl failure log ---
    # Where SitemapCrawlConnector appends one JSONL row per URL it
    # couldn't extract (404, paywall, SPA shell, …). Operator reads
    # this offline to decide which sites need a JS-rendered backend.
    lighthouse_failed_urls_log: str = "./data/source-research/unparseable.jsonl"

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
