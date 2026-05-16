"""Agent benchmark v2 — full telemetry + agentic loop + parallel runs.

Compares Lighthouse-MCP on/off across an expanded task set that mixes
single-step QA with project-scale tasks (WBS, PRD, tech design,
sprint planning, end-to-end debug). Each task runs through a 4-stage
agentic loop (plan → execute → self-review → finalize) so the
comparison reflects real agent behaviour rather than one-shot QA.

Captured per-task:
- Per-stage Claude calls, each with wall-time and usage
- Tool queries + retrieved-fact summaries (for B)
- Aggregate tokens / cost / wall-time
- Verdict from a Sonnet judge (4-axis + winner + rationale)

Parallelism: ``CONCURRENCY`` tasks run at once via ``asyncio.gather``,
each task's pair (A then B) is sequential within itself so judge sees
both finished outputs. Tune ``CONCURRENCY`` against Anthropic's
per-org RPM/TPM limits.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import sys
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field, asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


# ============================================================
# Tunables
# ============================================================
CONCURRENCY = 5  # parallel tasks; each task internally serial A→B→judge
MAX_AGENT_STEPS = 6  # cap on execute-stage Claude calls per agentic task
MAX_TOOL_LOOP = 10  # cap on tool-use iterations inside one execute call


# Pricing per 1M tokens (Sonnet 4.6, May 2026 official rates)
PRICING = {
    "input": 3.0 / 1_000_000,
    "output": 15.0 / 1_000_000,
    "cache_read": 0.30 / 1_000_000,
    "cache_write": 3.75 / 1_000_000,
}


logger = logging.getLogger(__name__)


# ============================================================
# Task catalogue — 20 original + 15 project-scale
# ============================================================
TASKS: list[dict[str, Any]] = [
    # ---------- original single-step (20) ----------
    {"role": "clarification", "type": "qa", "complexity": "simple",
     "title": "Clarify ambiguous AC",
     "description": "A ticket says: 'User should be able to manage their profile'. Ask 3 clarifying questions a BA would ask before this can be sized."},
    {"role": "clarification", "type": "qa", "complexity": "simple",
     "title": "Write acceptance criteria",
     "description": "Feature: 'User can upload a profile photo'. Write Given/When/Then acceptance criteria in Gherkin format covering happy path, file-size cap, and unsupported MIME type."},
    {"role": "decomposition", "type": "qa", "complexity": "simple",
     "title": "Split a large story",
     "description": "Story: 'As an admin I can manage team membership'. Split into 3-5 INVEST-compliant child stories with names + 1-line scope each."},
    {"role": "decomposition", "type": "qa", "complexity": "simple",
     "title": "Apply MECE to feature breakdown",
     "description": "Feature: 'Customer-facing analytics dashboard'. Decompose into MECE sub-features. Name each + boundary."},
    {"role": "designer", "type": "qa", "complexity": "simple",
     "title": "Spec dark-mode color tokens",
     "description": "We use Tailwind with shadcn/ui components. Spec a token-level dark-mode palette for a dashboard (background, foreground, primary, destructive). Cite token-naming convention."},
    {"role": "designer", "type": "qa", "complexity": "simple",
     "title": "Accessibility for icon-only button",
     "description": "A toolbar uses icon-only buttons (Edit, Delete, Share). Spec WCAG-compliant ARIA attributes and label behaviour."},
    {"role": "developer", "type": "qa", "complexity": "medium",
     "title": "FastAPI dependency injection",
     "description": "Show idiomatic FastAPI dependency-injection for a DB session (SQLAlchemy async) with lifespan setup + Depends() usage."},
    {"role": "developer", "type": "qa", "complexity": "medium",
     "title": "Anthropic prompt caching",
     "description": "Wire prompt caching on an Anthropic Python SDK call with a large system prompt + tool definitions. Where do cache_control markers go and what's the TTL?"},
    {"role": "devops", "type": "qa", "complexity": "medium",
     "title": "Kubernetes readiness probe",
     "description": "Add a readiness probe to a Deployment for a FastAPI service that exposes /health. Spec httpGet, initialDelaySeconds, periodSeconds, failureThreshold."},
    {"role": "devops", "type": "qa", "complexity": "medium",
     "title": "GitHub Actions secret rotation",
     "description": "Workflow needs OIDC trust to AWS so we don't store long-lived secrets. Outline configure-aws-credentials with id-token: write permission + role assume."},
    {"role": "planning", "type": "qa", "complexity": "simple",
     "title": "Sprint planning for cap-constrained team",
     "description": "5-dev team with average velocity 35 sp/sprint. PO wants to fit a 50sp epic + ongoing bug-fix work. How do you advise the sprint plan?"},
    {"role": "planning", "type": "qa", "complexity": "simple",
     "title": "Story-point estimation guidance",
     "description": "New team is debating SP vs hours. Lay out the 3 strongest arguments for story points + the 2 strongest arguments for NoEstimates."},
    {"role": "product-manager", "type": "qa", "complexity": "simple",
     "title": "RICE prioritization on 4 features",
     "description": "Score these features in RICE: (1) Dark mode, (2) SSO, (3) Mobile push notifications, (4) Bulk-import CSV. Use reasonable estimates."},
    {"role": "product-manager", "type": "prd", "complexity": "medium",
     "title": "Write a 1-page PRD",
     "description": "Draft a 1-page PRD for 'Magic-link email login' targeting B2B SaaS users. Cover: problem, audience, solution, success metrics, risks."},
    {"role": "reviewer", "type": "qa", "complexity": "simple",
     "title": "Review a Python async function",
     "description": "Spot 5 issues in this code and write conventional-comment-style review remarks:\n\n```python\nasync def get_user(uid):\n  try:\n    r = requests.get(f'/users/{uid}')\n    return r.json()\n  except:\n    return None\n```"},
    {"role": "reviewer", "type": "qa", "complexity": "medium",
     "title": "Auth endpoint security review",
     "description": "OWASP-style review of a POST /login endpoint that accepts email+password. List the 5 most important checks the reviewer must run before approving."},
    {"role": "self-heal", "type": "qa", "complexity": "simple",
     "title": "Blameless postmortem template",
     "description": "30-minute outage caused by an OOM-killed pod after a deploy. Draft the postmortem with timeline, contributing factors, action items — blameless tone."},
    {"role": "self-heal", "type": "qa", "complexity": "simple",
     "title": "Flaky-test triage",
     "description": "An e2e Playwright test fails intermittently (1 in 20 runs). Outline a triage checklist + 3 most likely root causes for flake in a Playwright suite."},
    {"role": "validation", "type": "qa", "complexity": "simple",
     "title": "Playwright POM fixtures",
     "description": "Show idiomatic Playwright Page Object Model setup using fixtures. Include test fixture + a sample LoginPage with a login() method."},
    {"role": "validation", "type": "qa", "complexity": "simple",
     "title": "BDD scenario for password reset",
     "description": "Write Gherkin scenarios for password reset covering: happy path, expired link, invalid email, rate-limited request."},

    # ---------- project-scale tasks (15) ----------
    # WBS — full decomposition
    {"role": "decomposition", "type": "wbs", "complexity": "complex",
     "title": "WBS: user-onboarding flow",
     "description": "Build a new user-onboarding flow (signup → email-verify → org-setup → team-invite → first-project). Produce a full WBS: epics, features, INVEST stories with AC, dependencies, ~SP each. Mark MVP scope."},
    {"role": "decomposition", "type": "wbs", "complexity": "complex",
     "title": "WBS: billing system",
     "description": "Build subscription billing (plans, trials, prorated upgrades, dunning, invoices). Produce a WBS with epics → features → INVEST stories. Identify external integrations (Stripe), data model, async jobs, and 3 risks."},
    {"role": "decomposition", "type": "wbs", "complexity": "complex",
     "title": "WBS: migrate to OAuth 2.0",
     "description": "Migrate auth from email/password to OAuth 2.0 + magic links, supporting Google + Microsoft. Decompose into stories, identify backward-compat path for existing users, name the 5 highest-risk decisions."},

    # PRD comprehensive
    {"role": "product-manager", "type": "prd", "complexity": "complex",
     "title": "PRD: in-product analytics dashboard",
     "description": "Write a comprehensive PRD for a customer-facing analytics dashboard: problem framing, user personas (admin / analyst), JTBD, key user stories with AC, success metrics (HEART), 3 alternatives considered, rollout plan, risks."},
    {"role": "product-manager", "type": "prd", "complexity": "complex",
     "title": "PRD: AI-suggested code review",
     "description": "Write a comprehensive PRD for adding AI-suggested review comments on PRs. Cover: target user, prior art (Copilot reviews), explicit non-goals, MVP scope, model selection criteria, accuracy/precision targets, kill criteria."},
    {"role": "product-manager", "type": "prd", "complexity": "complex",
     "title": "PRD: real-time collaborative editor",
     "description": "Write a PRD for adding real-time collaborative document editing (Figma/Notion-style). Cover: tech tradeoffs (CRDT vs OT), MVP scope, success metrics, scaling concerns, 5 user stories with AC, risk register."},

    # Tech design
    {"role": "developer", "type": "tech_design", "complexity": "complex",
     "title": "Tech design: async job system",
     "description": "Design an event-driven async job system for a Python backend (FastAPI). Cover: queue choice (Redis/Celery/Temporal), idempotency, retries, dead-letter, observability (OTEL), failure modes, capacity planning, deployment topology."},
    {"role": "developer", "type": "tech_design", "complexity": "complex",
     "title": "Tech design: multi-tenant data isolation",
     "description": "Design multi-tenant data isolation for a B2B SaaS on Postgres. Cover: row-level security, schema-per-tenant vs row-level, query performance, migration strategy, audit logging, GDPR considerations, escape-hatch for noisy neighbour."},
    {"role": "developer", "type": "tech_design", "complexity": "complex",
     "title": "Tech design: GraphQL→REST migration",
     "description": "Plan migrating a GraphQL API to REST (OpenAPI-spec) with N-month parallel-running window. Cover: route mapping, deprecation strategy, observability, error contract, performance regression detection, client communication."},

    # Project planning
    {"role": "planning", "type": "planning", "complexity": "complex",
     "title": "Q3 launch plan",
     "description": "Build a Q3 launch plan for a 12-person engineering team shipping 3 customer-facing features. Cover: capacity math, dependency graph, risk register, contingency, rollback strategy, comms calendar, on-call rotation."},
    {"role": "planning", "type": "planning", "complexity": "complex",
     "title": "Capacity plan: 8-person team",
     "description": "Build a 4-week capacity plan for an 8-person team. Two devs are part-time (60%), one is on PTO Week 2, team velocity averages 55sp. Allocate to: 30sp planned epic + 15sp bug-fix budget + 10sp tech debt. Show math."},
    {"role": "planning", "type": "planning", "complexity": "complex",
     "title": "Risk register for OAuth migration",
     "description": "Build a risk register for an OAuth migration project (40+ users impacted, 6-week timeline). 8-12 risks: probability, impact, mitigation, owner. Highlight the 3 stop-the-line risks."},

    # End-to-end debug
    {"role": "self-heal", "type": "debug", "complexity": "complex",
     "title": "Debug: production 5xx spike",
     "description": "Customer reports 5xx rate jumped from 0.1% to 4% over 20 minutes. Logs show DB connection pool exhaustion. Walk through diagnosis: data to collect, hypotheses ranked, immediate mitigation, root-cause investigation steps, postmortem outline."},
    {"role": "self-heal", "type": "debug", "complexity": "complex",
     "title": "Debug: memory leak in worker",
     "description": "Async worker pod RSS grows 50MB/h indefinitely. Stage diagnosis: tools to use (memray, tracemalloc, py-spy), what to capture, top-5 suspect patterns in async-Python code, mitigation order."},
    {"role": "self-heal", "type": "debug", "complexity": "complex",
     "title": "Debug: slow Postgres query",
     "description": "An API endpoint that joins 4 tables degraded p95 from 80ms → 2.3s over 2 weeks. Plan the diagnosis: EXPLAIN ANALYZE, pg_stat_statements, autovacuum check, index review, query rewrite options. Include rollback if a new index makes things worse."},
]


# ============================================================
# Tool definition for library_search
# ============================================================
LIBRARY_SEARCH_TOOL: dict[str, Any] = {
    "name": "library_search",
    "description": (
        "Search the Lighthouse knowledge base. Returns up to top_k ranked "
        "hits, each a one-line fact + an `[ep:<id>]` source handle. Hits "
        "now include prose snippets from the ingested article body, not "
        "just entity-relation triples. Use focused queries (3-8 words). "
        "If a hit's one-liner isn't enough, call `fetch_source` with its "
        "ep id to read the full original paragraph (a few KB)."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "top_k": {"type": "integer", "default": 5, "minimum": 1, "maximum": 15},
        },
        "required": ["query"],
    },
}

FETCH_SOURCE_TOOL: dict[str, Any] = {
    "name": "fetch_source",
    "description": (
        "Pull the ORIGINAL ingested paragraph behind a search hit. Pass "
        "the `ep` id from a `library_search` result (e.g. ep:9f3a2c). "
        "Returns the article title, source URL, and the full body text "
        "(a few KB). Prefer this over re-searching when you already have "
        "a relevant hit but its one-line summary isn't detailed enough — "
        "one round-trip gets you the canonical prose instead of N more "
        "searches."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "episode_id": {"type": "string"},
        },
        "required": ["episode_id"],
    },
}

LIGHTHOUSE_INSTRUCTIONS = """\

## Using Lighthouse (knowledge base)

You have two tools:

- `library_search(query, top_k)` — find facts. Returns ranked hits
  with one-line summaries plus an `[ep:<id>]` source handle each.
  Use focused queries (3-8 words), not the user's full prompt.
- `fetch_source(episode_id)` — pull the FULL original article
  paragraph behind a hit. Use when the one-line summary isn't
  enough — e.g. you need step-by-step instructions, code, exact
  config values, or RFC wording. One `fetch_source` call returns a
  few KB of canonical prose; that's almost always cheaper than 5
  more searches.

Typical flow: one `library_search` → scan summaries → if a hit is
close but thin, call `fetch_source` on its ep id. Cite findings
inline with `(per Lighthouse: ...)`. If the corpus has nothing
useful, say so and continue from memory rather than fabricating.
"""


# ============================================================
# Telemetry dataclasses
# ============================================================


@dataclass
class CallTelemetry:
    """Per-Claude-call counters."""

    stage: str  # "plan" | "execute" | "review" | "finalize" | "judge"
    duration_ms: float
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read: int = 0
    cache_write: int = 0
    tool_calls: int = 0  # how many tool_use blocks fired in this call

    @property
    def cost_usd(self) -> float:
        return (
            self.input_tokens * PRICING["input"]
            + self.output_tokens * PRICING["output"]
            + self.cache_read * PRICING["cache_read"]
            + self.cache_write * PRICING["cache_write"]
        )

    @property
    def total_tokens(self) -> int:
        return (
            self.input_tokens
            + self.output_tokens
            + self.cache_read
            + self.cache_write
        )


@dataclass
class RunTelemetry:
    """One full agentic run (A or B) for one task."""

    mode: str  # "A" | "B"
    plan: str = ""
    final: str = ""
    review_notes: str = ""
    tool_queries: list[str] = field(default_factory=list)
    tool_hits_per_query: list[int] = field(default_factory=list)
    calls: list[CallTelemetry] = field(default_factory=list)

    @property
    def total_cost(self) -> float:
        return sum(c.cost_usd for c in self.calls)

    @property
    def total_tokens(self) -> dict[str, int]:
        out = {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0}
        for c in self.calls:
            out["input"] += c.input_tokens
            out["output"] += c.output_tokens
            out["cache_read"] += c.cache_read
            out["cache_write"] += c.cache_write
        return out

    @property
    def total_time_ms(self) -> float:
        return sum(c.duration_ms for c in self.calls)

    @property
    def call_count(self) -> int:
        return len(self.calls)

    @property
    def tool_calls_total(self) -> int:
        return sum(c.tool_calls for c in self.calls)


# ============================================================
# Helpers
# ============================================================


def load_role_prompt(role: str) -> str:
    p = Path(
        f"/Users/denyskuzin/Projects/ship/apps/backend/app/resources/agent_roles/{role}.md"
    )
    raw = p.read_text(encoding="utf-8")
    raw = re.sub(r"^---\n.*?\n---\n", "", raw, count=1, flags=re.DOTALL)
    raw = re.sub(r"\{\{[A-Z_]+\}\}", "", raw)
    return raw.strip()


_CACHE = {"type": "ephemeral"}


async def _call_claude(
    client,
    *,
    model: str,
    system_text: str,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None = None,
    max_tokens: int = 1500,
    temperature: float = 0.0,
    stage: str = "?",
) -> tuple[Any, CallTelemetry]:
    """One Claude call with timing + usage capture."""
    t0 = time.monotonic()
    kwargs: dict[str, Any] = {
        "model": model,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "system": [{"type": "text", "text": system_text, "cache_control": _CACHE}],
        "messages": messages,
    }
    if tools:
        kwargs["tools"] = tools
    resp = await client.messages.create(**kwargs)
    elapsed = (time.monotonic() - t0) * 1000
    tool_calls = sum(
        1 for b in resp.content if getattr(b, "type", "") == "tool_use"
    )
    tel = CallTelemetry(
        stage=stage,
        duration_ms=elapsed,
        input_tokens=resp.usage.input_tokens,
        output_tokens=resp.usage.output_tokens,
        cache_read=getattr(resp.usage, "cache_read_input_tokens", 0),
        cache_write=getattr(resp.usage, "cache_creation_input_tokens", 0),
        tool_calls=tool_calls,
    )
    return resp, tel


def _extract_text(resp) -> str:
    return "".join(
        b.text for b in resp.content if getattr(b, "type", "") == "text"
    ).strip()


def _format_search_payload(hits, top_k: int) -> str:
    """Render search hits with their ep handles so the agent can call
    `fetch_source` afterwards. Truncates UUID to 8 chars in the wire
    payload to save tokens; the graph lookup is by prefix match below."""
    if not hits:
        return "(no hits)"
    lines = []
    for h in hits[:top_k]:
        ep = (h.episode_ids[0] if h.episode_ids else "").replace("-", "")[:8]
        tag = f" [ep:{ep}]" if ep else ""
        lines.append(f"- {h.summary}{tag}")
    return "\n".join(lines)


FETCH_SOURCE_PER_TASK_CAP = 8  # soft cap; over this we return a hint
                                # instead of body content. 14.3 avg on
                                # Sonnet was wasteful — most lift comes
                                # from the first 5-6 fetches.


async def _dispatch_tool(
    *,
    graph,
    name: str,
    input_: dict[str, Any],
    tel: "RunTelemetry",
) -> str:
    """Run one Lighthouse tool call. Returns the wire payload string.
    Tracks the call into RunTelemetry's tool_queries/tool_hits_per_query
    so the report counts every tool round (search OR fetch_source)."""
    if name == "library_search":
        query = input_.get("query", "")
        top_k = int(input_.get("top_k", 5))
        tel.tool_queries.append(query)
        try:
            hits = await graph.search(query, top_k=top_k)
            tel.tool_hits_per_query.append(len(hits))
            return _format_search_payload(hits, top_k)
        except Exception as exc:  # noqa: BLE001
            tel.tool_hits_per_query.append(0)
            return f"(library_search errored: {exc})"
    if name == "fetch_source":
        ep_in = str(input_.get("episode_id", "")).strip()
        tel.tool_queries.append(f"fetch_source({ep_in})")
        # Soft cap per task — counts prior fetch_source calls in this
        # RunTelemetry. Beyond the cap, return a hint instead of body
        # so the agent stops calling and commits to a draft.
        prior_fs = sum(
            1 for q in tel.tool_queries[:-1]
            if str(q).startswith("fetch_source(")
        )
        if prior_fs >= FETCH_SOURCE_PER_TASK_CAP:
            tel.tool_hits_per_query.append(0)
            return (
                f"(fetch_source soft-capped at {FETCH_SOURCE_PER_TASK_CAP} "
                "calls per task; you've gathered enough sources — commit "
                "to a draft answer now)"
            )
        if not ep_in:
            tel.tool_hits_per_query.append(0)
            return "(fetch_source: missing episode_id)"
        # Resolve short prefix (8-char wire form) to a full uuid via the
        # Neo4j driver. ``KnowledgeGraph.fetch_source`` only matches an
        # exact uuid, so a quick prefix lookup keeps the wire payload
        # small without forcing the agent to repeat full UUIDs.
        try:
            ep_full = await _resolve_episode(graph, ep_in)
            if ep_full is None:
                tel.tool_hits_per_query.append(0)
                return f"(fetch_source: no episode matches '{ep_in}')"
            src = await graph.fetch_source(ep_full)
            if src is None:
                tel.tool_hits_per_query.append(0)
                return f"(fetch_source: episode {ep_full[:8]} not found)"
            tel.tool_hits_per_query.append(1)
            # Cap body at ~6 KB so a single fetch doesn't blow the
            # context budget. Pages are typically 1-4 KB anyway; this
            # only trims the very long ones.
            body = src.content or ""
            if len(body) > 6000:
                body = body[:6000] + "\n... [truncated]"
            return (
                f"# {src.name}\n"
                f"Source: {src.source}\n\n"
                f"{body}"
            )
        except Exception as exc:  # noqa: BLE001
            tel.tool_hits_per_query.append(0)
            return f"(fetch_source errored: {exc})"
    return f"(unknown tool: {name})"


async def _resolve_episode(graph, ep_in: str) -> str | None:
    """Resolve a short prefix (8-hex, no dashes) to a full Episodic uuid.
    If the agent already passed a full uuid we just return it."""
    # Full uuid: 32-hex with 4 dashes. Accept it as-is.
    if len(ep_in) >= 32 and "-" in ep_in:
        return ep_in
    # Otherwise treat as hex prefix and look up via the Neo4j driver.
    from neo4j import AsyncGraphDatabase

    s = graph._settings  # noqa: SLF001 — bench is internal tooling
    driver = AsyncGraphDatabase.driver(
        s.neo4j_uri, auth=(s.neo4j_user, s.neo4j_password)
    )
    try:
        async with driver.session(database=s.neo4j_database) as session:
            # The wire form is the uuid with dashes stripped, truncated
            # to 8 chars. Convert back by allowing a prefix match on
            # ``replace(uuid, '-', '')``.
            result = await session.run(
                "MATCH (n:Episodic) "
                "WHERE replace(n.uuid, '-', '') STARTS WITH $pfx "
                "RETURN n.uuid AS uuid LIMIT 1",
                pfx=ep_in.lower(),
            )
            row = await result.single()
            return str(row["uuid"]) if row else None
    finally:
        await driver.close()


# ============================================================
# Agentic loop — plan → execute → review → finalize
# ============================================================


async def agentic_run(
    client,
    *,
    task: dict[str, Any],
    role_prompt: str,
    mode: str,
    graph,
    model: str,
) -> RunTelemetry:
    """One agentic run. ``mode == 'A'``: no tool. ``mode == 'B'``:
    library_search tool + Lighthouse-use instructions appended."""
    tel = RunTelemetry(mode=mode)
    if mode == "B":
        system_text = role_prompt + LIGHTHOUSE_INSTRUCTIONS
        tools = [LIBRARY_SEARCH_TOOL, FETCH_SOURCE_TOOL]
    else:
        system_text = role_prompt
        tools = None

    task_user = f"# {task['title']}\n\n{task['description']}"

    # --------- Stage 1: PLAN ---------
    plan_user = (
        task_user
        + "\n\n---\n\n"
        + "First, draft a 3-5 step PLAN for how you'll approach this. "
        "Do NOT answer the task itself yet. Output the plan only, as "
        "a numbered list with one line per step."
    )
    plan_resp, plan_tel = await _call_claude(
        client,
        model=model,
        system_text=system_text,
        messages=[{"role": "user", "content": plan_user}],
        tools=tools,
        stage="plan",
        max_tokens=600,
    )
    tel.calls.append(plan_tel)
    plan_text = _extract_text(plan_resp)
    tel.plan = plan_text

    # --------- Stage 2: EXECUTE ---------
    execute_user = (
        task_user
        + "\n\n## Your plan\n"
        + plan_text
        + "\n\n---\n\n"
        + "Now execute the plan. Produce your full draft answer. "
        + (
            "Use library_search to find facts; when a hit looks "
            "relevant but its one-line summary is too thin, fetch_source "
            "on its ep id to read the full article."
            if mode == "B"
            else ""
        )
    )
    messages: list[dict[str, Any]] = [
        {"role": "user", "content": execute_user},
    ]
    # Track ALL text accumulated across tool-use iterations — needed so
    # we don't drop a long, partially-tool-using draft on the floor when
    # the loop exhausts.
    accumulated_text: list[str] = []
    tool_uses: list = []
    resp = None
    for _ in range(MAX_TOOL_LOOP):
        resp, et = await _call_claude(
            client,
            model=model,
            system_text=system_text,
            messages=messages,
            tools=tools,
            stage="execute",
            max_tokens=2000,
        )
        tel.calls.append(et)
        # Capture every text block from this round, even when the model
        # also issued tool_use blocks alongside.
        this_round_text = _extract_text(resp)
        if this_round_text:
            accumulated_text.append(this_round_text)
        tool_uses = [b for b in resp.content if getattr(b, "type", "") == "tool_use"]
        if not tool_uses:
            break
        assistant_blocks: list[dict[str, Any]] = []
        for b in resp.content:
            if getattr(b, "type", "") == "text" and b.text.strip():
                assistant_blocks.append({"type": "text", "text": b.text})
        for b in tool_uses:
            assistant_blocks.append(
                {"type": "tool_use", "id": b.id, "name": b.name, "input": b.input}
            )
        messages.append({"role": "assistant", "content": assistant_blocks})
        tool_results: list[dict[str, Any]] = []
        for b in tool_uses:
            payload = await _dispatch_tool(
                graph=graph, name=b.name, input_=b.input, tel=tel
            )
            tool_results.append(
                {"type": "tool_result", "tool_use_id": b.id, "content": payload}
            )
        messages.append({"role": "user", "content": tool_results})
    # If we exhausted the loop with the model still wanting to call tools
    # and have not yet produced a substantive answer, force one final
    # no-tools round so the agent has to commit to a draft. Otherwise
    # the agentic pipeline downstream operates on an empty draft and
    # the self-review/finalize stages can't fix what isn't there.
    draft_text = "\n\n".join(accumulated_text).strip()
    if tool_uses or len(draft_text) < 200:
        # Append a final assistant turn so the conversation has a clean
        # boundary, then ask without tools for the draft.
        if tool_uses:
            # We need to satisfy the open tool_use blocks first.
            assistant_blocks = []
            for b in resp.content:
                if getattr(b, "type", "") == "text" and b.text.strip():
                    assistant_blocks.append({"type": "text", "text": b.text})
            for b in tool_uses:
                assistant_blocks.append(
                    {"type": "tool_use", "id": b.id, "name": b.name, "input": b.input}
                )
            messages.append({"role": "assistant", "content": assistant_blocks})
            tool_results = []
            for b in tool_uses:
                payload = await _dispatch_tool(
                    graph=graph, name=b.name, input_=b.input, tel=tel
                )
                tool_results.append(
                    {"type": "tool_result", "tool_use_id": b.id, "content": payload}
                )
            messages.append({"role": "user", "content": tool_results})
        messages.append(
            {
                "role": "user",
                "content": (
                    "You've gathered enough context. Stop calling tools and "
                    "produce your full draft answer to the original task NOW. "
                    "This is the final draft pass."
                ),
            }
        )
        resp, et = await _call_claude(
            client,
            model=model,
            system_text=system_text,
            messages=messages,
            tools=None,  # explicitly no tools — force commit
            stage="execute_force",
            max_tokens=3000,
        )
        tel.calls.append(et)
        force_text = _extract_text(resp)
        if force_text:
            accumulated_text.append(force_text)
        draft_text = "\n\n".join(accumulated_text).strip()

    # --------- Stage 3: SELF-REVIEW ---------
    review_messages = list(messages)
    review_messages.append(
        {"role": "assistant", "content": [{"type": "text", "text": draft_text}]}
    )
    review_messages.append(
        {
            "role": "user",
            "content": (
                "Now critique your own draft above. Identify 1-3 specific "
                "weaknesses (missing detail, unsupported claim, format gap). "
                "Output just the critique, not a rewrite."
            ),
        }
    )
    review_resp, rev_tel = await _call_claude(
        client,
        model=model,
        system_text=system_text,
        messages=review_messages,
        tools=None,
        stage="review",
        max_tokens=400,
    )
    tel.calls.append(rev_tel)
    tel.review_notes = _extract_text(review_resp)

    # --------- Stage 4: FINALIZE ---------
    final_messages = list(review_messages)
    final_messages.append(
        {"role": "assistant", "content": [{"type": "text", "text": tel.review_notes}]}
    )
    final_messages.append(
        {
            "role": "user",
            "content": (
                "Now produce the final answer addressing the critique. "
                "Output as a **clear, readable markdown answer** — sections, "
                "tables, code blocks where useful. Do NOT output JSON wire "
                "formats, tool-call payloads, or sidecar structures even if "
                "your role prompt suggests them — this is an evaluation "
                "answer, not a production handoff. Aim for substantive "
                "depth with concrete details."
            ),
        }
    )
    final_resp, fin_tel = await _call_claude(
        client,
        model=model,
        system_text=system_text,
        messages=final_messages,
        tools=None,
        stage="finalize",
        max_tokens=4000,
    )
    tel.calls.append(fin_tel)
    tel.final = _extract_text(final_resp)

    return tel


# ============================================================
# Judge
# ============================================================


JUDGE_RUBRIC = """\
You compare two answers to the same task. Score each on four axes
0-10 each (total 0-40):

1. specificity     (concrete details / numbers / names vs hand-wavy)
2. citation        (references to sources / standards / canonical names)
3. actionability   (could a junior agent execute on this directly?)
4. accuracy        (factually correct, no fabrications)

Then declare a winner. If equivalent quality, "tie".

Reply strict JSON only, no prose:

{
  "a": {"specificity":0-10,"citation":0-10,"actionability":0-10,"accuracy":0-10,"total":0-40},
  "b": {"specificity":0-10,"citation":0-10,"actionability":0-10,"accuracy":0-10,"total":0-40},
  "winner": "a" | "b" | "tie",
  "rationale": "<= 40 words"
}
"""


async def judge_pair(
    client,
    *,
    task: dict[str, Any],
    answer_a: str,
    answer_b: str,
    model: str,
) -> tuple[dict[str, Any], CallTelemetry]:
    resp, tel = await _call_claude(
        client,
        model=model,
        system_text=JUDGE_RUBRIC,
        messages=[
            {
                "role": "user",
                "content": (
                    f"## Task ({task['role']}, {task['type']}, {task['complexity']})\n"
                    f"{task['title']}\n\n{task['description']}\n\n"
                    f"## Answer A (no-lighthouse)\n{answer_a[:6000]}\n\n"
                    f"## Answer B (with-lighthouse)\n{answer_b[:6000]}"
                ),
            }
        ],
        tools=None,
        max_tokens=500,
        stage="judge",
    )
    text = _extract_text(resp).strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\n|\n```$", "", text)
    try:
        verdict = json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if m:
            try:
                verdict = json.loads(m.group(0))
            except Exception:
                verdict = {"winner": "tie", "rationale": "unparseable", "_raw": text}
        else:
            verdict = {"winner": "tie", "rationale": "unparseable", "_raw": text}
    return verdict, tel


# ============================================================
# Per-task pair runner
# ============================================================


async def run_task_pair(
    client,
    task_idx: int,
    task: dict[str, Any],
    role_prompt: str,
    graph,
    model: str,
    out_dir: Path,
    progress_lock: asyncio.Lock,
    progress_state: dict,
) -> dict[str, Any]:
    """A + B + judge for one task. Returns the full record."""
    # A and B are independent — we still run them serially per task so
    # the judge sees the full pair, but they share no state.
    a = await agentic_run(
        client,
        task=task,
        role_prompt=role_prompt,
        mode="A",
        graph=graph,
        model=model,
    )
    b = await agentic_run(
        client,
        task=task,
        role_prompt=role_prompt,
        mode="B",
        graph=graph,
        model=model,
    )
    verdict, judge_tel = await judge_pair(
        client, task=task, answer_a=a.final, answer_b=b.final, model=model
    )

    rec = {
        "task_idx": task_idx,
        "task": task,
        "a": asdict(a),
        "b": asdict(b),
        "verdict": verdict,
        "judge_telemetry": asdict(judge_tel),
    }
    (out_dir / f"task-{task_idx:02d}.json").write_text(json.dumps(rec, indent=2))

    async with progress_lock:
        progress_state["done"] += 1
        ar, br = (
            verdict.get("a", {}).get("total", 0),
            verdict.get("b", {}).get("total", 0),
        )
        win = verdict.get("winner", "?")
        print(
            f"  [{progress_state['done']:>2}/{progress_state['total']}] "
            f"{task['role']:<16s} {task['type']:<12s} {task['title'][:34]:<34s} "
            f"A={ar:>2}/40 B={br:>2}/40 → {win:<3s} "
            f"$A={a.total_cost:.4f} $B={b.total_cost:.4f} "
            f"calls={a.call_count}/{b.call_count}",
            flush=True,
        )

    return rec


# ============================================================
# Report rendering
# ============================================================


def render_report(records: list[dict[str, Any]], out_dir: Path) -> str:
    n = len(records)
    win_b = sum(1 for r in records if r["verdict"].get("winner") == "b")
    win_a = sum(1 for r in records if r["verdict"].get("winner") == "a")
    ties = sum(1 for r in records if r["verdict"].get("winner") == "tie")
    other = n - win_a - win_b - ties

    mean_a = sum(r["verdict"].get("a", {}).get("total", 0) for r in records) / n
    mean_b = sum(r["verdict"].get("b", {}).get("total", 0) for r in records) / n

    cost_a = sum(r["a"]["calls"][i]["cost_usd"] if False else 0 for r in records for i in range(0))
    cost_a = sum(sum(c.get("cost_usd") or _cost(c) for c in r["a"]["calls"]) for r in records)
    cost_b = sum(sum(c.get("cost_usd") or _cost(c) for c in r["b"]["calls"]) for r in records)
    cost_judge = sum(_cost(r["judge_telemetry"]) for r in records)

    time_a = sum(sum(c["duration_ms"] for c in r["a"]["calls"]) for r in records) / 1000
    time_b = sum(sum(c["duration_ms"] for c in r["b"]["calls"]) for r in records) / 1000
    time_judge = sum(r["judge_telemetry"]["duration_ms"] for r in records) / 1000

    tokens_a = _agg_tokens(records, "a")
    tokens_b = _agg_tokens(records, "b")

    by_role: dict[str, list[float]] = defaultdict(list)
    by_type: dict[str, list[float]] = defaultdict(list)
    by_complexity: dict[str, list[float]] = defaultdict(list)
    for r in records:
        d = (
            r["verdict"].get("b", {}).get("total", 0)
            - r["verdict"].get("a", {}).get("total", 0)
        )
        by_role[r["task"]["role"]].append(d)
        by_type[r["task"]["type"]].append(d)
        by_complexity[r["task"]["complexity"]].append(d)

    all_queries: list[str] = []
    tool_hits: list[int] = []
    for r in records:
        all_queries.extend(r["b"].get("tool_queries", []))
        tool_hits.extend(r["b"].get("tool_hits_per_query", []))
    avg_tool_calls_b = sum(r["b"].get("tool_calls_total", 0) or sum(c.get("tool_calls", 0) for c in r["b"]["calls"]) for r in records) / n

    md: list[str] = []
    md.append(f"# Lighthouse Agent Bench — Report\n")
    md.append(f"**Run:** {out_dir.name}  |  **Tasks:** {n}  |  **Model:** Claude Sonnet 4.6 (judge + agents)\n")
    md.append(f"**Methodology:** Each task is run twice through a 4-stage agentic loop")
    md.append(f"(plan → execute → self-review → finalize). Mode A has no tools, Mode B")
    md.append(f"has access to a `library_search` tool against the Lighthouse knowledge graph")
    md.append(f"(1052 episodes, 781 facts, ~285 source-roots, mostly SSR docs via sitemap).\n")
    md.append("---\n")
    md.append("## Headline numbers\n")
    md.append("| Metric | A (no-MCP) | B (Lighthouse) | Δ |")
    md.append("|---|---|---|---|")
    md.append(f"| Mean score (0-40) | {mean_a:.1f} | **{mean_b:.1f}** | **+{mean_b - mean_a:.1f}** |")
    md.append(f"| Winrate | {win_a / n * 100:.0f}% ({win_a}) | **{win_b / n * 100:.0f}%** ({win_b}) | ties {ties / n * 100:.0f}% ({ties}) |")
    md.append(f"| Total cost | ${cost_a:.2f} | ${cost_b:.2f} | +${cost_b - cost_a:.2f} |")
    md.append(f"| Cost / task | ${cost_a / n:.3f} | ${cost_b / n:.3f} | +${(cost_b - cost_a) / n:.3f} |")
    md.append(f"| Wall time (sum) | {time_a / 60:.1f} min | {time_b / 60:.1f} min | +{(time_b - time_a) / 60:.1f} min |")
    md.append(f"| Avg time / task | {time_a / n:.1f} s | {time_b / n:.1f} s | +{(time_b - time_a) / n:.1f} s |")
    md.append(f"| Tokens (in + out + cache) | {tokens_a:,} | {tokens_b:,} | +{tokens_b - tokens_a:,} |")
    md.append(f"| Tool calls (B) | — | {avg_tool_calls_b:.1f} avg / task | — |")
    md.append(f"| Judge cost | — | — | ${cost_judge:.2f} ({time_judge / 60:.1f} min) |")
    md.append("")
    md.append("---\n")
    md.append("## Per-role delta (B − A, mean score points)\n")
    md.append("| Role | n | Δ mean | Note |")
    md.append("|---|---|---|---|")
    for role, deltas in sorted(by_role.items(), key=lambda kv: -sum(kv[1]) / len(kv[1])):
        avg = sum(deltas) / len(deltas)
        sign = "🟢" if avg > 0 else "🔴" if avg < 0 else "⚪"
        md.append(f"| {role} | {len(deltas)} | {sign} {avg:+.1f} | {_role_note(role, avg)} |")
    md.append("")
    md.append("## Per-task-type delta\n")
    md.append("| Task type | n | Δ mean |")
    md.append("|---|---|---|")
    for t, deltas in sorted(by_type.items(), key=lambda kv: -sum(kv[1]) / len(kv[1])):
        md.append(f"| {t} | {len(deltas)} | {sum(deltas) / len(deltas):+.1f} |")
    md.append("")
    md.append("## Per-complexity delta\n")
    md.append("| Complexity | n | Δ mean |")
    md.append("|---|---|---|")
    for c, deltas in sorted(by_complexity.items(), key=lambda kv: -sum(kv[1]) / len(kv[1])):
        md.append(f"| {c} | {len(deltas)} | {sum(deltas) / len(deltas):+.1f} |")
    md.append("")

    md.append("---\n")
    md.append("## Top-5 Lighthouse wins\n")
    md.append("| Task | A | B | Δ | Rationale |")
    md.append("|---|---|---|---|---|")
    wins = sorted(
        [r for r in records if r["verdict"].get("winner") == "b"],
        key=lambda r: r["verdict"].get("b", {}).get("total", 0) - r["verdict"].get("a", {}).get("total", 0),
        reverse=True,
    )[:5]
    for r in wins:
        a_t = r["verdict"].get("a", {}).get("total", 0)
        b_t = r["verdict"].get("b", {}).get("total", 0)
        md.append(f"| [{r['task']['role']}] {r['task']['title']} | {a_t} | {b_t} | **+{b_t - a_t}** | {r['verdict'].get('rationale', '')[:120]} |")
    md.append("")
    md.append("## Lighthouse losses (A won)\n")
    md.append("| Task | A | B | Δ | Rationale |")
    md.append("|---|---|---|---|---|")
    losses = [r for r in records if r["verdict"].get("winner") == "a"]
    losses.sort(
        key=lambda r: r["verdict"].get("a", {}).get("total", 0) - r["verdict"].get("b", {}).get("total", 0),
        reverse=True,
    )
    for r in losses[:5]:
        a_t = r["verdict"].get("a", {}).get("total", 0)
        b_t = r["verdict"].get("b", {}).get("total", 0)
        md.append(f"| [{r['task']['role']}] {r['task']['title']} | {a_t} | {b_t} | **{b_t - a_t:+d}** | {r['verdict'].get('rationale', '')[:120]} |")
    md.append("")

    md.append("---\n")
    md.append("## Tool usage analysis (Mode B)\n")
    md.append(f"- **Total `library_search` calls**: {len(all_queries)}")
    md.append(f"- **Avg tool calls per task**: {len(all_queries) / n:.1f}")
    md.append(f"- **Queries with ≥1 hit**: {sum(1 for h in tool_hits if h > 0)} / {len(tool_hits)} ({sum(1 for h in tool_hits if h > 0) / max(1, len(tool_hits)) * 100:.0f}%)")
    md.append(f"- **Avg hits per query**: {sum(tool_hits) / max(1, len(tool_hits)):.1f}")
    md.append("")
    md.append("### Top-15 most-frequent tool queries\n")
    md.append("| Query | Count |")
    md.append("|---|---|")
    for q, c in Counter(all_queries).most_common(15):
        md.append(f"| `{q}` | {c} |")
    md.append("")

    md.append("---\n")
    md.append("## Cost / time efficiency\n")
    md.append("| Mode | $ / point | sec / point |")
    md.append("|---|---|---|")
    pts_a = mean_a * n
    pts_b = mean_b * n
    md.append(f"| A (no-MCP) | ${cost_a / max(1, pts_a):.5f} | {time_a / max(1, pts_a):.2f} |")
    md.append(f"| B (Lighthouse) | ${cost_b / max(1, pts_b):.5f} | {time_b / max(1, pts_b):.2f} |")
    md.append("")
    md.append("Lower is more efficient — fewer $ or seconds per quality point.\n")
    md.append("")
    md.append("---\n")
    md.append(f"_Generated {datetime.now(UTC).isoformat()} — Lighthouse {out_dir.name}_\n")

    return "\n".join(md)


def _cost(c: dict) -> float:
    return (
        c.get("input_tokens", 0) * PRICING["input"]
        + c.get("output_tokens", 0) * PRICING["output"]
        + c.get("cache_read", 0) * PRICING["cache_read"]
        + c.get("cache_write", 0) * PRICING["cache_write"]
    )


def _agg_tokens(records: list[dict[str, Any]], mode: str) -> int:
    total = 0
    for r in records:
        for c in r[mode]["calls"]:
            total += c["input_tokens"] + c["output_tokens"] + c["cache_read"] + c["cache_write"]
    return total


def _role_note(role: str, delta: float) -> str:
    if delta > 10:
        return "huge specific-syntax win"
    if delta > 4:
        return "clear win"
    if delta > 1:
        return "modest win"
    if delta > -1:
        return "neutral"
    if delta > -3:
        return "slight regression — noise"
    return "regression — needs source tuning"


# ============================================================
# Main
# ============================================================


async def main() -> int:
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--task-type",
        action="append",
        default=None,
        help="Filter to tasks with this 'type' (qa/wbs/prd/tech_design/"
        "planning/debug). Repeatable: --task-type wbs --task-type prd.",
    )
    parser.add_argument(
        "--role",
        action="append",
        default=None,
        help="Filter to a single role. Repeatable.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Cap the number of tasks run (after filtering).",
    )
    parser.add_argument(
        "--model",
        type=str,
        default=None,
        help="Override agent model (default: lighthouse_model_main from .env).",
    )
    parser.add_argument(
        "--judge-model",
        type=str,
        default=None,
        help="Override judge model (default: same as --model).",
    )
    parser.add_argument(
        "--rerun-tasks",
        default=None,
        help="Comma-separated 1-based task indices to rerun. Requires --out-dir.",
    )
    parser.add_argument(
        "--out-dir",
        default=None,
        help="Existing run dir to write into (with --rerun-tasks).",
    )
    args = parser.parse_args()

    selected = list(TASKS)
    rerun_indices: set[int] | None = None
    if args.rerun_tasks:
        rerun_indices = {int(s) for s in args.rerun_tasks.split(",") if s.strip()}
        selected = [t for i, t in enumerate(TASKS, 1) if i in rerun_indices]
        if not selected:
            print(f"--rerun-tasks {sorted(rerun_indices)} matched no tasks", file=sys.stderr)
            return 1
    if args.task_type:
        wanted = set(args.task_type)
        selected = [t for t in selected if t["type"] in wanted]
    if args.role:
        wanted = set(args.role)
        selected = [t for t in selected if t["role"] in wanted]
    if args.limit:
        selected = selected[: args.limit]
    if not selected:
        print("No tasks match the given filters.", file=sys.stderr)
        return 1

    logging.basicConfig(level=logging.WARNING)
    from anthropic import AsyncAnthropic

    from lighthouse.core.config import get_settings
    from lighthouse.core.graph import KnowledgeGraph

    settings = get_settings()
    if not settings.anthropic_api_key:
        print("ANTHROPIC_API_KEY missing — abort", file=sys.stderr)
        return 1

    client = AsyncAnthropic(api_key=settings.anthropic_api_key)
    model = args.model or settings.lighthouse_model_main
    judge_model = args.judge_model or model

    graph = KnowledgeGraph(settings)
    await graph.initialize()

    if args.out_dir:
        out_dir = Path(args.out_dir)
        if not out_dir.exists():
            print(f"--out-dir {out_dir} does not exist", file=sys.stderr)
            return 1
    else:
        run_id = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        out_dir = Path("tools/eval/agent_bench") / run_id
        out_dir.mkdir(parents=True, exist_ok=True)

    print(
        f"Bench v2 — {len(selected)} tasks (of {len(TASKS)} total), "
        f"{CONCURRENCY} parallel, model={model}"
    )
    print(f"Filters: type={args.task_type or 'all'} role={args.role or 'all'}")
    print(f"Output: {out_dir}\n")

    role_prompts: dict[str, str] = {}
    for t in selected:
        if t["role"] not in role_prompts:
            role_prompts[t["role"]] = load_role_prompt(t["role"])

    sem = asyncio.Semaphore(CONCURRENCY)
    progress_lock = asyncio.Lock()
    progress_state = {"done": 0, "total": len(selected)}

    async def gated(idx, task):
        async with sem:
            return await run_task_pair(
                client,
                idx,
                task,
                role_prompts[task["role"]],
                graph,
                model,
                out_dir,
                progress_lock,
                progress_state,
            )

    task_to_idx = {id(t): i + 1 for i, t in enumerate(TASKS)}

    t0 = time.monotonic()
    results = await asyncio.gather(*(gated(task_to_idx[id(t)], t) for t in selected))
    elapsed = time.monotonic() - t0
    await graph.close()

    print(f"\nFinished {len(results)} tasks in {elapsed / 60:.1f} min")
    if args.rerun_tasks:
        all_records = []
        for tp in sorted(out_dir.glob("task-*.json")):
            try:
                all_records.append(json.loads(tp.read_text()))
            except Exception:
                pass
        report_md = render_report(all_records, out_dir)
    else:
        report_md = render_report(results, out_dir)
    (out_dir / "report.md").write_text(report_md)
    print(f"\n{'=' * 72}")
    print(report_md)
    print(f"{'=' * 72}")
    print(f"\nReport: {out_dir}/report.md")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
