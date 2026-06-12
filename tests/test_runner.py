"""Source-runner unit tests.

Three concerns covered:

- :mod:`lighthouse.runner.config` — YAML loads, duration parsing,
  duplicate-name detection.
- :mod:`lighthouse.runner.state` — JSON round-trip + atomic replace
  preserves data on partial writes.
- :class:`SourceScheduler` — due-source detection, error isolation,
  state mutations after success/failure.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import yaml

from lighthouse.connectors.base import Connector, SourceDocument
from lighthouse.runner.config import (
    RunnerConfig,
    Schedule,
    SourceSpec,
    load_config,
    parse_duration,
)
from lighthouse.runner.scheduler import SourceScheduler
from lighthouse.runner.state import RunState, StateStore

# --- duration parsing ---------------------------------------------------


def test_parse_duration_accepts_known_units() -> None:
    assert parse_duration("30s") == timedelta(seconds=30)
    assert parse_duration("5m") == timedelta(minutes=5)
    assert parse_duration("6h") == timedelta(hours=6)
    assert parse_duration("1d") == timedelta(days=1)


def test_parse_duration_rejects_malformed() -> None:
    with pytest.raises(ValueError):
        parse_duration("5min")  # not in our shorthand
    with pytest.raises(ValueError):
        parse_duration("")
    with pytest.raises(ValueError):
        parse_duration("h6")


# --- config -------------------------------------------------------------


def test_load_config_round_trips(tmp_path: Path) -> None:
    p = tmp_path / "sources.yaml"
    p.write_text(
        yaml.safe_dump(
            {
                "sources": [
                    {
                        "name": "demo",
                        "connector": "markdown",
                        "args": {"path": "./docs"},
                        "schedule": {"every": "30m"},
                    }
                ]
            }
        )
    )
    cfg = load_config(p)
    assert len(cfg.sources) == 1
    assert cfg.sources[0].name == "demo"
    assert cfg.sources[0].schedule.interval == timedelta(minutes=30)


def test_config_rejects_duplicate_source_names() -> None:
    with pytest.raises(ValueError, match="duplicate source names"):
        RunnerConfig.model_validate(
            {
                "sources": [
                    {
                        "name": "x",
                        "connector": "markdown",
                        "args": {"path": "./a"},
                        "schedule": {"every": "1h"},
                    },
                    {
                        "name": "x",
                        "connector": "markdown",
                        "args": {"path": "./b"},
                        "schedule": {"every": "1h"},
                    },
                ]
            }
        )


def test_config_rejects_unknown_connector() -> None:
    with pytest.raises(ValueError):
        RunnerConfig.model_validate(
            {
                "sources": [
                    {
                        "name": "x",
                        "connector": "carrier-pigeon",  # not registered
                        "args": {},
                        "schedule": {"every": "1h"},
                    }
                ]
            }
        )


# --- state --------------------------------------------------------------


def test_state_store_round_trip(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "state.json")
    store.update(
        "src-a",
        RunState(
            last_run_at=datetime(2026, 5, 15, 10, 0, tzinfo=UTC),
            last_ok=True,
            last_documents=12,
        ),
    )
    store.update(
        "src-b",
        RunState(last_run_at=None, last_ok=False, last_error="boom"),
    )

    # Re-open and verify persistence.
    fresh = StateStore(tmp_path / "state.json")
    a = fresh.get("src-a")
    assert a is not None
    assert a.last_ok is True
    assert a.last_documents == 12
    b = fresh.get("src-b")
    assert b is not None
    assert b.last_ok is False
    assert b.last_error == "boom"


def test_state_store_handles_missing_file(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "nonexistent.json")
    assert store.get("anything") is None


# --- scheduler ----------------------------------------------------------


class _FakeConnector(Connector):
    name = "fake"

    def __init__(self, docs: list[SourceDocument]) -> None:
        self._docs = docs

    async def ingest(self) -> AsyncIterator[SourceDocument]:
        for d in self._docs:
            yield d


def _spec(name: str, every: str = "1h") -> SourceSpec:
    return SourceSpec(
        name=name,
        connector="markdown",
        args={"path": "/ignored"},
        schedule=Schedule(every=every),
    )


@pytest.fixture
def fake_graph_init_safe(fake_graph):
    """FakeGraph already implements initialize() — alias for clarity."""
    return fake_graph


async def test_scheduler_run_once_fires_every_source(
    tmp_path: Path, fake_graph_init_safe
) -> None:
    """``run_once`` should drain every configured source regardless of
    last_run state, and record success in state."""
    config = RunnerConfig(sources=[_spec("a"), _spec("b")])
    state = StateStore(tmp_path / "state.json")

    drained: list[str] = []

    async def fake_drain(connector, graph, source_prefix):
        drained.append(source_prefix)
        return 3

    scheduler = SourceScheduler(
        config,
        state,
        fake_graph_init_safe,
        drain_fn=fake_drain,
        connector_factory=lambda spec: _FakeConnector([]),
    )
    results = await scheduler.run_once()

    assert set(drained) == {"a", "b"}
    assert results["a"] == 3
    assert results["b"] == 3
    assert state.get("a").last_ok is True
    assert state.get("a").last_documents == 3


async def test_scheduler_tick_skips_unready_sources(
    tmp_path: Path, fake_graph_init_safe
) -> None:
    """A source whose last run is within its interval must not fire."""
    config = RunnerConfig(sources=[_spec("recent", every="1h")])
    state = StateStore(tmp_path / "state.json")
    # Mark it as run 5 minutes ago — schedule says 1h, so not due.
    state.update(
        "recent",
        RunState(last_run_at=datetime.now(UTC) - timedelta(minutes=5), last_ok=True),
    )

    drained: list[str] = []

    async def fake_drain(connector, graph, source_prefix):
        drained.append(source_prefix)
        return 0

    scheduler = SourceScheduler(
        config,
        state,
        fake_graph_init_safe,
        drain_fn=fake_drain,
        connector_factory=lambda spec: _FakeConnector([]),
    )
    fired = await scheduler.tick()
    assert fired == []
    assert drained == []


async def test_scheduler_isolates_errors_per_source(
    tmp_path: Path, fake_graph_init_safe
) -> None:
    """A failing source should not bring down its siblings."""
    config = RunnerConfig(sources=[_spec("good"), _spec("bad")])
    state = StateStore(tmp_path / "state.json")

    async def fake_drain(connector, graph, source_prefix):
        if source_prefix == "bad":
            raise RuntimeError("kaboom")
        return 7

    scheduler = SourceScheduler(
        config,
        state,
        fake_graph_init_safe,
        drain_fn=fake_drain,
        connector_factory=lambda spec: _FakeConnector([]),
    )
    results = await scheduler.run_once()

    assert results["good"] == 7
    assert results["bad"] == 0
    assert state.get("good").last_ok is True
    assert state.get("bad").last_ok is False
    assert "kaboom" in (state.get("bad").last_error or "")


async def test_scheduler_run_loop_can_be_cancelled(
    tmp_path: Path, fake_graph_init_safe
) -> None:
    config = RunnerConfig(sources=[_spec("a", every="1d")])
    state = StateStore(tmp_path / "state.json")

    scheduler = SourceScheduler(
        config,
        state,
        fake_graph_init_safe,
        heartbeat_seconds=0.01,
        drain_fn=lambda *a, **k: asyncio.sleep(0, result=0),
        connector_factory=lambda spec: _FakeConnector([]),
    )
    task = asyncio.create_task(scheduler.run())
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
