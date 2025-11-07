FROM python:3.10-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install the packages your script needs
RUN pip install requests beautifulsoup4 lxml schedule

# Copy application file
COPY ufuture-scraper.py .

# Create volume directories
RUN mkdir -p /app/data /app/logs

# Create scheduler script
COPY scheduler.py .

# Health check
HEALTHCHECK --interval=5m --timeout=10s --start-period=30s --retries=3 CMD python -c "import sqlite3; sqlite3.connect('/app/data/ufuture_forums.db').execute('SELECT 1')" || exit 1

# Run scheduler
CMD ["python", "/app/scheduler.py"]
