"""Persistent last-run state for the source runner.

One JSON file at ``LIGHTHOUSE_RUNNER_STATE`` keyed by source name with
the timestamp of the last successful (or failed) run, the error if any,
and the document count. Loaded once on scheduler start and rewritten
after each run.

Why JSON, not SQLite or another database? Because the state is tiny
(one row per configured source — dozens at most) and the operator
benefits from being able to ``cat`` it. If we ever outgrow this, the
swap path is a single class — callers all go through :class:`StateStore`.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class RunState:
    """One source's last-run record."""

    __slots__ = ("last_run_at", "last_ok", "last_error", "last_documents")

    def __init__(
        self,
        *,
        last_run_at: datetime | None = None,
        last_ok: bool = False,
        last_error: str | None = None,
        last_documents: int = 0,
    ) -> None:
        self.last_run_at = last_run_at
        self.last_ok = last_ok
        self.last_error = last_error
        self.last_documents = last_documents

    def to_dict(self) -> dict[str, Any]:
        return {
            "last_run_at": self.last_run_at.isoformat() if self.last_run_at else None,
            "last_ok": self.last_ok,
            "last_error": self.last_error,
            "last_documents": self.last_documents,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> RunState:
        return cls(
            last_run_at=(
                datetime.fromisoformat(d["last_run_at"])
                if d.get("last_run_at")
                else None
            ),
            last_ok=bool(d.get("last_ok", False)),
            last_error=d.get("last_error"),
            last_documents=int(d.get("last_documents", 0) or 0),
        )


class StateStore:
    """JSON-backed run-state persistence.

    Not async — the state file is small and writes are infrequent
    (one per source per schedule tick). A blocking write inside the
    async runner is cheaper than the lock dance an async file lib
    would introduce.
    """

    def __init__(self, path: Path | str) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._cache: dict[str, RunState] = {}
        self._loaded = False

    def load(self) -> dict[str, RunState]:
        if self._loaded:
            return self._cache
        if self._path.exists():
            try:
                raw = json.loads(self._path.read_text(encoding="utf-8")) or {}
                sources = raw.get("sources") or {}
                self._cache = {
                    name: RunState.from_dict(d) for name, d in sources.items()
                }
            except (OSError, json.JSONDecodeError):
                logger.exception(
                    "could not read runner state at %s — starting fresh", self._path
                )
                self._cache = {}
        self._loaded = True
        return self._cache

    def get(self, source_name: str) -> RunState | None:
        return self.load().get(source_name)

    def update(self, source_name: str, state: RunState) -> None:
        cache = self.load()
        cache[source_name] = state
        self._flush()

    def _flush(self) -> None:
        payload = {"sources": {name: s.to_dict() for name, s in self._cache.items()}}
        # Write to a sibling temp file and atomic-rename so a crash
        # mid-write doesn't leave a corrupt JSON file.
        tmp = self._path.with_suffix(self._path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        tmp.replace(self._path)


def utc_now() -> datetime:
    return datetime.now(UTC)
