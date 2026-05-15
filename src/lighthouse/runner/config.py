"""Source-runner configuration.

Sources live in a single YAML file (path from settings). Each entry
names a connector by string id, supplies its kwargs, and declares a
schedule. Connectors are instantiated lazily via a small registry so
the runner can validate config before importing heavy LlamaIndex deps.

The duration format is the dead-simple ``<int><unit>`` string:
``30s``, ``5m``, ``6h``, ``1d``. No cron expressions for v1 — the
fancy patterns aren't worth a dependency on croniter.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from datetime import timedelta
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field, field_validator

from lighthouse.connectors.base import Connector


# --- duration parsing ---------------------------------------------------


_DURATION_RE = re.compile(r"^\s*(\d+)\s*(s|m|h|d)\s*$")
_UNIT_SECONDS = {"s": 1, "m": 60, "h": 3600, "d": 86400}


def parse_duration(text: str) -> timedelta:
    """``5m`` → ``timedelta(minutes=5)``. Raises ``ValueError`` on
    malformed input — we don't want a typo'd ``5min`` silently meaning
    something different from what the operator expected."""
    m = _DURATION_RE.match(text)
    if not m:
        raise ValueError(
            f"invalid duration {text!r} — use Ns / Nm / Nh / Nd (e.g. '6h')"
        )
    n = int(m.group(1))
    unit = m.group(2)
    return timedelta(seconds=n * _UNIT_SECONDS[unit])


# --- config models ------------------------------------------------------


class Schedule(BaseModel):
    every: str = Field(description="Duration string: Ns / Nm / Nh / Nd")

    @field_validator("every")
    @classmethod
    def _validate(cls, v: str) -> str:
        parse_duration(v)  # raises if malformed
        return v

    @property
    def interval(self) -> timedelta:
        return parse_duration(self.every)


class SourceSpec(BaseModel):
    """One configured source.

    ``connector`` is a string id resolved through :func:`build_connector`
    so we can validate config without importing connector modules
    (LlamaIndex transitive deps are heavy).
    """

    name: str = Field(min_length=1, description="Stable id; used as source_prefix")
    connector: Literal["markdown", "web", "github"]
    args: dict[str, Any] = Field(default_factory=dict)
    schedule: Schedule


class RunnerConfig(BaseModel):
    sources: list[SourceSpec] = Field(default_factory=list)

    @field_validator("sources")
    @classmethod
    def _unique_names(cls, v: list[SourceSpec]) -> list[SourceSpec]:
        names = [s.name for s in v]
        dups = {n for n in names if names.count(n) > 1}
        if dups:
            raise ValueError(f"duplicate source names: {sorted(dups)}")
        return v


def load_config(path: Path | str) -> RunnerConfig:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"runner config not found: {p}")
    raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    return RunnerConfig.model_validate(raw)


# --- connector registry -------------------------------------------------


ConnectorFactory = Callable[[dict[str, Any]], Connector]


def _markdown_factory(args: dict[str, Any]) -> Connector:
    from lighthouse.connectors.markdown import MarkdownConnector

    return MarkdownConnector(args["path"])


def _web_factory(args: dict[str, Any]) -> Connector:
    from lighthouse.connectors.web import WebConnector

    return WebConnector(args["urls"])


def _github_factory(args: dict[str, Any]) -> Connector:
    from lighthouse.connectors.github import GitHubConnector

    slug = args.get("slug")
    if slug and "/" in slug:
        owner, repo = slug.split("/", 1)
    else:
        owner = args["owner"]
        repo = args["repo"]
    return GitHubConnector(
        owner=owner,
        repo=repo,
        branch=args.get("branch", "main"),
        file_extensions=args.get("file_extensions"),
    )


_FACTORIES: dict[str, ConnectorFactory] = {
    "markdown": _markdown_factory,
    "web": _web_factory,
    "github": _github_factory,
}


def build_connector(spec: SourceSpec) -> Connector:
    """Instantiate the connector for a source spec.

    Kept separate from ``SourceSpec`` so config validation doesn't drag
    LlamaIndex into module import time — we only pay for the heavy
    imports when the scheduler actually fires a source.
    """
    factory = _FACTORIES.get(spec.connector)
    if factory is None:
        raise ValueError(
            f"unknown connector {spec.connector!r} — registered: "
            f"{sorted(_FACTORIES)}"
        )
    return factory(spec.args)
