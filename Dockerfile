FROM python:3.10-slim

# uv, pinned to the version that wrote uv.lock
COPY --from=ghcr.io/astral-sh/uv:0.9.7 /uv /bin/uv

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install exactly the versions in uv.lock (--frozen fails the build if the lock is out of date)
COPY pyproject.toml uv.lock ./
RUN UV_PYTHON_DOWNLOADS=never uv sync --frozen --no-dev --no-install-project
ENV PATH="/app/.venv/bin:$PATH"

# Copy application file
COPY ufuture-scraper.py .

# Create volume directories
RUN mkdir -p /app/data /app/logs

# Create scheduler script
COPY scheduler.py .
COPY telegram_interactive_bot.py .
COPY supervisor.py .

# Health check
HEALTHCHECK --interval=5m --timeout=10s --start-period=30s --retries=3 CMD python -c "import os, socket, sqlite3; socket.gethostbyname('ufuture.uitm.edu.my'); sqlite3.connect(os.environ['DB_PATH']).execute('SELECT 1')" || exit 1

# Run scheduler and interactive Telegram bot under a small supervisor
CMD ["python", "/app/supervisor.py"]
