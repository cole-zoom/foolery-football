# Build from the repo root: `docker build -f services/api/Dockerfile -t <tag> .`
# The api package has path deps on stats-loader, decision-engine, ffdm-app,
# so the build context must include all four service directories.

FROM python:3.13-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

RUN pip install --no-cache-dir uv==0.5.11

WORKDIR /app

COPY services/api/pyproject.toml services/api/uv.lock services/api/
COPY services/app/pyproject.toml services/app/
COPY services/decision-engine/pyproject.toml services/decision-engine/
COPY services/stats-loader/pyproject.toml services/stats-loader/

COPY services/api/src services/api/src
COPY services/app/src services/app/src
COPY services/decision-engine/src services/decision-engine/src
COPY services/stats-loader/src services/stats-loader/src

WORKDIR /app/services/api
RUN uv sync --frozen --no-dev

FROM python:3.13-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/app/services/api/.venv/bin:$PATH" \
    PORT=8080

WORKDIR /app
COPY --from=builder /app /app

EXPOSE 8080

CMD ["sh", "-c", "uvicorn api.main:app --host 0.0.0.0 --port ${PORT}"]
