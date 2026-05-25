"""Librarian — the curator agent.

The Librarian sees every incoming Proposal and decides what to do
with it: accept (new episode in the graph), reject (with a reason
returned to the submitter), or escalate (human reviewer queue).
A separate cron-driven Drift Detector pass — not implemented here
yet — handles the inverse direction: flagging stale facts that the
sources no longer support.

Implementation notes:

- We use Anthropic directly for the curator prompt — it's independent
  of the retrieval/ingest path.
- Prompt caching is enabled on the system prompt: the rubric the
  Librarian uses is large and rarely changes, so subsequent proposals
  in the same 5-min window get a cache hit on the entire framing.
  This matches the pattern shipped in Ship's AnthropicAgentClient.
- The proposal payload itself is *not* cached — every proposal is
  unique so a cache marker there would create garbage entries.
"""

from __future__ import annotations

import logging
from typing import Any, Literal

from lighthouse.core.config import Settings, get_settings

logger = logging.getLogger(__name__)


_SYSTEM_PROMPT = """\
You are the Librarian for a Lighthouse instance — a knowledge base for AI agents.

You receive proposals (new facts, corrections, deprecations) and decide
how each should be handled. Your goals, in priority order:

1. **Correctness.** A wrong fact in the graph is worse than no fact.
   Reject anything you cannot verify against the supplied evidence.
2. **Generalizability.** This base serves many consumers. Reject anything
   that is obviously project-specific (e.g. "our API key lives at...").
3. **No duplicates.** If the proposed fact is already represented, reject
   with a pointer to the existing node.
4. **Critical mass for practice-derived claims.** A single project saying
   "X is true" is weaker evidence than three independent projects
   converging on it. Without evidence, escalate.

Output one of three decisions, with a one-sentence reason:
- ``accept`` — write to graph as a new episode
- ``reject`` — return reason to submitter
- ``escalate`` — flag for human review

Be terse. The decision and reason are logged verbatim; the submitter
sees them.
"""


# Anthropic prompt-cache marker. Re-use the same pattern that paid off
# in Ship: one breakpoint on the system block covers the (large, stable)
# rubric above and keeps every subsequent proposal's input cost minimal.
_CACHE_EPHEMERAL: dict[str, Any] = {"type": "ephemeral"}


Decision = Literal["accept", "reject", "escalate"]


class Librarian:
    """Anthropic-backed curator. Single async method; no state."""

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()
        self._client: Any | None = None

    async def _client_lazy(self) -> Any:
        if self._client is not None:
            return self._client
        from anthropic import AsyncAnthropic

        if not self._settings.anthropic_api_key:
            raise RuntimeError(
                "ANTHROPIC_API_KEY not set — librarian cannot evaluate proposals"
            )
        self._client = AsyncAnthropic(api_key=self._settings.anthropic_api_key)
        return self._client

    async def evaluate_proposal(
        self,
        *,
        proposal_type: str,
        content: str,
        evidence: list[str],
        rationale: str,
        target_node_id: str | None = None,
    ) -> tuple[Decision, str]:
        """Decide what to do with one proposal.

        Returns ``(decision, reason)``. The reason is whatever the model
        wrote — surfaced back to the submitter verbatim, so the prompt
        instructs the model to keep it to one sentence.
        """
        client = await self._client_lazy()

        # User message frames the proposal in a single block. We don't
        # mark it for caching — proposals are unique inputs, caching
        # them would just churn entries.
        evidence_lines = (
            [f"  - {e}" for e in evidence] if evidence else ["  (none provided)"]
        )
        user_lines = [
            f"Proposal type: {proposal_type}",
            f"Target node: {target_node_id or '(none — new fact)'}",
            f"Content: {content}",
            "Evidence:",
            *evidence_lines,
            f"Rationale: {rationale or '(none provided)'}",
            "",
            "Reply on a single line as: DECISION | one-sentence reason",
            "Where DECISION ∈ {accept, reject, escalate}.",
        ]

        message = await client.messages.create(
            model=self._settings.lighthouse_model_fast,
            max_tokens=256,
            temperature=0.0,
            system=[
                {
                    "type": "text",
                    "text": _SYSTEM_PROMPT,
                    "cache_control": _CACHE_EPHEMERAL,
                }
            ],
            messages=[{"role": "user", "content": "\n".join(user_lines)}],
        )

        raw = "".join(
            block.text for block in message.content if getattr(block, "type", "") == "text"
        ).strip()

        return _parse_decision(raw)


def _parse_decision(raw: str) -> tuple[Decision, str]:
    """Pull ``decision | reason`` out of the model's reply.

    Defensive: if the model strays from the format, fall back to
    ``escalate`` with the raw text as reason. Better to put it in the
    human queue than to silently misclassify.
    """
    if "|" not in raw:
        return "escalate", f"unparseable librarian reply: {raw[:200]}"
    head, _, reason = raw.partition("|")
    decision = head.strip().lower()
    if decision not in {"accept", "reject", "escalate"}:
        return "escalate", f"unknown decision '{decision}': {reason.strip()[:200]}"
    return decision, reason.strip()  # type: ignore[return-value]
