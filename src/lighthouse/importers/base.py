"""LighthouseImporter ABC.

An importer is a *type* (registered class) + an *instance config*.
The type declares:

- a stable string key (used in the `importers.type` DB column),
- a display label and one-line description,
- a JSON Schema describing the config fields the admin UI renders,
- which of those fields are secrets (encrypted at rest),
- a factory `build_connector(config)` that returns a Connector ready
  for `ingest.drain()`.

The runner persists user-supplied configs in the `importers` table,
splits secrets into the encrypted blob, and looks the type up via
the registry on every run.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, ClassVar

from lighthouse.connectors.base import Connector


@dataclass(slots=True, frozen=True)
class ImporterMeta:
    """Identity card for an importer type — what the admin UI lists."""

    type: str
    display_name: str
    description: str
    # JSON Schema (draft 2020-12). The admin UI renders a form from
    # this. Only the field shapes the UI cares about are required:
    # `type`, `properties`, `required`, optional `title`/`description`
    # per-field.
    config_schema: dict[str, Any]
    # Names of keys inside `config_schema.properties` that hold
    # secrets and must be encrypted before persisting.
    secret_keys: tuple[str, ...] = field(default_factory=tuple)
    # Names of config_schema properties the UI must collect BEFORE
    # calling `discover()` (the importer needs them to authenticate
    # and probe the source). Implies the importer supports discovery
    # when non-empty AND the subclass overrides discover().
    discovery_required: tuple[str, ...] = field(default_factory=tuple)


@dataclass(slots=True)
class DiscoveredItem:
    """One thing the user can pick to import from a source.

    `config_patch` is merged into the saved importer's config — keeps
    type-specific selection logic on the backend, frontend stays generic.
    """

    id: str
    name: str
    kind: str
    hint: str | None
    config_patch: dict[str, Any]


@dataclass(slots=True)
class ImporterRun:
    """Per-run counters the runner updates as drain progresses."""

    items_total: int | None = None
    items_done: int = 0
    chunks_added: int = 0


class LighthouseImporter(ABC):
    """Abstract base every importer adapter implements.

    Concrete subclasses set `meta` at class level and implement
    `build_connector`. The runner instantiates the subclass once per
    run, calls `build_connector(config, secrets)`, and feeds the
    result into `ingest.drain()`.

    Instances are stateless beyond their config — no per-importer
    long-lived resources. If an adapter needs an HTTP session it
    opens one inside the Connector it returns and closes it on
    `ingest()` exit.
    """

    meta: ImporterMeta
    # Subclasses set True when they implement `discover()`. The /types
    # API surfaces this so the wizard knows to offer a picker step.
    supports_discovery: ClassVar[bool] = False

    @abstractmethod
    def build_connector(
        self,
        config: Mapping[str, Any],
        secrets: Mapping[str, str],
    ) -> Connector:
        """Return a Connector ready for `ingest.drain()`.

        `config` is the non-secret portion straight from `importers.config`.
        `secrets` has already been decrypted from `importers.secrets_enc`;
        only the keys this importer declared in `meta.secret_keys` are
        present, missing keys are absent (not empty strings) so the
        adapter can decide whether to fall back to env vars.
        """
        raise NotImplementedError

    def discover(
        self,
        config: Mapping[str, Any],
        secrets: Mapping[str, str],
    ) -> list[DiscoveredItem]:
        """Probe the source and return what's available to import.

        Default raises `NotImplementedError` — the adapter doesn't
        support picker-style discovery, the wizard falls back to the
        flat-form path. Subclasses that override MUST also set
        `supports_discovery = True` at the class level so the /types
        response advertises it.

        The probe call is bounded: keep it under a few seconds, page
        sensibly, cap at ~200 items. The UI has a search filter so
        operators don't need every channel/page back at once.
        """
        raise NotImplementedError(
            f"{type(self).__name__} does not implement discover()"
        )
