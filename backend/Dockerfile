# FormaAI Backend Production Dockerfile
FROM python:3.12-slim

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Install uv for fast, deterministic dependency resolution
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app

# Copy dependency manifests
COPY pyproject.toml uv.lock ./

# Install locked dependencies
RUN uv sync --frozen --no-dev

# Copy application source code and migrations
COPY src/ ./src/
COPY migrations/ ./migrations/
COPY alembic.ini ./
COPY scripts/ ./scripts/

# Create persistent storage mount directory
RUN mkdir -p /app/data/storage

ENV PATH="/app/.venv/bin:$PATH"
ENV FORMAAI_APP_ENV="production"
ENV FORMAAI_HOST="0.0.0.0"
ENV FORMAAI_PORT=8000
ENV FORMAAI_RELOAD="false"

EXPOSE 8000

# Run entry point
CMD ["uv", "run", "python", "-m", "formaai"]
