"""Source-runner: scheduled ingestion of registered sources.

A long-running async loop reads ``sources.yaml``, fires each connector
on its schedule, and persists last-run state to JSON so restarts pick
up where they left off. Single-process; no Temporal/Prefect — sources
are independent and the worst case from a missed beat is the next
window catches up.
"""

from lighthouse.runner.config import RunnerConfig, SourceSpec, load_config
from lighthouse.runner.scheduler import SourceScheduler
from lighthouse.runner.state import RunState, StateStore

__all__ = [
    "RunnerConfig",
    "RunState",
    "SourceScheduler",
    "SourceSpec",
    "StateStore",
    "load_config",
]
