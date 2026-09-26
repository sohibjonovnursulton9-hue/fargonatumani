FROM python:3.11-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Copy dependency definition
COPY pyproject.toml .

# Install Python dependencies
RUN pip install --no-cache-dir .[prod]

# Copy application code
COPY . .

# Create non-root user
RUN useradd --create-home appuser
USER appuser

# Default command (overridden in docker-compose.yml)
CMD ["python", "-m", "app.main"]
