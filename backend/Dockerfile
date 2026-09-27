# syntax=docker/dockerfile:1
# Build context is the repository root: docker build -f backend/Dockerfile .
FROM python:3.12.8-slim-bookworm

COPY --from=ghcr.io/astral-sh/uv:0.12.5 /uv /usr/local/bin/uv

ENV PATH=/app/.venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app

COPY backend/pyproject.toml backend/uv.lock ./
RUN uv sync --frozen --no-install-project

COPY backend/app ./app
COPY backend/migrations ./migrations
COPY backend/alembic.ini ./alembic.ini
RUN uv sync --frozen

RUN useradd --uid 10001 --create-home app
USER app

CMD ["python", "-m", "app.worker", "run"]
