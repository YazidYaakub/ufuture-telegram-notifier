FROM ghcr.io/astral-sh/uv:latest as builder

WORKDIR /app

# Copy project files
COPY pyproject.toml .
COPY uv.lock .

# Build virtual environment
RUN uv venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
RUN uv sync --frozen

# Final stage
FROM python:3.10-slim

WORKDIR /app

# Install system dependencies including cron
RUN apt-get update && apt-get install -y \
    curl \
    cron \
    && rm -rf /var/lib/apt/lists/*

# Copy virtual environment from builder
COPY --from=builder /opt/venv /opt/venv

# Set PATH to use venv
ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Copy application file
COPY ufuture-scraper.py .

# Create volume directories
RUN mkdir -p /app/data /app/logs

# Copy crontab
COPY crontab /etc/cron.d/ufuture-crontab

# Make crontab executable
RUN chmod 0644 /etc/cron.d/ufuture-crontab && \
    crontab /etc/cron.d/ufuture-crontab

# Health check
HEALTHCHECK --interval=5m --timeout=10s --start-period=30s --retries=3 \
    CMD python -c "import sqlite3; sqlite3.connect('/app/data/ufuture_forums.db').execute('SELECT 1')" || exit 1

# Run cron daemon in foreground
CMD ["cron", "-f"]
