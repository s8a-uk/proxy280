# syntax=docker/dockerfile:1
ARG PYTHON_VERSION=3.15

FROM ghcr.io/astral-sh/uv:python${PYTHON_VERSION}-trixie-slim AS base
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=0
WORKDIR /app

# 開発用: ソースは compose で /app にマウントし、venv はマウント外に置く
FROM base AS dev
ENV UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH="/opt/venv/bin:$PATH"
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-install-project

FROM base AS builder
COPY pyproject.toml uv.lock ./
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-editable

FROM python:${PYTHON_VERSION}-slim-trixie AS runtime
RUN useradd --system --no-create-home app
COPY --from=builder /app/.venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PROXY280_ALLOWLIST_PATH=/config/allowlist.txt
USER app
EXPOSE 28000
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:28000/healthz', timeout=2)"]
CMD ["proxy280"]
