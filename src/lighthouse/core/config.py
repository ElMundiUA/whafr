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

    # --- Anthropic (Librarian agent) ---
    anthropic_api_key: str = ""
    lighthouse_model_main: str = "claude-sonnet-4-6"
    lighthouse_model_fast: str = "claude-haiku-4-5-20251001"

    # --- OpenAI (embeddings + search-time reranker) ---
    # The flat-RAG engine uses OpenAI for chunk/query embeddings
    # (vector retrieval) and a small model (gpt-4o-mini) for the
    # post-hybrid reranker and the relevance gate.
    openai_api_key: str = ""
    openai_model: str = "gpt-4o-mini"
    openai_small_model: str = "gpt-4o-mini"
    openai_embedding_model: str = "text-embedding-3-small"
    openai_embedding_dim: int = 1024

    # --- Flat-RAG (Postgres + pgvector) ---
    # The retrieval engine's database (Neon recommended). Required —
    # an empty string makes every query raise at first use.
    lighthouse_pg_url: str = ""
    # Admin/API asyncpg pool bounds. The old hardcoded max of 5 queued
    # up under load; size to expected concurrent admin+retrieval QPS.
    lighthouse_pg_pool_min: int = 1
    lighthouse_pg_pool_max: int = 10

    # --- Per-workspace S3 ingestion ---
    # When a workspace is set up, the engine provisions a per-workspace
    # S3 importer pointed at ``s3://<bucket>/<workspace_id>/``. The
    # bucket (and optional S3-compatible endpoint / IAM keys) are
    # instance-level config; the prefix is derived from workspace_id.
    # Leave secret empty to rely on the instance's IAM role / boto
    # default credential chain.
    lighthouse_workspace_s3_bucket: str = ""
    lighthouse_workspace_s3_endpoint_url: str = ""
    lighthouse_workspace_s3_access_id: str = ""
    lighthouse_workspace_s3_access_secret: str = ""

    # --- API auth ---
    # Single shared key gates the proposal endpoint. Empty string means
    # "no auth" — fine for local dev, never for a public deployment.
    lighthouse_proposal_api_key: str = ""

    # Shared bearer for the admin surface (/v1/importers, /v1/webhooks,
    # /v1/corpus, /v1/analytics, /v1/keys). Unset → admin endpoints
    # return 401 unless LIGHTHOUSE_INSECURE_ADMIN=true explicitly opts
    # into open admin for local development.
    lighthouse_admin_token: str = ""
    lighthouse_insecure_admin: bool = False

    # When true, /v1/search, /v1/fetch_* and the MCP tools require a
    # per-workspace API key (Bearer lh_…, managed via /v1/keys); the
    # workspace is derived from the key, never from the client header.
    # Default false keeps the single-tenant/public-corpus behaviour.
    lighthouse_retrieval_auth_required: bool = False

    # Per-(workspace, client-IP) sliding-window limit on /v1/search.
    # 0 disables. In-process only — multiply by replica count, and put
    # a real limiter at the ingress for serious deployments.
    lighthouse_search_rate_limit_per_minute: int = 0

    # --- Search gap classifier ---
    # When enabled, every logged search additionally gets its hits
    # rated for usefulness by Claude Haiku (fire-and-forget, off the
    # request path; see core/usefulness.py). Searches whose average
    # rating falls below the useful-threshold are flagged as coverage
    # gaps even though hits came back — vector search almost never
    # returns zero hits, so this is what actually catches "uncertain
    # answers". Costs one Haiku call per search; off by default.
    lighthouse_gap_classifier_enabled: bool = False

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
