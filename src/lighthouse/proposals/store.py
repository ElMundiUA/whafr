"""Git-backed proposal store.

Each proposal is one file: ``<id>.md`` with a YAML frontmatter block
for state (status, decision, reason, timestamps, evidence list) and a
markdown body for the natural-language content + rationale.

Why git? Three things come free:

1. **Audit trail.** Every state change is a commit — who changed what,
   when, and by which subsystem (we use distinct commit messages for
   submit vs decision vs escalation).
2. **History.** A rejected proposal that later becomes valid can be
   re-evaluated against its original wording, not a mutated version.
3. **Portability.** The store is just a directory of markdown files.
   Tooling (grep, fzf, IDE) Just Works; an operator can read a
   proposal without installing anything.

Git ops are best-effort: if ``git`` isn't on PATH or the working tree
isn't a repo, we still write the file and log a warning. The
in-store state remains correct; only the audit trail degrades.
"""

from __future__ import annotations

import asyncio
import logging
import re
import shutil
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

import yaml

logger = logging.getLogger(__name__)


ProposalStatus = Literal[
    "queued",       # written, not yet evaluated
    "evaluating",   # worker has picked it up
    "accepted",     # librarian accepted; episode in graph
    "rejected",     # librarian rejected; reason returned to submitter
    "escalated",    # librarian deferred; awaiting human reviewer
    "errored",      # worker crashed mid-evaluation
]


@dataclass(slots=True)
class ProposalRecord:
    """In-memory shape of one stored proposal.

    Mirrors the file's frontmatter + body. Mutated in place by the
    worker as it walks status transitions; :meth:`GitProposalStore.update`
    persists the change atomically.
    """

    id: str
    status: ProposalStatus
    type: Literal["add", "correct", "deprecate"]
    content: str
    submitted_at: datetime
    submitted_by: str = "anonymous"
    target_node_id: str | None = None
    evidence: list[str] = field(default_factory=list)
    rationale: str = ""
    decision_at: datetime | None = None
    reason: str | None = None
    episode_uuid: str | None = None


class GitProposalStore:
    """Filesystem + git store for proposals.

    Thread-safety: writes are serialised under ``_lock`` so two
    concurrent submits don't race on the same file or step on each
    other's git commits. Reads are lock-free.
    """

    def __init__(self, root: Path | str) -> None:
        self._root = Path(root)
        self._root.mkdir(parents=True, exist_ok=True)
        self._lock = asyncio.Lock()
        self._git_available = shutil.which("git") is not None
        if self._git_available:
            self._ensure_git_repo()
        else:
            logger.warning(
                "git not found on PATH — proposal audit trail disabled"
            )

    # --- public API ----------------------------------------------------

    async def create(self, record: ProposalRecord) -> None:
        """Write a new proposal file and commit it.

        Caller is responsible for generating ``record.id`` (we don't
        return it — the route already knows the id when it gets here).
        """
        async with self._lock:
            self._write(record)
            self._commit(record.id, action="submit", subject=record.content[:80])

    async def read(self, proposal_id: str) -> ProposalRecord | None:
        """Read one proposal by id. Returns ``None`` if absent.

        Filesystem read, no lock — callers tolerate eventual consistency
        with concurrent writers (e.g., a status poll racing the worker
        will see whichever state was last fsync'd).
        """
        path = self._path(proposal_id)
        if not path.exists():
            return None
        return self._parse(path)

    async def update(
        self,
        record: ProposalRecord,
        *,
        action: str = "update",
    ) -> None:
        """Overwrite the file with a new state and commit.

        ``action`` is the git subject prefix so the audit log reads
        like ``decision: rejected (Q1234)`` rather than a flat
        ``update``.
        """
        async with self._lock:
            self._write(record)
            self._commit(record.id, action=action, subject=record.status)

    # --- internals -----------------------------------------------------

    def _path(self, proposal_id: str) -> Path:
        return self._root / f"{proposal_id}.md"

    def _write(self, record: ProposalRecord) -> None:
        frontmatter = {
            "id": record.id,
            "status": record.status,
            "type": record.type,
            "submitted_at": record.submitted_at.isoformat(),
            "submitted_by": record.submitted_by,
            "target_node_id": record.target_node_id,
            "evidence": list(record.evidence),
            "decision_at": (
                record.decision_at.isoformat() if record.decision_at else None
            ),
            "reason": record.reason,
            "episode_uuid": record.episode_uuid,
        }
        body = (
            "---\n"
            + yaml.safe_dump(frontmatter, sort_keys=False, allow_unicode=True)
            + "---\n\n"
            + "## Proposal\n\n"
            + record.content.rstrip()
            + "\n"
        )
        if record.rationale:
            body += "\n## Rationale\n\n" + record.rationale.rstrip() + "\n"
        self._path(record.id).write_text(body, encoding="utf-8")

    def _parse(self, path: Path) -> ProposalRecord:
        text = path.read_text(encoding="utf-8")
        match = re.match(r"^---\n(.*?)\n---\n(.*)$", text, re.DOTALL)
        if not match:
            raise ValueError(f"proposal {path} missing frontmatter")
        front = yaml.safe_load(match.group(1)) or {}
        body = match.group(2)

        # Body has "## Proposal\n\n<content>\n\n## Rationale\n\n<rationale>".
        # Parse sections by header — tolerate missing rationale.
        content, rationale = _split_body(body)

        return ProposalRecord(
            id=str(front["id"]),
            status=front["status"],
            type=front["type"],
            content=content,
            submitted_at=_parse_iso(front["submitted_at"]),
            submitted_by=front.get("submitted_by") or "anonymous",
            target_node_id=front.get("target_node_id"),
            evidence=list(front.get("evidence") or []),
            rationale=rationale,
            decision_at=_parse_iso_opt(front.get("decision_at")),
            reason=front.get("reason"),
            episode_uuid=front.get("episode_uuid"),
        )

    # --- git -----------------------------------------------------------

    def _ensure_git_repo(self) -> None:
        """Initialise the proposal store as a git repo if it isn't one.

        Idempotent: running ``git init`` on an existing repo is a no-op
        for our purposes (we don't touch existing refs).
        """
        try:
            if not (self._root / ".git").exists():
                self._run_git("init", "-b", "main")
                self._run_git("config", "user.email", "librarian@lighthouse")
                self._run_git("config", "user.name", "Lighthouse Librarian")
        except Exception:
            logger.exception("failed to initialise proposal git repo")
            self._git_available = False

    def _commit(self, proposal_id: str, *, action: str, subject: str) -> None:
        if not self._git_available:
            return
        try:
            self._run_git("add", f"{proposal_id}.md")
            self._run_git(
                "commit",
                "-m",
                f"{action}: {proposal_id[:8]} — {subject[:80]}",
                "--allow-empty",
            )
        except Exception:
            logger.exception("git commit failed for proposal %s", proposal_id)

    def _run_git(self, *args: str) -> None:
        import subprocess  # local import — only path that needs subprocess

        subprocess.run(
            ["git", *args],
            cwd=self._root,
            check=True,
            capture_output=True,
        )


def _split_body(body: str) -> tuple[str, str]:
    """Pull ``## Proposal`` and ``## Rationale`` sections out of ``body``.

    Defensive: if the markers are missing, the whole body falls into
    ``content``. This means a hand-edited proposal file with non-
    canonical structure still round-trips through the store, just
    without a structured rationale field.
    """
    content_marker = "## Proposal\n\n"
    rationale_marker = "\n## Rationale\n\n"
    start = body.find(content_marker)
    if start < 0:
        return body.strip(), ""
    after = body[start + len(content_marker):]
    rationale_at = after.find(rationale_marker)
    if rationale_at < 0:
        return after.strip(), ""
    content = after[:rationale_at].strip()
    rationale = after[rationale_at + len(rationale_marker):].strip()
    return content, rationale


def _parse_iso(s: str) -> datetime:
    return datetime.fromisoformat(s)


def _parse_iso_opt(s: str | None) -> datetime | None:
    return datetime.fromisoformat(s) if s else None


def utc_now() -> datetime:
    """Single helper so we don't sprinkle ``datetime.now(UTC)`` everywhere
    — and so tests can monkey-patch one call site."""
    return datetime.now(UTC)


def new_proposal_id() -> str:
    """Caller-side id minting — kept here so tests can predict ids by
    monkey-patching this helper rather than the route."""
    return str(uuid.uuid4())
