# Lighthouse Engine — production image.
#
# Base deps only (~200 MB). Importer connectors that need extra
# llama-hub readers (Notion, Slack, S3, …) are gated behind optional
# extras — see [project.optional-dependencies] in pyproject.toml.
# To bake them in, change the install line to:
#
#     pip install --no-cache-dir "/wheels/$(ls /wheels)[importers-all]"
#
# or pick individual groups, e.g. ".[importers-notion,importers-s3]".

# ── Stage 1: build ──────────────────────────────────────────────────
FROM python:3.12-slim AS builder

WORKDIR /build

# Build the wheel first so the runtime stage carries no build tooling.
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN pip install --no-cache-dir build && python -m build --wheel -o /wheels

# Install into a self-contained venv we can copy wholesale.
RUN python -m venv /opt/venv \
    && /opt/venv/bin/pip install --no-cache-dir /wheels/*.whl

# ── Stage 2: runtime ────────────────────────────────────────────────
FROM python:3.12-slim

# curl only for the container healthcheck.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

RUN useradd --create-home --uid 1000 lighthouse
USER lighthouse
WORKDIR /home/lighthouse

COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Writable working dirs for proposal store / runner state defaults.
RUN mkdir -p data/proposals

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD curl -fsS http://localhost:8000/health || exit 1

# Migrations run automatically on API startup.
CMD ["uvicorn", "lighthouse.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
