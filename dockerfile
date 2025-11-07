FROM python:3.10-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    curl \
    cron \
    && rm -rf /var/lib/apt/lists/*

# Install UV globally
RUN pip install uv

# Copy project files
COPY pyproject.toml .
COPY uv.lock .

# Install dependencies directly (no venv)
RUN uv pip install -r /dev/stdin < <(uv pip compile pyproject.toml)

# Or simpler - just install from pyproject.toml
RUN pip install requests beautifulsoup4 lxml

# Copy application file
COPY ufuture-scraper.py .

# Create volume directories
RUN mkdir -p /app/data /app/logs

# Copy crontab
COPY crontab /etc/cron.d/ufuture-crontab
RUN chmod 0644 /etc/cron.d/ufuture-crontab && \
    crontab /etc/cron.d/ufuture-crontab

# Health check
HEALTHCHECK --interval=5m --timeout=10s --start-period=30s --retries=3 \
    CMD python -c "import sqlite3; sqlite3.connect('/app/data/ufuture_forums.db').execute('SELECT 1')" || exit 1

# Run cron daemon
CMD ["cron", "-f"]
