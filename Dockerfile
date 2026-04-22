# ── 构建阶段 ──────────────────────────────────────────────────────────────────
FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim AS builder

WORKDIR /app
COPY pyproject.toml .
RUN uv sync --no-dev --no-install-project

# ── 运行阶段：直接复用builder的Python，不另外拉镜像 ───────────────────────────
FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim

WORKDIR /app

COPY --from=builder /app/.venv /app/.venv
COPY src/ ./src/

ENV PATH="/app/.venv/bin:$PATH"

RUN mkdir -p /app/logs

CMD ["python", "src/main.py"]
