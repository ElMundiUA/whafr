"""Tier-B llama-hub importer adapters.

Seventeen wrappers covering: chat (slack, discord), cloud storage
(s3, gcs, azure-blob, dropbox, google-drive), project management
(asana, trello), customer support (zendesk, intercom), databases
(database, mongodb), research (arxiv), and community forums
(reddit, discourse), plus structured records (airtable).

Same convention as `llama_tier_a`: subclass `LlamaHubImporter`,
declare schema + secret keys, lazy-import the reader inside
`make_reader()` so a slim build doesn't crash at module-load time.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from lighthouse.importers._optional import import_reader
from lighthouse.importers.adapters.llama_hub import LlamaHubImporter
from lighthouse.importers.base import ImporterMeta
from lighthouse.importers.registry import register

_LIST_SPLIT = re.compile(r"[\s,]+")


def _split_csv(raw: str) -> list[str]:
    return [s for s in _LIST_SPLIT.split(raw.strip()) if s]


# ─────────────────────────── Slack ────────────────────────────


@register
class SlackImporter(LlamaHubImporter):
    meta = ImporterMeta(
        type="slack",
        display_name="Slack",
        description=(
            "Archive of Slack channels the bot is invited to. "
            "Create a Slack app, enable channels:history + groups:history, "
            "install to your workspace, paste the bot token."
        ),
        config_schema={
            "type": "object",
            "required": ["slack_token", "channel_ids"],
            "properties": {
                "slack_token": {
                    "type": "string",
                    "title": "Bot token (xoxb-…)",
                    "format": "password",
                },
                "channel_ids": {
                    "type": "string",
                    "title": "Channel IDs",
                    "description": "Comma- or newline-separated channel IDs (C…).",
                    "format": "textarea",
                },
                "earliest_date": {
                    "type": "string",
                    "title": "Earliest date (ISO, optional)",
                    "description": "YYYY-MM-DD; messages older than this are skipped.",
                },
            },
        },
        secret_keys=("slack_token",),
    )

    def make_reader(self, config: Mapping[str, Any], secrets: Mapping[str, str]) -> Any:
        cls = import_reader(
            "llama_index.readers.slack", "SlackReader", "importers-slack"
        )
        kwargs: dict[str, Any] = {"slack_token": secrets["slack_token"]}
        if config.get("earliest_date"):
            kwargs["earliest_date"] = str(config["earliest_date"])
        return cls(**kwargs)

    def load_kwargs(
        self, config: Mapping[str, Any], secrets: Mapping[str, str]
    ) -> Mapping[str, Any]:
        return {"channel_ids": _split_csv(str(config.get("channel_ids") or ""))}


# ─────────────────────────── Discord ──────────────────────────


@register
class DiscordImporter(LlamaHubImporter):
    meta = ImporterMeta(
        type="discord",
        display_name="Discord",
        description=(
            "Messages from Discord channels the bot can read. "
            "Create a bot at discord.com/developers, enable Message "
            "Content Intent, invite to your server."
        ),
        config_schema={
            "type": "object",
            "required": ["discord_token", "channel_ids"],
            "properties": {
                "discord_token": {
                    "type": "string",
                    "title": "Bot token",
                    "format": "password",
                },
                "channel_ids": {
                    "type": "string",
                    "title": "Channel IDs",
                    "description": "Comma- or newline-separated numeric channel IDs.",
                    "format": "textarea",
                },
                "limit": {
                    "type": "integer",
                    "title": "Messages per channel (0 = all)",
                    "default": 1000,
                    "minimum": 0,
                    "maximum": 1000000,
                },
            },
        },
        secret_keys=("discord_token",),
    )

    def make_reader(self, config: Mapping[str, Any], secrets: Mapping[str, str]) -> Any:
        cls = import_reader(
            "llama_index.readers.discord", "DiscordReader", "importers-discord"
        )
        return cls(discord_token=secrets["discord_token"])

    def load_kwargs(
        self, config: Mapping[str, Any], secrets: Mapping[str, str]
    ) -> Mapping[str, Any]:
        ids = [int(s) for s in _split_csv(str(config.get("channel_ids") or "")) if s.isdigit()]
        out: dict[str, Any] = {"channel_ids": ids}
        cap = int(config.get("limit", 1000) or 0)
        if cap > 0:
            out["limit"] = cap
        return out


# ──────────────────────────── S3 ──────────────────────────────


@register
class S3Importer(LlamaHubImporter):
    meta = ImporterMeta(
        type="s3",
        display_name="Amazon S3",
        description=(
            "Documents stored in S3 (or any S3-compatible store via "
            "endpoint_url). IAM keys; bucket required."
        ),
        config_schema={
            "type": "object",
            "required": ["bucket"],
            "properties": {
                "bucket": {"type": "string", "title": "Bucket"},
                "prefix": {
                    "type": "string",
                    "title": "Prefix (optional)",
                    "description": "e.g. runbooks/ to scope the walk.",
                },
                "aws_access_id": {
                    "type": "string",
                    "title": "AWS access key id",
                },
                "aws_access_secret": {
                    "type": "string",
                    "title": "AWS secret access key",
                    "format": "password",
                },
                "s3_endpoint_url": {
                    "type": "string",
                    "title": "Endpoint URL (optional)",
                    "description": "Set for MinIO / DO Spaces / R2.",
                },
            },
        },
        secret_keys=("aws_access_secret",),
    )

    def make_reader(self, config: Mapping[str, Any], secrets: Mapping[str, str]) -> Any:
        cls = import_reader("llama_index.readers.s3", "S3Reader", "importers-s3")
        kwargs: dict[str, Any] = {"bucket": str(config["bucket"])}
        if config.get("prefix"):
            kwargs["prefix"] = str(config["prefix"])
        if config.get("aws_access_id"):
            kwargs["aws_access_id"] = str(config["aws_access_id"])
        if secrets.get("aws_access_secret"):
            kwargs["aws_access_secret"] = secrets["aws_access_secret"]
        if config.get("s3_endpoint_url"):
            kwargs["s3_endpoint_url"] = str(config["s3_endpoint_url"])
        return cls(**kwargs)


# ──────────────────────────── GCS ─────────────────────────────


@register
class GCSImporter(LlamaHubImporter):
    meta = ImporterMeta(
        type="gcs",
        display_name="Google Cloud Storage",
        description=(
            "Documents in a GCS bucket. Service-account JSON authentication."
        ),
        config_schema={
            "type": "object",
            "required": ["bucket", "service_account_key"],
            "properties": {
                "bucket": {"type": "string", "title": "Bucket"},
                "prefix": {
                    "type": "string",
                    "title": "Prefix (optional)",
                },
                "service_account_key": {
                    "type": "string",
                    "title": "Service-account JSON",
                    "description": (
                        "Full JSON contents. SA needs "
                        "Storage Object Viewer."
                    ),
                    "format": "textarea",
                },
            },
        },
        secret_keys=("service_account_key",),
    )

    def make_reader(self, config: Mapping[str, Any], secrets: Mapping[str, str]) -> Any:
        import json

        cls = import_reader("llama_index.readers.gcs", "GCSReader", "importers-gcs")
        kwargs: dict[str, Any] = {"bucket": str(config["bucket"])}
        if config.get("prefix"):
            kwargs["prefix"] = str(config["prefix"])
        sa = secrets.get("service_account_key")
        if sa:
            try:
                kwargs["service_account_key_json"] = json.loads(sa)
            except json.JSONDecodeError:
                kwargs["service_account_key_path"] = sa
        return cls(**kwargs)


# ───────────────────────── Azure Blob ─────────────────────────


@register
class AzureBlobImporter(LlamaHubImporter):
    meta = ImporterMeta(
        type="azure_blob",
        display_name="Azure Blob Storage",
        description=(
            "Documents in an Azure Storage container. Auth via "
            "connection string (simplest) or account URL + key."
        ),
        config_schema={
            "type": "object",
            "required": ["container_name"],
            "properties": {
                "container_name": {"type": "string", "title": "Container"},
                "blob": {
                    "type": "string",
                    "title": "Blob name (optional)",
                    "description": "Empty = walk the whole container.",
                },
                "connection_string": {
                    "type": "string",
                    "title": "Connection string",
                    "format": "password",
                },
            },
        },
        secret_keys=("connection_string",),
    )

    def make_reader(self, config: Mapping[str, Any], secrets: Mapping[str, str]) -> Any:
        cls = import_reader(
            "llama_index.readers.azstorage_blob",
            "AzStorageBlobReader",
            "importers-azure-blob",
        )
        kwargs: dict[str, Any] = {"container_name": str(config["container_name"])}
        if config.get("blob"):
            kwargs["blob"] = str(config["blob"])
        if secrets.get("connection_string"):
            kwargs["connection_string"] = secrets["connection_string"]
        return cls(**kwargs)


# ─────────────────────────── Box ──────────────────────────────


@register
class BoxImporter(LlamaHubImporter):
    meta = ImporterMeta(
        type="box",
        display_name="Box",
        description=(
            "Files from a Box folder. JWT-auth via developer-token "
            "(quick-start) or service-account JSON for production."
        ),
        config_schema={
            "type": "object",
            "required": ["developer_token"],
            "properties": {
                "developer_token": {
                    "type": "string",
                    "title": "Developer token",
                    "description": (
                        "From a custom Box app (Authentication → "
                        "Generate Developer Token). 60-minute lifetime; "
                        "rotate via JWT or OAuth for long-running ingest."
                    ),
                    "format": "password",
                },
                "folder_id": {
                    "type": "string",
                    "title": "Folder ID (optional)",
                    "description": "Root = '0'. Find in the URL bar.",
                    "default": "0",
                },
            },
        },
        secret_keys=("developer_token",),
    )

    def make_reader(self, config: Mapping[str, Any], secrets: Mapping[str, str]) -> Any:
        cls = import_reader(
            "llama_index.readers.box", "BoxReader", "importers-box"
        )
        return cls(box_developer_token=secrets["developer_token"])

    def load_kwargs(
        self, config: Mapping[str, Any], secrets: Mapping[str, str]
    ) -> Mapping[str, Any]:
        return {"folder_id": str(config.get("folder_id") or "0")}


# ──────────────────────── Google Drive ────────────────────────


@register
class GoogleDriveImporter(LlamaHubImporter):
    meta = ImporterMeta(
        type="google_drive",
        display_name="Google Drive",
        description=(
            "Files in a Drive folder. Use a service-account JSON; "
            "share the folder with the SA's email address."
        ),
        config_schema={
            "type": "object",
            "required": ["folder_id", "service_account_key"],
            "properties": {
                "folder_id": {
                    "type": "string",
                    "title": "Folder ID",
                    "description": "From the Drive URL, the part after /folders/.",
                },
                "service_account_key": {
                    "type": "string",
                    "title": "Service-account JSON",
                    "description": (
                        "Full JSON; SA needs Drive read scope, "
                        "share folder with SA email."
                    ),
                    "format": "textarea",
                },
            },
        },
        secret_keys=("service_account_key",),
    )

    def make_reader(self, config: Mapping[str, Any], secrets: Mapping[str, str]) -> Any:
        import json

        cls = import_reader(
            "llama_index.readers.google",
            "GoogleDriveReader",
            "importers-google-drive",
        )
        kwargs: dict[str, Any] = {"folder_id": str(config["folder_id"])}
        sa = secrets.get("service_account_key")
        if sa:
            try:
                kwargs["service_account_key"] = json.loads(sa)
            except json.JSONDecodeError:
                kwargs["service_account_key_path"] = sa
        return cls(**kwargs)


# ─────────────────────────── Asana ────────────────────────────


@register
class AsanaImporter(LlamaHubImporter):
    meta = ImporterMeta(
        type="asana",
        display_name="Asana",
        description=(
            "Asana projects + tasks (with descriptions, comments). "
            "Generate a PAT at app.asana.com/0/my-apps."
        ),
        config_schema={
            "type": "object",
            "required": ["asana_token"],
            "properties": {
                "asana_token": {
                    "type": "string",
                    "title": "Personal Access Token",
                    "format": "password",
                },
                "workspace_id": {
                    "type": "string",
                    "title": "Workspace ID (optional)",
                },
                "project_id": {
                    "type": "string",
                    "title": "Project ID (optional)",
                    "description": "Empty = walk the workspace.",
                },
            },
        },
        secret_keys=("asana_token",),
    )

    def make_reader(self, config: Mapping[str, Any], secrets: Mapping[str, str]) -> Any:
        cls = import_reader(
            "llama_index.readers.asana", "AsanaReader", "importers-asana"
        )
        return cls(asana_token=secrets["asana_token"])

    def load_kwargs(
        self, config: Mapping[str, Any], secrets: Mapping[str, str]
    ) -> Mapping[str, Any]:
        out: dict[str, Any] = {}
        if config.get("workspace_id"):
            out["workspace_id"] = str(config["workspace_id"])
        if config.get("project_id"):
            out["project_id"] = str(config["project_id"])
        return out


# ─────────────────────────── Trello ───────────────────────────


@register
class TrelloImporter(LlamaHubImporter):
    meta = ImporterMeta(
        type="trello",
        display_name="Trello",
        description=(
            "Cards from a Trello board (title, description, comments). "
            "API key + token from trello.com/app-key."
        ),
        config_schema={
            "type": "object",
            "required": ["api_key", "board_id"],
            "properties": {
                "api_key": {
                    "type": "string",
                    "title": "API key",
                },
                "api_token": {
                    "type": "string",
                    "title": "API token",
                    "format": "password",
                },
                "board_id": {"type": "string", "title": "Board ID"},
            },
        },
        secret_keys=("api_token",),
    )

    def make_reader(self, config: Mapping[str, Any], secrets: Mapping[str, str]) -> Any:
        cls = import_reader(
            "llama_index.readers.trello", "TrelloReader", "importers-trello"
        )
        return cls(api_key=str(config["api_key"]), api_token=secrets["api_token"])

    def load_kwargs(
        self, config: Mapping[str, Any], secrets: Mapping[str, str]
    ) -> Mapping[str, Any]:
        return {"board_id": str(config["board_id"])}


# ─────────────────────────── Zendesk ──────────────────────────


@register
class ZendeskImporter(LlamaHubImporter):
    meta = ImporterMeta(
        type="zendesk",
        display_name="Zendesk help center",
        description=(
            "Articles from a Zendesk Guide knowledge base. Public KBs "
            "don't need auth; private ones use email + API token."
        ),
        config_schema={
            "type": "object",
            "required": ["zendesk_subdomain"],
            "properties": {
                "zendesk_subdomain": {
                    "type": "string",
                    "title": "Subdomain",
                    "description": "The part before .zendesk.com (acme → acme.zendesk.com).",
                },
                "locale": {
                    "type": "string",
                    "title": "Locale",
                    "default": "en-us",
                },
            },
        },
        secret_keys=(),
    )

    def make_reader(self, config: Mapping[str, Any], secrets: Mapping[str, str]) -> Any:
        cls = import_reader(
            "llama_index.readers.zendesk", "ZendeskReader", "importers-zendesk"
        )
        return cls(
            zendesk_subdomain=str(config["zendesk_subdomain"]),
            locale=str(config.get("locale") or "en-us"),
        )


# ─────────────────────────── Intercom ─────────────────────────


@register
class IntercomImporter(LlamaHubImporter):
    meta = ImporterMeta(
        type="intercom",
        display_name="Intercom help center",
        description=(
            "Public articles from Intercom Help Center. Generate an "
            "access token in Intercom Developer Hub."
        ),
        config_schema={
            "type": "object",
            "required": ["intercom_access_token"],
            "properties": {
                "intercom_access_token": {
                    "type": "string",
                    "title": "Access token",
                    "format": "password",
                },
            },
        },
        secret_keys=("intercom_access_token",),
    )

    def make_reader(self, config: Mapping[str, Any], secrets: Mapping[str, str]) -> Any:
        cls = import_reader(
            "llama_index.readers.intercom",
            "IntercomReader",
            "importers-intercom",
        )
        return cls(intercom_access_token=secrets["intercom_access_token"])


# ────────────────────────── Database ──────────────────────────


@register
class DatabaseImporter(LlamaHubImporter):
    meta = ImporterMeta(
        type="database",
        display_name="SQL database",
        description=(
            "Rows from a SQL database, returned as documents per query. "
            "SQLAlchemy URI."
        ),
        config_schema={
            "type": "object",
            "required": ["uri", "query"],
            "properties": {
                "uri": {
                    "type": "string",
                    "title": "SQLAlchemy URI",
                    "description": (
                        "e.g. postgresql+psycopg://user:pw@host/db, "
                        "mysql+pymysql://…, sqlite:///./data.db"
                    ),
                    "format": "password",
                },
                "query": {
                    "type": "string",
                    "title": "SQL query",
                    "description": "SELECT … — each row becomes a Document.",
                    "format": "textarea",
                },
            },
        },
        secret_keys=("uri",),
    )

    def make_reader(self, config: Mapping[str, Any], secrets: Mapping[str, str]) -> Any:
        cls = import_reader(
            "llama_index.readers.database",
            "DatabaseReader",
            "importers-database",
        )
        return cls(uri=secrets["uri"])

    def load_kwargs(
        self, config: Mapping[str, Any], secrets: Mapping[str, str]
    ) -> Mapping[str, Any]:
        return {"query": str(config["query"])}


# ─────────────────────────── MongoDB ──────────────────────────


@register
class MongoDBImporter(LlamaHubImporter):
    meta = ImporterMeta(
        type="mongodb",
        display_name="MongoDB",
        description=(
            "Documents from a MongoDB collection. Connection URI + "
            "database + collection."
        ),
        config_schema={
            "type": "object",
            "required": ["uri", "db_name", "collection_name"],
            "properties": {
                "uri": {
                    "type": "string",
                    "title": "MongoDB URI",
                    "description": "mongodb://… or mongodb+srv://…",
                    "format": "password",
                },
                "db_name": {"type": "string", "title": "Database"},
                "collection_name": {"type": "string", "title": "Collection"},
                "field_names": {
                    "type": "string",
                    "title": "Text fields",
                    "description": (
                        "Comma-separated field names whose values "
                        "become the document body."
                    ),
                    "default": "text",
                },
                "max_docs": {
                    "type": "integer",
                    "title": "Max docs (0 = all)",
                    "default": 0,
                    "minimum": 0,
                    "maximum": 1000000,
                },
            },
        },
        secret_keys=("uri",),
    )

    def make_reader(self, config: Mapping[str, Any], secrets: Mapping[str, str]) -> Any:
        cls = import_reader(
            "llama_index.readers.mongodb",
            "SimpleMongoReader",
            "importers-mongodb",
        )
        cap = int(config.get("max_docs", 0) or 0)
        return cls(uri=secrets["uri"], max_docs=cap if cap > 0 else 1000)

    def load_kwargs(
        self, config: Mapping[str, Any], secrets: Mapping[str, str]
    ) -> Mapping[str, Any]:
        fields = [
            s.strip()
            for s in str(config.get("field_names") or "text").split(",")
            if s.strip()
        ]
        return {
            "db_name": str(config["db_name"]),
            "collection_name": str(config["collection_name"]),
            "field_names": fields,
        }


# ──────────────────────────── arXiv ───────────────────────────


@register
class ArxivImporter(LlamaHubImporter):
    meta = ImporterMeta(
        type="arxiv",
        display_name="arXiv papers",
        description=(
            "Pull recent papers matching a query from arxiv.org. No auth."
        ),
        config_schema={
            "type": "object",
            "required": ["query"],
            "properties": {
                "query": {
                    "type": "string",
                    "title": "Search query",
                    "description": (
                        "Free-text or arXiv-API syntax "
                        "(e.g. 'cat:cs.LG AND retrieval')."
                    ),
                },
                "max_results": {
                    "type": "integer",
                    "title": "Max papers",
                    "default": 50,
                    "minimum": 1,
                    "maximum": 1000,
                },
            },
        },
        secret_keys=(),
    )

    def make_reader(self, config: Mapping[str, Any], secrets: Mapping[str, str]) -> Any:
        cls = import_reader(
            "llama_index.readers.papers", "ArxivReader", "importers-arxiv"
        )
        return cls()

    def load_kwargs(
        self, config: Mapping[str, Any], secrets: Mapping[str, str]
    ) -> Mapping[str, Any]:
        return {
            "search_query": str(config["query"]),
            "max_results": int(config.get("max_results", 50)),
        }


# ─────────────────────────── Airtable ─────────────────────────


@register
class AirtableImporter(LlamaHubImporter):
    meta = ImporterMeta(
        type="airtable",
        display_name="Airtable",
        description=(
            "Records from one Airtable table. PAT from airtable.com/create/tokens."
        ),
        config_schema={
            "type": "object",
            "required": ["api_key", "base_id", "table_id"],
            "properties": {
                "api_key": {
                    "type": "string",
                    "title": "Personal Access Token",
                    "format": "password",
                },
                "base_id": {
                    "type": "string",
                    "title": "Base ID",
                    "description": "From the API URL: appXXXXXXXX.",
                },
                "table_id": {
                    "type": "string",
                    "title": "Table ID or name",
                },
            },
        },
        secret_keys=("api_key",),
    )

    def make_reader(self, config: Mapping[str, Any], secrets: Mapping[str, str]) -> Any:
        cls = import_reader(
            "llama_index.readers.airtable",
            "AirtableReader",
            "importers-airtable",
        )
        return cls(api_key=secrets["api_key"])

    def load_kwargs(
        self, config: Mapping[str, Any], secrets: Mapping[str, str]
    ) -> Mapping[str, Any]:
        return {
            "table_id": str(config["table_id"]),
            "base_id": str(config["base_id"]),
        }


# ─────────────────────────── Reddit ───────────────────────────


@register
class RedditImporter(LlamaHubImporter):
    meta = ImporterMeta(
        type="reddit",
        display_name="Reddit",
        description=(
            "Posts + top comments from one or more subreddits. Create "
            "a script-type app at reddit.com/prefs/apps."
        ),
        config_schema={
            "type": "object",
            "required": ["client_id", "client_secret", "user_agent", "subreddits", "search_keys"],
            "properties": {
                "client_id": {"type": "string", "title": "Client ID"},
                "client_secret": {
                    "type": "string",
                    "title": "Client secret",
                    "format": "password",
                },
                "user_agent": {
                    "type": "string",
                    "title": "User-Agent",
                    "description": "Free-text; Reddit asks for descriptive UAs.",
                },
                "subreddits": {
                    "type": "string",
                    "title": "Subreddits",
                    "description": "Comma- or newline-separated names (no r/ prefix).",
                    "format": "textarea",
                },
                "search_keys": {
                    "type": "string",
                    "title": "Search terms",
                    "description": "Each term yields posts; comma- or newline-separated.",
                    "format": "textarea",
                },
                "post_limit": {
                    "type": "integer",
                    "title": "Posts per term",
                    "default": 20,
                    "minimum": 1,
                    "maximum": 500,
                },
            },
        },
        secret_keys=("client_secret",),
    )

    def make_reader(self, config: Mapping[str, Any], secrets: Mapping[str, str]) -> Any:
        cls = import_reader(
            "llama_index.readers.reddit", "RedditReader", "importers-reddit"
        )
        return cls()

    def load_kwargs(
        self, config: Mapping[str, Any], secrets: Mapping[str, str]
    ) -> Mapping[str, Any]:
        return {
            "client_id": str(config["client_id"]),
            "client_secret": secrets["client_secret"],
            "user_agent": str(config["user_agent"]),
            "subreddits": _split_csv(str(config["subreddits"])),
            "search_keys": _split_csv(str(config["search_keys"])),
            "post_limit": int(config.get("post_limit", 20)),
        }


# ───────────────────────── Stack Overflow ─────────────────────


@register
class StackOverflowImporter(LlamaHubImporter):
    meta = ImporterMeta(
        type="stackoverflow",
        display_name="Stack Overflow / Stack Exchange",
        description=(
            "Q&A from a team's Stack Overflow for Teams. Provide the "
            "team slug + access token from the team's admin panel."
        ),
        config_schema={
            "type": "object",
            "required": ["team_name", "access_token"],
            "properties": {
                "team_name": {
                    "type": "string",
                    "title": "Team slug",
                    "description": "e.g. 'acme' for stackoverflowteams.com/c/acme.",
                },
                "access_token": {
                    "type": "string",
                    "title": "Access token",
                    "description": (
                        "From Stack Overflow for Teams admin → API → "
                        "Personal Access Tokens."
                    ),
                    "format": "password",
                },
                "cache_dir": {
                    "type": "string",
                    "title": "Cache dir (optional)",
                    "description": "Reader caches paged results here.",
                },
            },
        },
        secret_keys=("access_token",),
    )

    def make_reader(self, config: Mapping[str, Any], secrets: Mapping[str, str]) -> Any:
        cls = import_reader(
            "llama_index.readers.stackoverflow",
            "StackoverflowReader",
            "importers-stackoverflow",
        )
        return cls(
            team_name=str(config["team_name"]),
            access_token=secrets["access_token"],
            cache_dir=(
                str(config["cache_dir"]) if config.get("cache_dir") else None
            ),
        )
