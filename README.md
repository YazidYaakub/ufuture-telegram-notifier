# UiTM Forum Scraper + Telegram Notifier

Automatically scrape UiTM forums, detect updates, and receive Telegram notifications about new posts and topics.

## Features

- 🔍 **Automatic Forum Scraping** - Scrapes all your UiTM course forums
- 🆕 **Change Detection** - Detects new posts and topics since last run
- 📱 **Telegram Notifications** - Get instant alerts with topic links and reply excerpts
- 💾 **SQLite Database** - Stores forum state for comparison
- 🐳 **Docker Ready** - Optimized Dockerfile for easy deployment
- ⚡ **Fast Builds** - Uses UV package manager (10-100x faster than pip)
- 🔐 **Secure** - Environment variables for credentials (no hardcoded secrets)
- 🏠 **TrueNAS Compatible** - Ready for TrueNAS Scale deployment
- 🧭 **DNS-aware Runtime** - Detects container DNS failures and exits non-zero instead of silently succeeding

## What It Does

1. Logs into UiTM forum system with your credentials
2. Scrapes all course forums you're enrolled in
3. Extracts week/topic information (posts, topics, latest activity)
4. Optionally opens topic pages to capture topic titles, reply counts, reply excerpts, and open/reply links
5. Stores data in local SQLite database
6. Compares with previous run to detect changes
7. Sends Telegram notification if new posts/topics found
8. Saves current state for next comparison

## Quick Start

### Local Testing

#### Prerequisites
- Python 3.10+
- [UV package manager](https://docs.astral.sh/uv/)
- [direnv](https://direnv.net/) (optional but recommended)

#### Installation

```bash
# Clone repository
git clone https://github.com/YOUR_USERNAME/ufuture-forum-scraper.git
cd ufuture-forum-scraper

# Install dependencies with UV
uv sync
```

#### Configuration

Create `.envrc` in project root:

```bash
export UFUTURE_USERNAME=your_student_id
export UFUTURE_PASSWORD=your_password
export TELEGRAM_BOT_TOKEN=your_bot_token
export TELEGRAM_CHAT_ID=your_chat_id
export DB_PATH=./data/ufuture_forums.db
export SNAPSHOT_PATH=./data/telegram_last_snapshot.json
export LOG_DIR=./logs
```

Then:
```bash
direnv allow
```

#### Run

```bash
uv run python ufuture-scraper.py
```

Output:
```
================================================================================
UiTM Forum Scraper + Telegram Notifier
Started: 07/11/2025 05:58:09
================================================================================
Logged in successfully
Found 10 course cards
Processed 5 unique courses
Found 5 forums
Saved to database: ./data/ufuture_forums.db
Scrape completed: 5 courses, 75 weeks
Detected 1 update(s)!
ISP610: 1 new post(s), 0 new topic(s)
Telegram notification sent
Completed: 07/11/2025 05:58:21
```

## Docker Deployment

### Build Docker Image

```bash
docker build -f Dockerfile -t ufuture-forum-scraper:latest .
```

### Run Docker Container

```bash
docker run -e UFUTURE_USERNAME=your_student_id \
           -e UFUTURE_PASSWORD=your_password \
           -e TELEGRAM_BOT_TOKEN=your_bot_token \
           -e TELEGRAM_CHAT_ID=your_chat_id \
           -v ufuture-data:/app/data \
           ufuture-forum-scraper:latest
```

## TrueNAS Scale Deployment

### Prerequisites
- TrueNAS Scale installed
- GitHub repository with this code
- Telegram bot token and chat ID

### Steps

1. **Go to TrueNAS UI** → Apps → Create Custom App

2. **Paste YAML configuration:**

```yaml
services:
  ufuture-forum-scraper:
    build:
      context: https://github.com/YOUR_USERNAME/ufuture-forum-scraper.git#main
      dockerfile: Dockerfile
    
    container_name: ufuture-forum-scraper
    hostname: ufuture-forum-scraper
    restart: unless-stopped

    # Optional but recommended if your LAN DNS is filtered or unreliable.
    dns:
      - 1.1.1.1
      - 8.8.8.8
    
    environment:
      - UFUTURE_USERNAME=your_student_id
      - UFUTURE_PASSWORD=your_password
      - TELEGRAM_BOT_TOKEN=your_bot_token
      - TELEGRAM_CHAT_ID=your_chat_id
      - DB_PATH=/app/data/ufuture_forums.db
      - SNAPSHOT_PATH=/app/data/telegram_last_snapshot.json
      - LOG_DIR=/app/logs
      - REQUEST_RETRIES=3
      - REQUEST_BACKOFF_SECONDS=5
      - SCRAPE_TOPIC_DETAILS=true
      - TOPIC_DETAIL_LIMIT=20
      - INCLUDE_REPLY_EXCERPTS=true
      - REPLY_EXCERPT_LENGTH=160
      - TELEGRAM_MAX_TOPICS_PER_MESSAGE=8
      - SCRAPER_TIMEOUT_SECONDS=300
      - SCRAPER_FAILURE_LIMIT=0
    
    volumes:
      - ufuture-data:/app/data
      - ufuture-logs:/app/logs

volumes:
  ufuture-data:
  ufuture-logs:

```

3. **Replace placeholders:**
   - `YOUR_USERNAME` = your GitHub username
   - `your_student_id` = your UiTM student ID
   - `your_password` = your UiTM password
   - `your_bot_token` = Telegram bot token
   - `your_chat_id` = Telegram chat ID

4. **Click Deploy**

5. **Wait 5-10 minutes** for first build

6. **Monitor logs** in TrueNAS UI to verify it's working

## Environment Variables

| Variable | Description | Example |
|----------|-------------|---------|
| `UFUTURE_USERNAME` | UiTM student ID | `your_student_id` |
| `UFUTURE_PASSWORD` | UiTM password | `your_password` |
| `TELEGRAM_BOT_TOKEN` | Telegram bot token | `123456:ABC...` |
| `TELEGRAM_CHAT_ID` | Telegram chat ID | `987654321` |
| `DB_PATH` | Database file path | `./data/ufuture_forums.db` |
| `SNAPSHOT_PATH` | State file path | `./data/telegram_last_snapshot.json` |
| `LOG_DIR` | Logs directory | `./logs` |
| `REQUEST_RETRIES` | HTTP/DNS retry attempts | `3` |
| `REQUEST_BACKOFF_SECONDS` | Base delay between retries | `5` |
| `SCRAPE_TOPIC_DETAILS` | Fetch forum topic pages and reply excerpts after week overview scrape | `true` |
| `TOPIC_DETAIL_LIMIT` | Maximum topic detail pages fetched per scraper run | `20` |
| `INCLUDE_REPLY_EXCERPTS` | Include short reply excerpts in Telegram notifications | `true` |
| `REPLY_EXCERPT_LENGTH` | Maximum stored/sent reply excerpt length | `160` |
| `TELEGRAM_MAX_TOPICS_PER_MESSAGE` | Maximum topic detail blocks included per changed week | `8` |
| `SCRAPER_TIMEOUT_SECONDS` | Scheduler subprocess timeout | `300` |
| `SCRAPER_FAILURE_LIMIT` | Exit scheduler after N failures; `0` means never exit | `0` |
| `SCRAPER_PATH` | Path used by scheduler to run scraper | container scraper path |

## Telegram Setup

Topic detail notifications include an `Open / reply` link that opens the UiTM topic page in your browser. The scraper does not post replies to UiTM; replies remain manual inside the authenticated UiTM session.

### Create Telegram Bot

1. Open Telegram and search for **@BotFather**
2. Start chat and send `/newbot`
3. Follow instructions to create your bot
4. Copy the **API token** (this is your `TELEGRAM_BOT_TOKEN`)

### Get Chat ID

1. Send message to your bot
2. Visit: `https://api.telegram.org/bot<YOUR_BOT_TOKEN>/getUpdates`
3. Look for `"chat":{"id":123456789}` - this is your `TELEGRAM_CHAT_ID`

Or use bot like **@userinfobot** to get your chat ID directly

## Project Structure

```
ufuture-forum-scraper/
├── ufuture-scraper.py                       # Main application
├── scheduler.py                             # Runs scraper every 10 minutes
├── Dockerfile                                # Docker build configuration
├── pyproject.toml                            # UV dependencies
├── uv.lock                                   # Locked dependency versions
├── .gitignore                                # Git ignore rules
├── .dockerignore                             # Docker ignore rules
├── .envrc                                    # direnv configuration (local only)
└── README.md                                 # This file
```

## Database Schema

### courses table
```sql
id              INTEGER PRIMARY KEY
course_code     TEXT UNIQUE
course_name     TEXT
forum_url       TEXT
last_updated    TIMESTAMP
```

### weeks table
```sql
id              INTEGER PRIMARY KEY
course_code     TEXT FOREIGN KEY
week_title      TEXT
week_link       TEXT
topics          TEXT
posts           TEXT
latest_user     TEXT
latest_time     TEXT
last_updated    TIMESTAMP
UNIQUE(course_code, week_title)
```

### topics table
```sql
id              INTEGER PRIMARY KEY
course_code     TEXT FOREIGN KEY
week_title      TEXT
topic_id        TEXT
topic_title     TEXT
topic_url       TEXT
description     TEXT
created_by      TEXT
created_at      TEXT
replies         INTEGER
last_activity   TEXT
status          TEXT
last_updated    TIMESTAMP
UNIQUE(course_code, week_title, topic_id)
```

### posts table
```sql
id              INTEGER PRIMARY KEY
topic_id        TEXT
post_id         TEXT
parent_post_id  TEXT
author          TEXT
content_excerpt TEXT
posted_time     TEXT
last_updated    TIMESTAMP
UNIQUE(topic_id, post_id)
```

## How It Works

### First Run
- Scrapes all forums
- Stores in database
- Saves state snapshot
- **No notification** (nothing to compare against)

### Subsequent Runs
- Scrapes forums again
- Compares with previous state
- Detects: new posts, new topics, latest user activity
- Adds topic titles, open/reply links, and recent reply excerpts when topic detail scraping is enabled
- **Sends Telegram notification** if changes found
- Updates state snapshot

### State Comparison
The snapshot file uses schema version 2 and stores `weeks`, `topics`, and `posts`. Older week-only snapshot lists are still accepted and upgraded on the next successful run. On the first upgraded run, topic details enrich the notification; exact per-post diffs become available after one version-2 snapshot exists.

## Security Notes

### ✅ Secure
- Credentials never stored in code
- Environment variables only
- `.env` and `.envrc` protected by `.gitignore`
- No credentials in Docker image
- No credentials in GitHub

### ❌ Never Do
- Hardcode credentials in code
- Commit `.env` files
- Share `.envrc` files
- Put credentials in YAML
- Commit credentials to GitHub

### 🔐 Best Practices
- Use different credentials for different environments
- Rotate credentials regularly
- Use unique passwords
- Don't share `.env` files
- Keep bot tokens private

## Troubleshooting

### "Read-only file system" error
**Solution:** Make sure you have write permissions to `./data` and `./logs` folders

### "Logged in failed" error
**Solution:** Check your UiTM username and password are correct

### "DNS lookup failed" or "Temporary failure in name resolution"
**Solution:** This is a container or TrueNAS DNS problem, not a forum parser problem.

Check DNS from TrueNAS and from inside the container. If public DNS works but your LAN DNS does not, add explicit DNS servers to the app YAML:

```yaml
services:
  ufuture-forum-scraper:
    dns:
      - 1.1.1.1
      - 8.8.8.8
```

If you use Technitium DNS, check its query logs, allowlist the UiTM domain if blocked, confirm upstream forwarders work, and make sure TrueNAS/container subnets are allowed to query it.

### "Telegram notification failed" error
**Solution:** Verify bot token and chat ID are correct

### Container says scraper completed with non-zero code
**Solution:** Read the scraper log immediately above that line. Exit code `75` means a temporary network or DNS failure. Exit code `2` means required configuration is missing.

### No database file created
**Solution:** Check `DB_PATH` in environment variables is correct

### Docker build takes too long
**Solution:** First build takes 5-10 minutes. Subsequent builds are faster.

### TrueNAS says "Build failed"
**Solution:** 
- Check GitHub username in YAML is correct
- Check repository is public or you have access
- Check Dockerfile exists in repo
- Check pyproject.toml and uv.lock exist

## Performance

- **First run:** depends on enrolled forums and `TOPIC_DETAIL_LIMIT`
- **Subsequent runs:** depends on enrolled forums and `TOPIC_DETAIL_LIMIT`
- **Database size:** ~500KB per semester
- **Build time (Docker):** ~2-5 minutes (first), ~30 seconds (cached)

## Dependencies

- **requests** - HTTP client for web scraping
- **beautifulsoup4** - HTML parsing
- **lxml** - XML/HTML parsing (faster)
- **schedule** - Runs the scraper every 10 minutes inside the container

All managed by UV package manager for reproducible builds.

## Updates

### Update code from GitHub

```bash
git pull origin main
uv sync  # Update dependencies if needed
uv run python ufuture-scraper.py
```

## Validation

```bash
uv sync --frozen
uv run python -m py_compile ufuture-scraper.py scheduler.py
docker build -f Dockerfile -t ufuture-forum-scraper:latest .
```

The live scraper contacts UiTM and Telegram, so run it only when your environment variables are set and you intend to perform a real scrape.

### Update Docker image (TrueNAS)

1. Push code to GitHub
2. In TrueNAS UI → Apps → Your app → Update
3. TrueNAS rebuilds image from latest code

## Limitations

- Requires valid UiTM credentials
- Scrapes only forums you're enrolled in
- Depends on UiTM website structure (may break if they change layout)
- Topic detail notifications are capped by `TOPIC_DETAIL_LIMIT` to avoid broad authenticated crawling
- Telegram API rate limits apply

## Future Improvements

- [ ] Support multiple UiTM accounts
- [ ] Custom notification filters (by course, by time, etc.)
- [ ] Web dashboard to view forum updates
- [ ] Email notifications option
- [ ] Forum archive/export functionality
- [ ] Scheduled runs (cron integration)
- [ ] Discord notifications

## Changelog

### v0.1.0 (2025-11-07)
- Initial release
- Forum scraping
- Change detection
- Telegram notifications
- Docker support
- TrueNAS Scale support

## License

This project is provided as-is for personal use.

## Support

For issues or questions:
1. Check the Troubleshooting section
2. Review your environment variables
3. Check your credentials are correct
4. Review logs in `./logs` folder

## Contributing

Feel free to fork and improve! Ideas welcome.

## Disclaimer

This tool is for personal educational use. Please use responsibly and respect UiTM's terms of service.

---

**Made for UiTM students by a UiTM student** ✨
