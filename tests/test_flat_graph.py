"""Unit tests for FlatGraph helpers that need no DB or network."""

from __future__ import annotations

from lighthouse.core.flat_graph import _deterministic_uuid


def test_deterministic_uuid_stable_within_workspace() -> None:
    # Re-ingest idempotency: same inputs → same uuid → ON CONFLICT no-op.
    a = _deterministic_uuid("https://x/doc", "abc123", 0, "ws-a")
    b = _deterministic_uuid("https://x/doc", "abc123", 0, "ws-a")
    assert a == b


def test_deterministic_uuid_differs_across_workspaces() -> None:
    # Same document, two tenants. The uuid MUST differ so the second
    # tenant's write doesn't ON CONFLICT-overwrite the first tenant's row
    # — that was a cross-tenant leak + recipe bleed before K2.
    a = _deterministic_uuid("https://x/doc", "abc123", 0, "ws-a")
    b = _deterministic_uuid("https://x/doc", "abc123", 0, "ws-b")
    assert a != b


def test_deterministic_uuid_differs_across_chunk_index() -> None:
    a = _deterministic_uuid("https://x/doc", "abc123", 0, "ws-a")
    b = _deterministic_uuid("https://x/doc", "abc123", 1, "ws-a")
    assert a != b
