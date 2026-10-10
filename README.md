# UiTM Forum Scraper + Telegram Notifier

Automatically scrape UiTM forums, detect updates, and receive Telegram notifications about new posts and topics.

## Features

- 🔍 **Automatic Forum Scraping** - Scrapes all your UiTM course forums
- 🆕 **Change Detection** - Detects new posts and topics since last run
- 📱 **Telegram Notifications** - Get instant alerts with topic buttons and reply excerpts
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
      - DETAIL_EXCERPT_LENGTH=700
      - TELEGRAM_MAX_TOPICS_PER_MESSAGE=8
      - TELEGRAM_INLINE_BUTTONS=true
      - TELEGRAM_DEDUP_REPLIES=true
      - TELEGRAM_SHORT_NAMES=true
      - TELEGRAM_MEDIA_BUTTONS=true
      - TELEGRAM_REPLY_TARGET_LIMIT=8
      - TELEGRAM_INTERACTIVE_BOT=true
      - TELEGRAM_BUTTON_MODE=hybrid
      - TELEGRAM_ACTION_TTL_HOURS=72
      - TELEGRAM_REPLY_DRAFT_TTL_HOURS=24
      - TELEGRAM_BOT_RESTART_SECONDS=5
      - TELEGRAM_DROP_PENDING_UPDATES=true
      - UFUTURE_REPLY_ENABLED=false
      - TELEGRAM_ALLOWED_USER_IDS=
      - REPLY_CONFIRM_BEFORE_SEND=true
      - REPLY_POST_TIMEOUT_SECONDS=45
      - UFUTURE_POST_REPLIES=false
      - SCRAPER_TIMEOUT_SECONDS=300
      - SCRAPER_FAILURE_LIMIT=0
      - SCRAPER_ALERT_AFTER=3
      - TOPIC_DETAIL_MODE=changed
      - TELEGRAM_SILENT_CLASSMATE_REPLIES=true
    
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
   - `TELEGRAM_ALLOWED_USER_IDS` can be left blank, or set to your Telegram user ID to lock reply actions to one user

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
| `DETAIL_EXCERPT_LENGTH` | Maximum stored excerpt length used by interactive Details views | `700` |
| `TELEGRAM_MAX_TOPICS_PER_MESSAGE` | Maximum topic detail blocks included per changed week | `8` |
| `TELEGRAM_INLINE_BUTTONS` | Add Telegram inline URL buttons for changed topics | `true` |
| `TELEGRAM_DEDUP_REPLIES` | Compact repeated identical reply excerpts with a multiplier | `true` |
| `TELEGRAM_SHORT_NAMES` | Shorten long display names in the main notification | `true` |
| `TELEGRAM_MEDIA_BUTTONS` | Show a media button when a media/attachment URL is detected | `true` |
| `TELEGRAM_REPLY_TARGET_LIMIT` | Maximum recent replyable posts offered as Telegram reply targets, excluding the original post | `8` |
| `TELEGRAM_INTERACTIVE_BOT` | Run the long-polling interactive Telegram bot worker | `true` |
| `TELEGRAM_BUTTON_MODE` | Button behavior; `hybrid` uses callback buttons for Details/Reply and URL buttons for Media/Open | `hybrid` |
| `TELEGRAM_ACTION_TTL_HOURS` | How long notification callback actions remain valid | `72` |
| `TELEGRAM_REPLY_DRAFT_TTL_HOURS` | How long pending Telegram reply drafts remain valid | `24` |
| `TELEGRAM_BOT_RESTART_SECONDS` | Supervisor delay before restarting a crashed bot worker | `5` |
| `TELEGRAM_DROP_PENDING_UPDATES` | Ignore queued Telegram updates on the first bot start after deployment | `true` |
| `UFUTURE_REPLY_ENABLED` | Enable live UiTM reply posting after preview confirmation; keep disabled until posting is verified | `false` |
| `TELEGRAM_ALLOWED_USER_IDS` | Optional Telegram user IDs allowed to use reply actions, comma-separated for multiple users; blank allows the configured chat | blank |
| `REPLY_CONFIRM_BEFORE_SEND` | Require preview confirmation before any UiTM reply post attempt | `true` |
| `REPLY_POST_TIMEOUT_SECONDS` | Timeout for a confirmed UiTM reply post attempt | `45` |
| `UFUTURE_POST_REPLIES` | Legacy live-posting guard; prefer `UFUTURE_REPLY_ENABLED` | `false` |
| `SCRAPER_TIMEOUT_SECONDS` | Scheduler subprocess timeout | `300` |
| `SCRAPER_FAILURE_LIMIT` | Exit scheduler after N failures; `0` means never exit | `0` |
| `SCRAPER_PATH` | Path used by scheduler to run scraper | container scraper path |
| `SCRAPER_ALERT_AFTER` | Send one Telegram alert after N consecutive failed runs, and a recovery message when it works again; `0` disables | `3` |
| `TOPIC_DETAIL_MODE` | `changed` fetches topic pages only for weeks whose counts changed (plus a full fetch when the database has no topics yet); `all` fetches up to `TOPIC_DETAIL_LIMIT` topics every run | `changed` |
| `TELEGRAM_SILENT_CLASSMATE_REPLIES` | Send notifications silently when they only contain classmates' replies; new topics, posts by the topic creator, and replies to your posts still notify with sound | `true` |
| `TELEGRAM_SEND_ATTACHMENTS` | Download attachments from new posts with the logged-in UiTM session and send them into Telegram as a reply to their notification. Files are held in memory only and never written to disk | `true` |
| `ATTACHMENT_MAX_MB` | Largest attachment sent as a file; bigger ones get an "Open in UiTM" link instead (Telegram's bot upload limit is 50 MB) | `20` |
| `ATTACHMENT_MAX_PER_RUN` | Maximum attachments sent per scraper run; the rest are sent on later runs | `5` |
| `TELEGRAM_LAYOUT` | `digest` sends one message with sound per important topic (new topic, lecturer/topic-creator post, reply to you) plus one silent summary of classmates' replies per run; `classic` keeps one message per changed week | `digest` |
| `DRY_RUN` | Print Telegram messages and alerts instead of sending them; use with temporary `DB_PATH`/`SNAPSHOT_PATH` | `false` |

## Telegram Setup

Topic detail notifications can include inline buttons. In `hybrid` mode, `Details` and `Reply` are Telegram callback buttons handled inside the chat, while `Media` and `Open` remain URL buttons. `Media` appears only when a media or attachment URL is detected. Reply target selection, preview, edit, save draft, and cancel run inside Telegram; live UiTM posting remains guarded by `UFUTURE_REPLY_ENABLED=false` until the posting path is verified.

The container runs a small supervisor that starts both the scraper scheduler and the interactive Telegram bot worker. The bot uses Telegram long polling, stores its update offset in SQLite, and only responds to the configured `TELEGRAM_CHAT_ID`.

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
├── telegram_interactive_bot.py              # Handles Telegram callback buttons and reply drafts
├── supervisor.py                            # Runs scheduler and interactive bot in one container
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
media_url       TEXT
media_label     TEXT
attachments_json TEXT     -- all attachments in the post: [{"url", "label"}]
is_own          INTEGER   -- 1 when the post belongs to the logged-in user
posted_time     TEXT
last_updated    TIMESTAMP
UNIQUE(topic_id, post_id)
```

### attachment_queue table
```sql
url                 TEXT PRIMARY KEY
label               TEXT
course_code         TEXT
topic_id            TEXT
topic_title         TEXT
post_id             TEXT
reply_to_message_id TEXT      -- Telegram notification the file replies to
silent              INTEGER
status              TEXT      -- pending, sent, too_large, failed
attempts            INTEGER
size_bytes          INTEGER
telegram_file_id    TEXT      -- lets the file be re-sent without downloading it again
last_error          TEXT
created_at          TEXT
updated_at          TEXT
```
File contents are never stored.

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
- Compares against every post id already stored in SQLite, so new replies are exact even when there are more topics than `TOPIC_DETAIL_LIMIT`
- Orders posts by posted time (UiTM renders nested replies out of time order)
- Skips your own posts; a change made only of your own posts sends nothing
- Tags replies to your posts with `↩️ you` and posts by the topic creator with `📌`, lists attachment names with `📎`
- Sends attachments from new posts into Telegram as a reply to the notification (attachments that already existed before are not sent). Failed downloads are retried on the next runs, up to 3 attempts
- Prioritizes changed weeks before spending the topic detail limit, then adds topic titles, recent reply excerpts, inline topic buttons, and optional media buttons when topic detail scraping is enabled
- **Sends Telegram notification** if changes found
- Updates state snapshot

### State Comparison
The snapshot file uses schema version 2 and stores `weeks`, `topics`, and `posts`. Older week-only snapshot lists are still accepted and upgraded on the next successful run. On the first upgraded run, topic details enrich the notification; exact per-post diffs become available after one version-2 snapshot exists.

### Notification Layout (`TELEGRAM_LAYOUT=digest`)
- **Important topics** (new topics, posts by the topic creator, replies to your posts) each get a short message with sound: course, week (`W2`), topic, who posted and when (`today 2:10 PM`), the post text and 📎 attachment names. Attachments are sent as replies to that message.
- **Classmates' replies** are grouped into one silent "Classmate replies" summary per run, with counts and first names.
- Each topic has a single `Topic ▸` button that turns the message into a topic card (`📄 Details`, `✍️ Reply`, `🌐 Open in UiTM`, `« Back`). Details and Back edit the same message instead of adding new ones.

### Bot Commands
| Command | What it does |
|---|---|
| `/latest` | The 5 newest posts (excluding yours), each with a `▸` button to its topic card |
| `/courses` | Browse course → week → topic, ending at the topic card |
| `/files` | Pick a course, then tap a file to get it in the chat |
| `/status` | Last successful check, last failure and reason, counts, attachment queue, muted courses |
| `/mute CSC649`, `/unmute CSC649` | Silence a course; replies to your posts still notify. Without a code, shows course buttons |
| `/help` | Lists the commands; the bot also shows a hint when you send text without an open reply draft |
| `/cancel` | Cancel the current reply draft |

Files already sent to Telegram are re-sent instantly from their Telegram `file_id`. Other files are downloaded by the bot with its own UiTM login, in memory only, and sent; nothing is written to disk. Topic cards show a `📎 Files` button when the topic has attachments.

Courses that are no longer on your UiTM dashboard (for example last semester's) are deleted from the database, with their weeks, topics, posts and queued files, at the end of each successful scrape. A course whose page fails to load in a run is kept; a failed run deletes nothing.

The first run after upgrading re-reads every topic page once (up to `TOPIC_DETAIL_LIMIT`) to fill in attachments and your own posts for data stored by older versions. No notifications are sent for this refresh.

### Interactive Telegram Bot
- `Topic ▸` opens the topic card in place; `« Back` restores the original notification.
- `Details` shows stored topic/reply context inside Telegram.
- `Reply` lets you choose `Original post` or a recent replyable reply, then write a response.
- `Preview` shows the target and message before any post attempt.
- `Confirm` is guarded; with the default config it keeps the draft in Telegram and does not post to UiTM.
- `Save Draft` stores the draft locally only; `Cancel` discards the flow.
- `Media` opens the detected media/attachment URL instead of downloading it into Telegram.
- `Open` opens the UiTM topic page.
- UiTM posting is intentionally guarded and disabled by default.

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
**Solution:** Read the scraper log immediately above that line. Exit code `75` means a temporary network or DNS failure. Exit code `3` means UiTM rejected the login (the scraper does not retry, to avoid locking the account). Exit code `2` means required configuration is missing.

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

For interactive-bot branch changes, also validate:

```bash
uv run python -m py_compile telegram_interactive_bot.py supervisor.py
```

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
