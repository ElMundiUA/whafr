"""Tier-A llama-hub importer adapters.

Eight thin wrappers over `llama-index-readers-*` packages. Each is
gated by an optional-deps group; if the package isn't installed,
`make_reader()` raises `MissingExtraError` with a pip-install hint
instead of a raw ModuleNotFoundError.

All adapters subclass `LlamaHubImporter` from `adapters.llama_hub` —
the base supplies `build_connector()` which wraps the reader's
`load_data()` output via `LlamaHubDocumentConnector`.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from lighthouse.importers._optional import import_reader
from lighthouse.importers.adapters.llama_hub import LlamaHubImporter
from lighthouse.importers.base import ImporterMeta
from lighthouse.importers.registry import register

# ─────────────────────────── Notion ────────────────────────────


@register
class NotionImporter(LlamaHubImporter):
    meta = ImporterMeta(
        type="notion",
        display_name="Notion",
        description=(
            "Index a Notion workspace via the official API. Create an "
            "internal integration in Notion → Settings → Integrations, "
            "share the pages you want indexed with it, paste the token."
        ),
        config_schema={
            "type": "object",
            "required": ["integration_token"],
            "properties": {
                "integration_token": {
                    "type": "string",
                    "title": "Integration token",
                    "description": "Internal integration secret (secret_*).",
                    "format": "password",
                },
                "database_ids": {
                    "type": "string",
                    "title": "Database IDs (optional)",
                    "description": "Comma- or newline-separated. Empty = all shared pages.",
                    "format": "textarea",
                },
                "page_ids": {
                    "type": "string",
                    "title": "Page IDs (optional)",
                    "description": "Comma- or newline-separated specific page IDs.",
                    "format": "textarea",
                },
            },
        },
        secret_keys=("integration_token",),
    )

    def make_reader(self, config: Mapping[str, Any], secrets: Mapping[str, str]) -> Any:
        cls = import_reader(
            "llama_index.readers.notion", "NotionPageReader", "importers-notion"
        )
        return cls(integration_token=secrets["integration_token"])

    def load_kwargs(
        self, config: Mapping[str, Any], secrets: Mapping[str, str]
    ) -> Mapping[str, Any]:
        def _split(raw: str) -> list[str]:
            return [s.strip() for s in raw.replace(",", "\n").splitlines() if s.strip()]

        page_ids = _split(str(config.get("page_ids") or ""))
        db_ids = _split(str(config.get("database_ids") or ""))
        # NotionPageReader.load_data accepts page_ids OR database_id;
        # prefer page_ids when both given (more selective).
        if page_ids:
            return {"page_ids": page_ids}
        if db_ids:
            # The reader takes a single database_id; emit multiple by
            # falling back to combined page_ids if needed. For now
            # warn-and-pick-first.
            return {"database_id": db_ids[0]}
        return {}


# ─────────────────────────── Confluence ────────────────────────


@register
class ConfluenceImporter(LlamaHubImporter):
    meta = ImporterMeta(
        type="confluence",
        display_name="Confluence (Atlassian)",
        description=(
            "Index a Confluence space. Atlassian API token + your "
            "email; URL is your site's, e.g. acme.atlassian.net/wiki."
        ),
        config_schema={
            "type": "object",
            "required": ["base_url", "user_name", "space_key"],
            "properties": {
                "base_url": {
                    "type": "string",
                    "title": "Base URL",
                    "description": "e.g. https://acme.atlassian.net/wiki",
                    "format": "uri",
                },
                "user_name": {
                    "type": "string",
                    "title": "Email",
                    "description": "Atlassian account email.",
                },
                "space_key": {
                    "type": "string",
                    "title": "Space key",
                    "description": "e.g. DEV, OPS, RUNBOOKS.",
                },
                "api_token": {
                    "type": "string",
                    "title": "API token",
                    "description": "id.atlassian.com/manage-profile/security/api-tokens",
                    "format": "password",
                },
                "limit": {
                    "type": "integer",
                    "title": "Max pages",
                    "default": 0,
                    "minimum": 0,
                    "maximum": 100000,
                    "description": "0 = no cap.",
                },
            },
        },
        secret_keys=("api_token",),
    )

    def make_reader(self, config: Mapping[str, Any], secrets: Mapping[str, str]) -> Any:
        cls = import_reader(
            "llama_index.readers.confluence",
            "ConfluenceReader",
            "importers-confluence",
        )
        return cls(
            base_url=str(config["base_url"]),
            user_name=str(config["user_name"]),
            password=secrets["api_token"],
        )

    def load_kwargs(
        self, config: Mapping[str, Any], secrets: Mapping[str, str]
    ) -> Mapping[str, Any]:
        out: dict[str, Any] = {"space_key": str(config["space_key"])}
        cap = int(config.get("limit", 0) or 0)
        if cap > 0:
            out["max_num_results"] = cap
        return out


# ─────────────────────────── Jira ──────────────────────────────


@register
class JiraImporter(LlamaHubImporter):
    meta = ImporterMeta(
        type="jira",
        display_name="Jira (Atlassian)",
        description=(
            "Pull issues + comments via JQL. Useful as agent context: "
            "'what bugs are open on this surface?'"
        ),
        config_schema={
            "type": "object",
            "required": ["server_url", "email", "jql"],
            "properties": {
                "server_url": {
                    "type": "string",
                    "title": "Server URL",
                    "description": "e.g. https://acme.atlassian.net",
                    "format": "uri",
                },
                "email": {
                    "type": "string",
                    "title": "Email",
                },
                "api_token": {
                    "type": "string",
                    "title": "API token",
                    "format": "password",
                },
                "jql": {
                    "type": "string",
                    "title": "JQL query",
                    "description": "e.g. project = SHIP AND status != Done",
                    "format": "textarea",
                },
            },
        },
        secret_keys=("api_token",),
    )

    def make_reader(self, config: Mapping[str, Any], secrets: Mapping[str, str]) -> Any:
        cls = import_reader(
            "llama_index.readers.jira", "JiraReader", "importers-jira"
        )
        return cls(
            email=str(config["email"]),
            api_token=secrets["api_token"],
            server_url=str(config["server_url"]),
        )

    def load_kwargs(
        self, config: Mapping[str, Any], secrets: Mapping[str, str]
    ) -> Mapping[str, Any]:
        return {"query": str(config["jql"])}


# ─────────────────────────── GitLab ────────────────────────────


@register
class GitLabImporter(LlamaHubImporter):
    meta = ImporterMeta(
        type="gitlab",
        display_name="GitLab repository",
        description=(
            "Pull doc files from a GitLab repo (gitlab.com or self-hosted). "
            "PAT with read_repository scope."
        ),
        config_schema={
            "type": "object",
            "required": ["project_id", "private_token"],
            "properties": {
                "base_url": {
                    "type": "string",
                    "title": "GitLab base URL",
                    "description": "Default gitlab.com; set for self-hosted.",
                    "default": "https://gitlab.com",
                    "format": "uri",
                },
                "project_id": {
                    "type": "string",
                    "title": "Project ID or path",
                    "description": "Numeric ID, or namespaced path e.g. group/repo.",
                },
                "branch": {
                    "type": "string",
                    "title": "Branch",
                    "default": "main",
                },
                "file_extensions": {
                    "type": "string",
                    "title": "File extensions",
                    "default": ".md,.mdx,.rst,.txt",
                },
                "private_token": {
                    "type": "string",
                    "title": "Private token (PAT)",
                    "format": "password",
                },
            },
        },
        secret_keys=("private_token",),
    )

    def make_reader(self, config: Mapping[str, Any], secrets: Mapping[str, str]) -> Any:
        cls = import_reader(
            "llama_index.readers.gitlab", "GitLabRepositoryReader", "importers-gitlab"
        )
        return cls(
            url=str(config.get("base_url") or "https://gitlab.com"),
            private_token=secrets["private_token"],
            project_id=str(config["project_id"]),
        )

    def load_kwargs(
        self, config: Mapping[str, Any], secrets: Mapping[str, str]
    ) -> Mapping[str, Any]:
        exts = [
            e.strip()
            for e in str(config.get("file_extensions") or ".md,.mdx,.rst,.txt").split(",")
            if e.strip()
        ]
        return {
            "ref": str(config.get("branch") or "main"),
            "file_extensions": exts,
        }


# ─────────────────────────── Bitbucket ─────────────────────────


@register
class BitbucketImporter(LlamaHubImporter):
    meta = ImporterMeta(
        type="bitbucket",
        display_name="Bitbucket repository",
        description=(
            "Doc files from a Bitbucket Cloud workspace. Auth via "
            "username + app password (read scope)."
        ),
        config_schema={
            "type": "object",
            "required": ["workspace", "repo_slug", "username"],
            "properties": {
                "workspace": {
                    "type": "string",
                    "title": "Workspace",
                },
                "repo_slug": {
                    "type": "string",
                    "title": "Repo slug",
                },
                "branch": {
                    "type": "string",
                    "title": "Branch",
                    "default": "main",
                },
                "username": {
                    "type": "string",
                    "title": "Username",
                },
                "app_password": {
                    "type": "string",
                    "title": "App password",
                    "format": "password",
                },
            },
        },
        secret_keys=("app_password",),
    )

    def make_reader(self, config: Mapping[str, Any], secrets: Mapping[str, str]) -> Any:
        cls = import_reader(
            "llama_index.readers.bitbucket",
            "BitbucketReader",
            "importers-bitbucket",
        )
        return cls(
            base_url=f"https://api.bitbucket.org/2.0/repositories/"
                    f"{config['workspace']}/{config['repo_slug']}/src/"
                    f"{config.get('branch') or 'main'}",
            project_key=str(config["workspace"]),
            branch=str(config.get("branch") or "main"),
            repository=str(config["repo_slug"]),
            extensions=[".md", ".rst", ".txt"],
        )


# ─────────────────────────── Linear ────────────────────────────


@register
class LinearImporter(LlamaHubImporter):
    meta = ImporterMeta(
        type="linear",
        display_name="Linear issues",
        description=(
            "Pull issues / comments from a Linear workspace via a "
            "personal API key (Settings → API)."
        ),
        config_schema={
            "type": "object",
            "required": ["api_key", "team_id"],
            "properties": {
                "api_key": {
                    "type": "string",
                    "title": "API key",
                    "description": "Personal API key from linear.app/settings/api",
                    "format": "password",
                },
                "team_id": {
                    "type": "string",
                    "title": "Team ID",
                    "description": "UUID of the team to index.",
                },
            },
        },
        secret_keys=("api_key",),
    )

    def make_reader(self, config: Mapping[str, Any], secrets: Mapping[str, str]) -> Any:
        cls = import_reader(
            "llama_index.readers.linear", "LinearReader", "importers-linear"
        )
        return cls(api_key=secrets["api_key"])

    def load_kwargs(
        self, config: Mapping[str, Any], secrets: Mapping[str, str]
    ) -> Mapping[str, Any]:
        return {"team_id": str(config["team_id"])}


# ─────────────────────────── Wikipedia ─────────────────────────


@register
class WikipediaImporter(LlamaHubImporter):
    meta = ImporterMeta(
        type="wikipedia",
        display_name="Wikipedia articles",
        description=(
            "Pull a hand-picked list of Wikipedia article titles. "
            "Good for stable canonical references the agent should know."
        ),
        config_schema={
            "type": "object",
            "required": ["pages"],
            "properties": {
                "pages": {
                    "type": "string",
                    "title": "Article titles",
                    "description": "One title per line (matches Wikipedia URL slug).",
                    "format": "textarea",
                },
                "language": {
                    "type": "string",
                    "title": "Language code",
                    "default": "en",
                },
            },
        },
        secret_keys=(),
    )

    def make_reader(self, config: Mapping[str, Any], secrets: Mapping[str, str]) -> Any:
        cls = import_reader(
            "llama_index.readers.wikipedia",
            "WikipediaReader",
            "importers-wikipedia",
        )
        return cls()

    def load_kwargs(
        self, config: Mapping[str, Any], secrets: Mapping[str, str]
    ) -> Mapping[str, Any]:
        pages = [
            p.strip()
            for p in str(config.get("pages") or "").splitlines()
            if p.strip()
        ]
        return {
            "pages": pages,
            "lang_prefix": str(config.get("language") or "en"),
        }


# ───────────────────────── Local files ─────────────────────────


@register
class LocalFilesImporter(LlamaHubImporter):
    meta = ImporterMeta(
        type="local_files",
        display_name="Local files / directory",
        description=(
            "Index a directory mounted on the engine host. "
            "PDF / DOCX / EPUB / Markdown / CSV / JSON via SimpleDirectoryReader."
        ),
        config_schema={
            "type": "object",
            "required": ["path"],
            "properties": {
                "path": {
                    "type": "string",
                    "title": "Path",
                    "description": (
                        "Absolute directory or file path inside the engine "
                        "container. Mount via Docker volume / k8s volume."
                    ),
                },
                "recursive": {
                    "type": "boolean",
                    "title": "Walk subdirectories",
                    "default": True,
                },
                "extensions": {
                    "type": "string",
                    "title": "File extensions (optional)",
                    "description": (
                        "Comma-separated, e.g. .md,.pdf,.txt. "
                        "Empty = let the reader autodetect."
                    ),
                },
            },
        },
        secret_keys=(),
    )

    def make_reader(self, config: Mapping[str, Any], secrets: Mapping[str, str]) -> Any:
        # SimpleDirectoryReader is in llama-index-core, not a hub package
        # — guard it anyway in case future versions split it out.
        cls = import_reader(
            "llama_index.core",
            "SimpleDirectoryReader",
            "importers-file",
        )
        path = Path(str(config["path"]))
        exts_raw = str(config.get("extensions") or "").strip()
        exts = (
            [e.strip() for e in exts_raw.split(",") if e.strip()]
            if exts_raw
            else None
        )
        if path.is_file():
            return cls(input_files=[str(path)], required_exts=exts)
        return cls(
            input_dir=str(path),
            recursive=bool(config.get("recursive", True)),
            required_exts=exts,
        )
