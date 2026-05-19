FROM python:3.12-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src/ ./src/

RUN pip install --upgrade pip && pip install -e ".[importers-all]"

EXPOSE 8000

CMD ["uvicorn", "lighthouse.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
