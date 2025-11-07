FROM python:3.10-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    curl \
    cron \
    && rm -rf /var/lib/apt/lists/*

# Install the packages your script needs
RUN pip install requests beautifulsoup4 lxml

# Copy application file
COPY ufuture-scraper.py .

# Create volume directories
RUN mkdir -p /app/data /app/logs

# Copy crontab
COPY crontab /etc/cron.d/ufuture-crontab
RUN chmod 0644 /etc/cron.d/ufuture-crontab

# Create log file with proper permissions
RUN touch /app/logs/cron.log && chmod 666 /app/logs/cron.log

# Health check
HEALTHCHECK --interval=5m --timeout=10s --start-period=30s --retries=3 \
    CMD python -c "import sqlite3; sqlite3.connect('/app/data/ufuture_forums.db').execute('SELECT 1')" || exit 1

# Start cron - remove any stale PID file first
CMD rm -f /var/run/crond.pid /var/run/cron.pid && /usr/sbin/cron -f
