# syntax=docker/dockerfile:1
# TERRA-X API and worker share this image; the worker overrides CMD in compose.
FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PATH="/app/.venv/bin:$PATH"

# uv is used only to resolve the locked environment at build time.
COPY --from=ghcr.io/astral-sh/uv:0.8 /uv /usr/local/bin/uv

WORKDIR /app

# Install dependencies from the lock file before copying source for layer caching.
COPY apps/api/pyproject.toml apps/api/uv.lock ./
RUN uv sync --frozen --no-dev

COPY apps/api/ ./

# Writable scientific cache and report roots; compose mounts a volume over /data.
RUN mkdir -p /data/cache /data/reports

EXPOSE 8000

# Migrations are applied by the API service command in compose, before uvicorn starts.
CMD ["uvicorn", "terrax.main:app", "--host", "0.0.0.0", "--port", "8000"]
