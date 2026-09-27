FROM python:3.11-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Copy dependency definition
COPY pyproject.toml .
COPY app ./app

# Install Python dependencies (prod includes asyncpg, gunicorn)
RUN pip install --no-cache-dir .[prod]

# Copy migrations and the remaining runtime files. Local databases and secrets
# are excluded by .dockerignore.
COPY alembic ./alembic
COPY alembic.ini .

# The application only needs read access to its code and migrations.
RUN useradd --create-home --shell /usr/sbin/nologin appuser \
    && chown -R appuser:appuser /app
USER appuser

# app.server runs migrations before starting the web and bot processes.
CMD ["python", "-m", "app.server"]
