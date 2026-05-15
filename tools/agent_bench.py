"""Agent benchmark — Lighthouse on/off side-by-side.

For each task we run the same role-prompted Claude twice:

- **A (no-mcp)**: role system prompt only, model answers from its
  training memory.
- **B (with-lighthouse)**: same role prompt + a short instructions
  block on how to use the ``library_search`` tool + the tool itself.
  Model may call the tool 0-N times before answering.

Both runs use Anthropic prompt caching on the system block — the
role prompt rarely changes between tasks and we score ~20 tasks, so
the cache hit dominates the second-task input cost.

A Claude Sonnet judge then scores each answer on 4 axes
(specificity, citation, actionability, factual-accuracy) 0-10 each,
plus picks an overall winner. Results land as JSON in
``tools/eval/agent_bench/<run_id>/`` and a compact comparison table
prints to stdout.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import sys
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# Lazy heavy imports happen inside functions

logger = logging.getLogger(__name__)


# ============================================================
# Task catalogue — 2 per role, mix of canonical + practical
# ============================================================
TASKS: list[dict[str, str]] = [
    # --- clarification ---
    {
        "role": "clarification",
        "title": "Clarify ambiguous AC",
        "description": (
            "A ticket says: 'User should be able to manage their profile'.\n"
            "Ask 3 clarifying questions a BA would ask before this can be sized."
        ),
    },
    {
        "role": "clarification",
        "title": "Write acceptance criteria",
        "description": (
            "Feature: 'User can upload a profile photo'.\n"
            "Write Given/When/Then acceptance criteria in Gherkin format "
            "covering happy path, file-size cap, and unsupported MIME type."
        ),
    },
    # --- decomposition ---
    {
        "role": "decomposition",
        "title": "Split a large story",
        "description": (
            "Story: 'As an admin I can manage team membership'.\n"
            "Split into 3-5 INVEST-compliant child stories with names + 1-line scope each."
        ),
    },
    {
        "role": "decomposition",
        "title": "Apply MECE to feature breakdown",
        "description": (
            "Feature: 'Customer-facing analytics dashboard'.\n"
            "Decompose into MECE sub-features. Name each + boundary."
        ),
    },
    # --- designer ---
    {
        "role": "designer",
        "title": "Spec dark-mode color tokens",
        "description": (
            "We use Tailwind with shadcn/ui components. Spec a token-level "
            "dark-mode palette for a dashboard (background, foreground, "
            "primary, destructive). Cite token-naming convention."
        ),
    },
    {
        "role": "designer",
        "title": "Accessibility for icon-only button",
        "description": (
            "A toolbar uses icon-only buttons (Edit, Delete, Share). "
            "Spec WCAG-compliant ARIA attributes and label behaviour."
        ),
    },
    # --- developer ---
    {
        "role": "developer",
        "title": "Implement FastAPI dependency injection",
        "description": (
            "Show idiomatic FastAPI dependency-injection for a DB session "
            "(SQLAlchemy async) with lifespan setup + Depends() usage."
        ),
    },
    {
        "role": "developer",
        "title": "Anthropic prompt caching",
        "description": (
            "Wire prompt caching on an Anthropic Python SDK call with a "
            "large system prompt + tool definitions. Where do cache_control "
            "markers go and what's the TTL?"
        ),
    },
    # --- devops ---
    {
        "role": "devops",
        "title": "Kubernetes readiness probe",
        "description": (
            "Add a readiness probe to a Deployment for a FastAPI service "
            "that exposes /health. Spec httpGet, initialDelaySeconds, "
            "periodSeconds, failureThreshold."
        ),
    },
    {
        "role": "devops",
        "title": "GitHub Actions secret rotation",
        "description": (
            "Workflow needs OIDC trust to AWS so we don't store long-lived "
            "secrets. Outline configure-aws-credentials with id-token: "
            "write permission + role assume."
        ),
    },
    # --- planning ---
    {
        "role": "planning",
        "title": "Sprint planning for cap-constrained team",
        "description": (
            "5-dev team with average velocity 35 sp/sprint. PO wants to "
            "fit a 50sp epic + ongoing bug-fix work. How do you advise the "
            "sprint plan?"
        ),
    },
    {
        "role": "planning",
        "title": "Story-point estimation guidance",
        "description": (
            "New team is debating SP vs hours. Lay out the 3 strongest "
            "arguments for story points + the 2 strongest arguments for "
            "NoEstimates."
        ),
    },
    # --- product-manager ---
    {
        "role": "product-manager",
        "title": "RICE prioritization on 4 features",
        "description": (
            "Score these features in RICE: (1) Dark mode, (2) SSO, "
            "(3) Mobile push notifications, (4) Bulk-import CSV. "
            "Use reasonable estimates."
        ),
    },
    {
        "role": "product-manager",
        "title": "Write a 1-page PRD",
        "description": (
            "Draft a 1-page PRD for 'Magic-link email login' targeting "
            "B2B SaaS users. Cover: problem, audience, solution, success "
            "metrics, risks."
        ),
    },
    # --- reviewer ---
    {
        "role": "reviewer",
        "title": "Review a Python async function",
        "description": (
            "Spot 5 issues in this code and write conventional-comment-"
            "style review remarks:\n\n"
            "```python\n"
            "async def get_user(uid):\n"
            "  try:\n"
            "    r = requests.get(f'/users/{uid}')\n"
            "    return r.json()\n"
            "  except:\n"
            "    return None\n"
            "```"
        ),
    },
    {
        "role": "reviewer",
        "title": "Auth endpoint security review",
        "description": (
            "OWASP-style review of a POST /login endpoint that accepts "
            "email+password. List the 5 most important checks the reviewer "
            "must run before approving."
        ),
    },
    # --- self-heal ---
    {
        "role": "self-heal",
        "title": "Blameless postmortem template",
        "description": (
            "30-minute outage caused by an OOM-killed pod after a deploy. "
            "Draft the postmortem with timeline, contributing factors, "
            "action items — blameless tone."
        ),
    },
    {
        "role": "self-heal",
        "title": "Flaky-test triage",
        "description": (
            "An e2e Playwright test fails intermittently (1 in 20 runs). "
            "Outline a triage checklist + 3 most likely root causes for "
            "flake in a Playwright suite."
        ),
    },
    # --- validation ---
    {
        "role": "validation",
        "title": "Playwright POM fixtures",
        "description": (
            "Show idiomatic Playwright Page Object Model setup using "
            "fixtures. Include test fixture + a sample LoginPage with "
            "a login() method."
        ),
    },
    {
        "role": "validation",
        "title": "BDD scenario for password reset",
        "description": (
            "Write Gherkin scenarios for password reset covering: "
            "happy path, expired link, invalid email, rate-limited "
            "request. Reference Cucumber best practices."
        ),
    },
]


# ============================================================
# Library-search tool definition (Anthropic tool-use format)
# ============================================================
LIBRARY_SEARCH_TOOL: dict[str, Any] = {
    "name": "library_search",
    "description": (
        "Search the Lighthouse knowledge base for canonical facts about a "
        "topic. Returns up to top_k facts with summaries. Use this BEFORE "
        "answering when the question references a specific framework, "
        "pattern, methodology, or industry standard — Lighthouse contains "
        "curated content from official docs, foundational books, and "
        "trusted technical blogs that augments your training. Call this "
        "tool with focused queries (3-8 words) rather than the user's "
        "full question."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Focused query — 3-8 words, technical terms",
            },
            "top_k": {
                "type": "integer",
                "default": 5,
                "minimum": 1,
                "maximum": 15,
            },
        },
        "required": ["query"],
    },
}


# ============================================================
# Helpers
# ============================================================


def load_role_prompt(role: str) -> str:
    """Read the role's BASE prompt from ship and strip the template
    placeholders we won't fill in for this benchmark."""
    p = Path(
        f"/Users/denyskuzin/Projects/ship/apps/backend/app/resources/agent_roles/{role}.md"
    )
    raw = p.read_text(encoding="utf-8")
    # Strip the YAML frontmatter (between two `---` lines) if present.
    raw = re.sub(r"^---\n.*?\n---\n", "", raw, count=1, flags=re.DOTALL)
    # Drop {{BASE}} and other unfilled handlebar markers — they'd
    # confuse the model into asking what they mean.
    raw = re.sub(r"\{\{[A-Z_]+\}\}", "", raw)
    return raw.strip()


def lighthouse_instructions() -> str:
    """Block appended to the role prompt when running the with-lighthouse
    leg. Tells the agent how to use the tool."""
    return (
        "\n\n## Using Lighthouse (knowledge base)\n\n"
        "You have access to a `library_search` tool. Use it to ground your "
        "answer in canonical sources (official docs, foundational refs) "
        "rather than relying solely on memory. Call it BEFORE drafting "
        "your final answer for any question that touches a specific "
        "framework, methodology, or industry standard.\n\n"
        "Guidelines:\n"
        "- Issue 1-3 focused queries (3-8 words each) — don't echo the "
        "user's full question.\n"
        "- After receiving results, ground specific claims with a brief "
        "(\"per Lighthouse:\") inline citation in your final answer.\n"
        "- If Lighthouse returns nothing useful, say so and answer from "
        "memory rather than fabricating a citation."
    )


_CACHE_EPHEMERAL = {"type": "ephemeral"}


async def run_no_tools(
    client, role_prompt: str, task: dict[str, str], model: str
) -> dict[str, Any]:
    """Just send the task as a user message; no tools."""
    resp = await client.messages.create(
        model=model,
        max_tokens=1500,
        temperature=0.0,
        system=[{"type": "text", "text": role_prompt, "cache_control": _CACHE_EPHEMERAL}],
        messages=[
            {
                "role": "user",
                "content": f"# {task['title']}\n\n{task['description']}",
            }
        ],
    )
    text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
    return {
        "answer": text.strip(),
        "tool_calls": 0,
        "usage": {
            "input_tokens": resp.usage.input_tokens,
            "output_tokens": resp.usage.output_tokens,
            "cache_read_input_tokens": getattr(resp.usage, "cache_read_input_tokens", 0),
            "cache_creation_input_tokens": getattr(
                resp.usage, "cache_creation_input_tokens", 0
            ),
        },
    }


async def run_with_lighthouse(
    client,
    role_prompt: str,
    task: dict[str, str],
    model: str,
    graph,
) -> dict[str, Any]:
    """Tool-use loop: model may call library_search 0-N times before
    settling on an answer. Caps at 8 iterations to keep runaway
    queries bounded."""
    system_text = role_prompt + lighthouse_instructions()
    messages: list[dict[str, Any]] = [
        {
            "role": "user",
            "content": f"# {task['title']}\n\n{task['description']}",
        }
    ]
    tool_calls = 0
    last_usage: dict[str, int] = {}

    for _ in range(8):
        resp = await client.messages.create(
            model=model,
            max_tokens=1500,
            temperature=0.0,
            system=[
                {"type": "text", "text": system_text, "cache_control": _CACHE_EPHEMERAL}
            ],
            tools=[LIBRARY_SEARCH_TOOL],
            messages=messages,
        )
        last_usage = {
            "input_tokens": resp.usage.input_tokens,
            "output_tokens": resp.usage.output_tokens,
            "cache_read_input_tokens": getattr(resp.usage, "cache_read_input_tokens", 0),
            "cache_creation_input_tokens": getattr(
                resp.usage, "cache_creation_input_tokens", 0
            ),
        }
        # Look for tool_use blocks; if none, we're done — emit text.
        tool_uses = [b for b in resp.content if getattr(b, "type", "") == "tool_use"]
        if not tool_uses:
            text = "".join(
                b.text for b in resp.content if getattr(b, "type", "") == "text"
            )
            return {
                "answer": text.strip(),
                "tool_calls": tool_calls,
                "usage": last_usage,
            }
        # Run each tool_use serially, append results.
        assistant_blocks = [
            {
                "type": "tool_use",
                "id": b.id,
                "name": b.name,
                "input": b.input,
            }
            for b in tool_uses
        ]
        # Also include any text the model emitted before the tool call.
        for b in resp.content:
            if getattr(b, "type", "") == "text" and b.text.strip():
                assistant_blocks.insert(
                    0,
                    {"type": "text", "text": b.text},
                )
        messages.append({"role": "assistant", "content": assistant_blocks})

        tool_results: list[dict[str, Any]] = []
        for b in tool_uses:
            tool_calls += 1
            query = b.input.get("query", "")
            top_k = int(b.input.get("top_k", 5))
            try:
                hits = await graph.search(query, top_k=top_k)
                payload = "\n".join(
                    f"- {h.summary}" for h in hits[:top_k]
                ) or "(no hits)"
            except Exception as exc:  # noqa: BLE001
                payload = f"(library_search errored: {exc})"
            tool_results.append(
                {"type": "tool_result", "tool_use_id": b.id, "content": payload}
            )
        messages.append({"role": "user", "content": tool_results})

    # Loop exhausted — return whatever last text we have.
    return {
        "answer": "(tool-use loop exhausted without final answer)",
        "tool_calls": tool_calls,
        "usage": last_usage,
    }


# ============================================================
# Judge
# ============================================================


JUDGE_RUBRIC = """\
You compare two answers to the same task. For each answer score on
four axes 0-10:

1. specificity (Concrete details / numbers / names vs hand-wavy)
2. citation    (References to sources / standards / canonical names)
3. actionability (Could a junior agent execute on this directly?)
4. accuracy    (Factually correct, no fabrications)

Then declare a winner. If they're equivalent quality, return "tie".

Reply strict JSON only, no prose:

{
  "a": {"specificity": 0-10, "citation": 0-10, "actionability": 0-10, "accuracy": 0-10, "total": 0-40},
  "b": {"specificity": 0-10, "citation": 0-10, "actionability": 0-10, "accuracy": 0-10, "total": 0-40},
  "winner": "a" | "b" | "tie",
  "rationale": "<= 30 words"
}
"""


async def judge_pair(
    client,
    task: dict[str, str],
    answer_a: str,
    answer_b: str,
    model: str = "claude-sonnet-4-6",
) -> dict[str, Any]:
    resp = await client.messages.create(
        model=model,
        max_tokens=400,
        temperature=0.0,
        system=[{"type": "text", "text": JUDGE_RUBRIC, "cache_control": _CACHE_EPHEMERAL}],
        messages=[
            {
                "role": "user",
                "content": (
                    f"## Task ({task['role']})\n{task['title']}\n\n"
                    f"{task['description']}\n\n"
                    f"## Answer A (no-lighthouse)\n{answer_a[:4000]}\n\n"
                    f"## Answer B (with-lighthouse)\n{answer_b[:4000]}"
                ),
            }
        ],
    )
    text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\n|\n```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # Salvage: find first { ... } block
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if m:
            try:
                return json.loads(m.group(0))
            except Exception:
                pass
        return {"winner": "tie", "rationale": "judge unparseable", "_raw": text}


# ============================================================
# Main
# ============================================================


async def main() -> int:
    import logging
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s | %(message)s")

    from anthropic import AsyncAnthropic

    from lighthouse.core.config import get_settings
    from lighthouse.core.graph import KnowledgeGraph

    settings = get_settings()
    if not settings.anthropic_api_key:
        print("ANTHROPIC_API_KEY missing — abort", file=sys.stderr)
        return 1

    client = AsyncAnthropic(api_key=settings.anthropic_api_key)
    model = settings.lighthouse_model_main  # claude-sonnet-4-6

    graph = KnowledgeGraph(settings)
    await graph.initialize()

    run_id = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    out_dir = Path("tools/eval/agent_bench") / run_id
    out_dir.mkdir(parents=True, exist_ok=True)

    results: list[dict[str, Any]] = []

    # Cache role prompts so we don't re-read 20 times.
    role_prompts: dict[str, str] = {}

    for i, task in enumerate(TASKS, 1):
        role = task["role"]
        if role not in role_prompts:
            role_prompts[role] = load_role_prompt(role)
        role_prompt = role_prompts[role]

        print(
            f"[{i:2d}/{len(TASKS)}] {role:<16s} | {task['title']}",
            flush=True,
        )

        # A — no-tool baseline
        a = await run_no_tools(client, role_prompt, task, model)
        # B — with lighthouse
        b = await run_with_lighthouse(client, role_prompt, task, model, graph)

        v = await judge_pair(client, task, a["answer"], b["answer"], model=model)

        rec = {
            "task": task,
            "a": a,
            "b": b,
            "verdict": v,
        }
        (out_dir / f"task-{i:02d}.json").write_text(json.dumps(rec, indent=2))
        results.append(rec)

        a_total = v.get("a", {}).get("total", 0)
        b_total = v.get("b", {}).get("total", 0)
        winner = v.get("winner", "?")
        print(
            f"      A={a_total:>2}/40  B={b_total:>2}/40  → {winner}"
            f"  (B used tool {b['tool_calls']}x)",
            flush=True,
        )

    await graph.close()

    # ============================================================
    # Aggregate report
    # ============================================================
    print()
    print("=" * 72)
    print(f"  Lighthouse agent bench — {len(results)} tasks, model={model}")
    print(f"  Run id: {run_id}")
    print("=" * 72)
    win_b = sum(1 for r in results if r["verdict"].get("winner") == "b")
    win_a = sum(1 for r in results if r["verdict"].get("winner") == "a")
    ties = sum(1 for r in results if r["verdict"].get("winner") == "tie")
    sum_a = sum(r["verdict"].get("a", {}).get("total", 0) for r in results)
    sum_b = sum(r["verdict"].get("b", {}).get("total", 0) for r in results)
    n = max(1, len(results))
    print(
        f"  Winners: B (lighthouse) {win_b:>2}  ·  A (no-lighthouse) {win_a:>2}  ·  tie {ties:>2}"
    )
    print(f"  Mean score: A {sum_a/n:.1f}/40   vs   B {sum_b/n:.1f}/40")
    by_role: dict[str, list[int]] = {}
    for r in results:
        by_role.setdefault(r["task"]["role"], []).append(
            r["verdict"].get("b", {}).get("total", 0)
            - r["verdict"].get("a", {}).get("total", 0)
        )
    print("  Per-role delta (B - A, mean):")
    for role, deltas in sorted(by_role.items()):
        sign = "+" if sum(deltas) / len(deltas) >= 0 else ""
        print(f"    {role:<16s} {sign}{sum(deltas)/len(deltas):.1f}")
    print()
    print(f"  Detailed per-task results in {out_dir}/")
    print("=" * 72)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
