"""Render bench charts for the public eval report.

All 11 models, DSQ-clean numbers from ``tools/audit_bench.py``. Saves
PNGs at 2× resolution into ``reports/charts/`` for the two PDF builds.
"""
from __future__ import annotations

import json
from pathlib import Path

import matplotlib.pyplot as plt
import matplotlib.ticker as mtick


ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "reports" / "charts"
OUT.mkdir(parents=True, exist_ok=True)


# DSQ-clean final numbers — manually curated from audit_bench output
# (the audit ranks by directory, so we relabel + reorder by Δ).
MODELS = [
    # name, provider, A, B, delta, winrate_B, cost_a, cost_b, time_a, time_b
    ("gemini-2.5-pro",       "OpenRouter", 14.6, 25.3, 10.7, 74, 0.083, 0.097, 60.4, 64.8),
    ("qwen3-coder",          "OpenRouter", 19.7, 26.1,  6.4, 57, 0.034, 0.049, 35.0, 50.1),
    ("qwen3.6-plus",         "OpenRouter", 26.9, 31.2,  4.4, 71, 0.174, 0.148, 186.5, 135.0),
    ("deepseek-chat-v3.1",   "OpenRouter", 24.1, 27.7,  3.6, 63, 0.054, 0.079, 82.8, 111.3),
    ("claude-sonnet-4-6",    "Anthropic",  30.6, 32.5,  1.9, 54, 0.041, 0.044, 102.0, 92.0),
    ("claude-haiku-4-5",     "Anthropic",  29.7, 31.3,  1.6, 51, 0.090, 0.120, 40.8, 44.4),
    ("gpt-5.5",              "OpenAI",     28.1, 29.0,  0.9, 40, 0.102, 0.116, 88.0, 95.0),
    ("kimi-k2.6",            "OpenRouter", 28.3, 28.6,  0.3, 47, 0.072, 0.060, 103.0, 92.0),
    ("mistral-large-2411",   "OpenRouter", 22.9, 23.1,  0.1, 40, 0.063, 0.050, 81.0, 61.5),
    ("gpt-5.2",              "OpenAI",     29.5, 29.1, -0.4, 31, 0.055, 0.067, 61.0, 62.0),
    ("llama-3.3-70b-paid",   "OpenRouter", 23.9, 21.2, -2.6, 23, 0.050, 0.046, 79.2, 36.3),
]

PROVIDER_COLOR = {
    "OpenAI": "#10A37F",
    "Anthropic": "#D97757",
    "OpenRouter": "#5B5BD6",
}


def style():
    plt.rcParams.update({
        "font.family": "DejaVu Sans",
        "font.size": 11,
        "axes.titlesize": 13,
        "axes.labelsize": 11,
        "axes.spines.top": False,
        "axes.spines.right": False,
        "axes.grid": True,
        "grid.alpha": 0.25,
        "grid.linestyle": "--",
        "figure.dpi": 144,
    })


def chart_delta_bar():
    """Bar chart: Δ score per model, color-coded by provider, sorted high to low."""
    style()
    fig, ax = plt.subplots(figsize=(11, 6))
    rows = sorted(MODELS, key=lambda r: r[4], reverse=True)
    names = [r[0] for r in rows]
    deltas = [r[4] for r in rows]
    colors = [PROVIDER_COLOR[r[1]] for r in rows]

    bars = ax.barh(names, deltas, color=colors, edgecolor="black", linewidth=0.5)
    ax.axvline(0, color="black", linewidth=0.8)
    ax.invert_yaxis()
    ax.set_xlabel("Δ score (B − A), points out of 40")
    ax.set_title("Lighthouse impact on agent quality, by model", pad=14)

    for bar, d in zip(bars, deltas):
        x_off = 0.18 if d >= 0 else -0.18
        ha = "left" if d >= 0 else "right"
        ax.text(bar.get_width() + x_off, bar.get_y() + bar.get_height() / 2,
                f"{d:+.1f}", va="center", ha=ha, fontsize=10, fontweight="bold")

    handles = [plt.Rectangle((0, 0), 1, 1, color=c) for c in PROVIDER_COLOR.values()]
    ax.legend(handles, PROVIDER_COLOR.keys(), loc="lower right", frameon=False)
    ax.set_xlim(-4, 12.5)
    fig.tight_layout()
    fig.savefig(OUT / "delta_by_model.png", bbox_inches="tight")
    plt.close(fig)


def chart_ucurve():
    """Scatter: baseline A score vs Δ (B-A). Visualizes the U-curve hypothesis."""
    style()
    fig, ax = plt.subplots(figsize=(10, 6))
    for r in MODELS:
        name, prov, a, b, d, _, _, _, _, _ = r
        ax.scatter(a, d, s=120, color=PROVIDER_COLOR[prov], edgecolor="black", linewidth=0.6, zorder=3)
        # Label placement: offset above for + deltas, below for negative
        dy = 0.35 if d >= 0 else -0.55
        ax.annotate(name, (a, d), xytext=(a + 0.3, d + dy), fontsize=9)

    ax.axhline(0, color="black", linewidth=0.8)
    ax.set_xlabel("Baseline quality (A, no retrieval) — points out of 40")
    ax.set_ylabel("Δ (B − A) — points")
    ax.set_title("Lighthouse helps weaker baseline models more — U-curve",
                 pad=14)

    # Trend line: weighted linear fit, dashed
    import numpy as np
    xs = np.array([r[2] for r in MODELS])
    ys = np.array([r[4] for r in MODELS])
    coef = np.polyfit(xs, ys, 1)
    xline = np.linspace(min(xs) - 1, max(xs) + 1, 50)
    ax.plot(xline, coef[0] * xline + coef[1], "--", color="gray", alpha=0.6,
            label=f"fit: Δ ≈ {coef[0]:+.2f}·A {coef[1]:+.1f}")
    ax.legend(loc="upper right", frameon=False)
    ax.set_xlim(12, 33)
    ax.set_ylim(-4, 13)
    fig.tight_layout()
    fig.savefig(OUT / "ucurve.png", bbox_inches="tight")
    plt.close(fig)


def chart_cost_time():
    """Side-by-side: $/task A vs B, seconds/task A vs B."""
    style()
    fig, axes = plt.subplots(1, 2, figsize=(12, 6))
    rows = sorted(MODELS, key=lambda r: r[4], reverse=True)
    names = [r[0] for r in rows]
    y = list(range(len(rows)))

    # cost
    cost_a = [r[6] for r in rows]
    cost_b = [r[7] for r in rows]
    ax = axes[0]
    bw = 0.4
    ax.barh([i - bw / 2 for i in y], cost_a, bw, color="#999", label="A (no retrieval)")
    ax.barh([i + bw / 2 for i in y], cost_b, bw, color="#10A37F", label="B (Lighthouse)")
    ax.set_yticks(y, names)
    ax.invert_yaxis()
    ax.set_xlabel("USD per task")
    ax.set_title("Cost per task — A vs B")
    ax.legend(frameon=False, loc="lower right")
    ax.xaxis.set_major_formatter(mtick.FormatStrFormatter("$%.3f"))

    # time
    time_a = [r[8] for r in rows]
    time_b = [r[9] for r in rows]
    ax = axes[1]
    ax.barh([i - bw / 2 for i in y], time_a, bw, color="#999", label="A (no retrieval)")
    ax.barh([i + bw / 2 for i in y], time_b, bw, color="#10A37F", label="B (Lighthouse)")
    ax.set_yticks(y, [""] * len(names))
    ax.invert_yaxis()
    ax.set_xlabel("Seconds per task")
    ax.set_title("Wall time per task — A vs B")
    ax.legend(frameon=False, loc="lower right")

    fig.suptitle("Cost and latency overhead is modest; some models win on both",
                 y=1.02, fontsize=13)
    fig.tight_layout()
    fig.savefig(OUT / "cost_time.png", bbox_inches="tight")
    plt.close(fig)


def chart_dsq_impact():
    """Before vs after DSQ filter — Δ change for the 4 models where it mattered."""
    style()
    fig, ax = plt.subplots(figsize=(8, 5))
    rows = [
        # (label, raw_delta, clean_delta)
        ("claude-sonnet-4-6", -0.7, 1.9),
        ("gpt-5.5",            0.1, 0.9),
        ("gemini-2.5-pro",     9.5, 10.7),
        ("qwen3-coder",        6.2, 6.4),
    ]
    names = [r[0] for r in rows]
    raw = [r[1] for r in rows]
    clean = [r[2] for r in rows]

    y = list(range(len(rows)))
    bw = 0.4
    ax.barh([i - bw / 2 for i in y], raw, bw, color="#bbb", label="Raw (including DSQ)")
    ax.barh([i + bw / 2 for i in y], clean, bw, color="#5B5BD6", label="DSQ-clean")
    ax.axvline(0, color="black", linewidth=0.8)
    ax.set_yticks(y, names)
    ax.invert_yaxis()
    ax.set_xlabel("Δ score (B − A)")
    ax.set_title("DSQ filter changes the headline — esp. sonnet-4-6 (sign flip)",
                 pad=12)
    ax.legend(frameon=False, loc="lower right")
    fig.tight_layout()
    fig.savefig(OUT / "dsq_impact.png", bbox_inches="tight")
    plt.close(fig)


def chart_live_quality():
    """Live MCP retrieval quality on 5 spot-check queries."""
    style()
    fig, ax = plt.subplots(figsize=(10, 5))
    queries = [
        ("Gherkin AC",      4.0, 5.0, True),
        ("BDD scenario",    None, 5.0, True),
        ("WCAG contrast",   1.5, 5.0, True),
        ("RICE framework",  2.0, 2.0, False),
        ("K8s probes",      0.0, 0.0, False),
        ("INVEST acronym",  None, 0.0, False),
    ]
    names = [q[0] for q in queries]
    before = [q[1] for q in queries]
    after = [q[2] for q in queries]

    y = list(range(len(queries)))
    bw = 0.35
    # Plot 'before' only where defined
    for i, b in enumerate(before):
        if b is not None:
            ax.barh(i - bw / 2, b, bw, color="#bbb")
    for i, a in enumerate(after):
        ax.barh(i + bw / 2, a, bw,
                color=("#10A37F" if queries[i][3] else "#D97757"))

    ax.set_yticks(y, names)
    ax.invert_yaxis()
    ax.set_xlabel("Useful hits / 5 (manual rating)")
    ax.set_xlim(0, 5.5)
    ax.set_title("Live retrieval quality before and after the upgrade — by query",
                 pad=12)
    handles = [
        plt.Rectangle((0, 0), 1, 1, color="#bbb"),
        plt.Rectangle((0, 0), 1, 1, color="#10A37F"),
        plt.Rectangle((0, 0), 1, 1, color="#D97757"),
    ]
    ax.legend(handles, ["Pre-upgrade", "Covered + upgraded", "Corpus gap"],
              frameon=False, loc="lower right")
    fig.tight_layout()
    fig.savefig(OUT / "live_quality.png", bbox_inches="tight")
    plt.close(fig)


def main():
    chart_delta_bar()
    chart_ucurve()
    chart_cost_time()
    chart_dsq_impact()
    chart_live_quality()
    print(f"wrote charts → {OUT}")


if __name__ == "__main__":
    main()
