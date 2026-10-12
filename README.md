# uFuture Telegram Notifier

[![Python](https://img.shields.io/badge/Python-3.10-blue)](https://www.python.org/)
[![Docker](https://img.shields.io/badge/Docker-amd64%20%7C%20arm64-2496ED)](docs/setup-guide.md)
[![Telegram](https://img.shields.io/badge/Telegram-Bot-26A5E4)](https://core.telegram.org/bots)
[![License](https://img.shields.io/badge/License-Apache_2.0-green)](LICENSE)

Watches your UiTM uFuture course forums and brings them into Telegram: notifications for new posts, lecturer files in the chat, browsing, and replying back to the forum. Runs on your own computer or server with Docker.

## Features

- **Forum monitoring**: checks every enrolled course forum every 10 minutes and detects new topics and posts exactly (compared against every stored post).
- **Smart notifications**: lecturer posts, new topics and replies to you arrive with sound; classmates' replies are grouped into one silent summary; your own posts never notify.
- **Files in Telegram**: attachments from new posts are sent into the chat (images inline), held in memory only and never written to disk.
- **Topic cards**: one `▸` button per topic opens details, files and reply options in place.
- **Reply from Telegram**: swipe-reply to a notification (optionally with files); the reply is posted with UiTM's own reply form and confirmed on the forum.
- **Bot commands**: `/latest`, `/courses`, `/files`, `/status`, `/drafts`, `/mute`.
- **Safe by default**: posting is off until enabled; login rejections are not retried (no account lockout); failure and recovery alerts.
- **Easy to run**: one Docker container (ready-made image for PCs and Raspberry Pi), set up with a single `.env` file; also runs on TrueNAS.

## How it fits together

```
supervisor.py ── starts and watches two processes ──┐
  ├── scheduler.py ── every 10 min ──> ufuture-scraper.py ──> UiTM uFuture
  │                                        │  (login, scrape, compare, notify, send files)
  │                                        └──> SQLite (data/ufuture_forums.db) + Telegram
  └── telegram_interactive_bot.py ── long polling ──> Telegram
                                           (buttons, commands, drafts, posting replies)
```

Both processes share the SQLite database. The bot reuses the scraper module for UiTM login, downloads and formatting.

## Get started

**New to this? Follow the step-by-step [setup guide](docs/setup-guide.md).** No technical experience is needed: it covers installing Docker Desktop, creating your Telegram bot, filling in your settings and starting the app.

In short, with Docker installed:

1. Download `ufuture-setup.zip` from the latest [release](https://github.com/YazidYaakub/ufuture-telegram-notifier/releases) and unzip it into a folder.
2. Copy `.env.example` to `.env` and fill in the 5 values in PART 1 (every setting is explained inside the file).
3. In that folder, run:
   ```bash
   docker compose up -d
   ```
4. Send your bot `/status` in Telegram.

The first check stores your forums and sends no notification; from then on you're told about new posts.

Each person runs their own copy with their own UiTM login and their own Telegram bot.

### Other ways to run it

- **TrueNAS SCALE:** see [docs/deploy/truenas.md](docs/deploy/truenas.md).
- **Any Docker host (Linux server, Raspberry Pi, Unraid, Synology, Portainer):** use the same `docker-compose.yml` and `.env`.
- **From source (development):**
  ```bash
  git clone https://github.com/YazidYaakub/ufuture-telegram-notifier.git
  cd ufuture-telegram-notifier
  uv sync
  set -a; source .env; set +a      # load your settings
  DB_PATH=./data/ufuture_forums.db SNAPSHOT_PATH=./data/telegram_last_snapshot.json LOG_DIR=./logs \
    uv run python ufuture-scraper.py
  ```
  Add `DRY_RUN=true` (with a temporary `DB_PATH` / `SNAPSHOT_PATH`) to see messages without sending them.

### Updating

```bash
docker compose pull
docker compose up -d
```

## Configuration

All settings are read from `.env` (see [`.env.example`](.env.example), which explains each one in plain language). With Docker Compose, `DB_PATH`, `SNAPSHOT_PATH` and `LOG_DIR` are already set for you.


| Variable | Description | Default |
|----------|-------------|---------|
| `UFUTURE_USERNAME` | UiTM student ID | required |
| `UFUTURE_PASSWORD` | UiTM password | required |
| `TELEGRAM_BOT_TOKEN` | Telegram bot token | required |
| `TELEGRAM_CHAT_ID` | Telegram chat that receives notifications and may use the bot | required |
| `TELEGRAM_ALLOWED_USER_IDS` | Telegram user IDs allowed to use buttons, commands and replies (comma-separated); blank allows anyone in the chat | blank |
| `DB_PATH` | SQLite database file | `/app/data/ufuture_forums.db` |
| `SNAPSHOT_PATH` | State snapshot file | `/app/data/telegram_last_snapshot.json` |
| `LOG_DIR` | Logs directory | `/app/logs` |
| `REQUEST_RETRIES` | HTTP/DNS retry attempts (not used for rejected logins) | `3` |
| `REQUEST_BACKOFF_SECONDS` | Base delay between retries | `5` |
| `SCRAPE_TOPIC_DETAILS` | Fetch topic pages for post details | `true` |
| `TOPIC_DETAIL_MODE` | `changed` fetches topic pages only for weeks whose counts changed (plus a full fetch when the database has no topics yet); `all` fetches up to `TOPIC_DETAIL_LIMIT` topics every run | `changed` |
| `TOPIC_DETAIL_LIMIT` | Maximum topic pages fetched per run | `20` |
| `INCLUDE_REPLY_EXCERPTS` | Include reply excerpts in `classic` notifications | `true` |
| `REPLY_EXCERPT_LENGTH` | Maximum reply excerpt length | `160` |
| `DETAIL_EXCERPT_LENGTH` | Maximum stored post excerpt length (Details view) | `700` |
| `TELEGRAM_LAYOUT` | `digest`: one message with sound per important topic plus one silent classmate summary per run. `classic`: one message per changed week | `digest` |
| `TELEGRAM_SILENT_CLASSMATE_REPLIES` | Send classmate-only updates silently | `true` |
| `TELEGRAM_MAX_TOPICS_PER_MESSAGE` | Maximum topics per message or summary | `8` |
| `TELEGRAM_INLINE_BUTTONS` | Add inline buttons to notifications | `true` |
| `TELEGRAM_BUTTON_MODE` | `hybrid`/`callback`: buttons handled by the bot; anything else: plain links to UiTM | `hybrid` |
| `TELEGRAM_DEDUP_REPLIES` | Compact identical reply excerpts (`classic`) | `true` |
| `TELEGRAM_SHORT_NAMES` | Shorten long names (`classic`) | `true` |
| `TELEGRAM_MEDIA_BUTTONS` | Show a Media link button (`classic`) | `true` |
| `TELEGRAM_REPLY_TARGET_LIMIT` | No longer used (the reply picker always offers up to 8 posts) | `8` |
| `TELEGRAM_SEND_ATTACHMENTS` | Send attachments from new posts into Telegram (memory only, never written to disk) | `true` |
| `ATTACHMENT_MAX_MB` | Largest attachment sent as a file; bigger ones get an "Open in UiTM" link (Telegram's bot limit is 50 MB) | `20` |
| `ATTACHMENT_MAX_PER_RUN` | Maximum attachments sent per run; the rest follow on later runs | `5` |
| `TELEGRAM_INTERACTIVE_BOT` | Run the interactive bot worker | `true` |
| `TELEGRAM_ACTION_TTL_HOURS` | How long file and legacy buttons stay valid; topic buttons never expire | `720` |
| `TELEGRAM_REPLY_DRAFT_TTL_HOURS` | How long an open reply draft stays valid (saved drafts: 7 days) | `24` |
| `TELEGRAM_BOT_RESTART_SECONDS` | Delay before the supervisor restarts a crashed bot | `5` |
| `TELEGRAM_DROP_PENDING_UPDATES` | Ignore Telegram updates queued before the bot's first start | `true` |
| `UFUTURE_REPLY_ENABLED` | Allow **Send to UiTM** to post replies (with attachments) after preview and confirmation | `false` |
| `UFUTURE_POST_REPLIES` | Legacy name for `UFUTURE_REPLY_ENABLED` | `false` |
| `REPLY_CONFIRM_BEFORE_SEND` | No longer used: every reply always goes through the preview and needs a tap on Send | `true` |
| `REPLY_MIN_INTERVAL_SECONDS` | Minimum time between two posted replies | `60` |
| `REPLY_POST_TIMEOUT_SECONDS` | Timeout for posting a reply | `45` |
| `SCRAPER_TIMEOUT_SECONDS` | Maximum time for one scraper run | `300` |
| `SCRAPER_FAILURE_LIMIT` | Stop the scheduler after N failed runs; `0` never stops | `0` |
| `SCRAPER_ALERT_AFTER` | Telegram alert after N failed runs in a row, plus a recovery message; `0` disables | `3` |
| `LOCAL_UTC_OFFSET_HOURS` | Hours ahead of UTC for times shown in Telegram (UiTM uses Malaysia time; the container runs in UTC) | `8` |
| `DRY_RUN` | Print messages and alerts instead of sending them | `false` |
| `SCRAPER_PATH`, `SCHEDULER_PATH`, `TELEGRAM_INTERACTIVE_BOT_PATH` | Script paths inside the container (no need to change) | `/app/...` |

## Telegram setup

1. **Create the bot:** message **@BotFather**, send `/newbot`, and copy the token (`TELEGRAM_BOT_TOKEN`).
2. **Get your chat ID:** send your bot a message, then open `https://api.telegram.org/bot<TOKEN>/getUpdates` and read `"chat":{"id":...}`. In a private chat this is also your user ID for `TELEGRAM_ALLOWED_USER_IDS`. **@userinfobot** shows it too.

The bot only responds in `TELEGRAM_CHAT_ID`, and only to `TELEGRAM_ALLOWED_USER_IDS` when set.

## Using it in Telegram

### Notifications (`TELEGRAM_LAYOUT=digest`)
- **Important topics** (new topics, posts by the topic creator, replies to your posts) each get a short message **with sound**: course, week (`W2`), topic, who posted and when, the post text and attachment names. Their files arrive as replies to that message.
- **Classmates' replies** are grouped into one **silent** "Classmate replies" summary per run.
- Your own posts never notify.
- Each topic has one `Topic ▸` button. It turns the message into a topic card (`Details`, `Reply`, `Files`, `Open in UiTM`, `« Back`); Details and Back edit the same message.

### Commands

| Command | What it does |
|---|---|
| `/latest` | The 5 newest posts (not yours), each with `▸` to its topic card |
| `/courses` | Browse course → week → topic, ending at the topic card |
| `/files` | Pick a course, then tap a file to get it in the chat |
| `/status` | Last successful check, last failure and reason, counts, file queue, muted courses |
| `/drafts` | Continue a saved reply draft |
| `/mute CSC649`, `/unmute CSC649` | Silence a course (replies to your posts still notify); without a code, shows course buttons |
| `/help` | Lists the commands |
| `/cancel` | Cancel the current reply draft |

### Replying to the forum
- **Swipe-reply** to a notification, topic card or list message and type (or send a file). The bot drafts a reply to the post shown in that message; on the classmate summary it first asks which topic. Swipe-reply works on notifications sent by v1.6.0 or later; for older ones, tap `▸` first.
- **Or** tap `▸` → `Reply`, pick the post you're replying to, and type.
- Attach up to 5 files (20 MB each). A file's caption becomes the reply text.
- The draft is one message: `Send to UiTM`, `Edit text`, `Change target`, `Remove files`, `Save`, `Cancel`.
- **Send to UiTM** (with `UFUTURE_REPLY_ENABLED=true`) posts through UiTM's reply form, re-reads the topic, and only reports `Posted` once the reply is visible. The topic is refreshed in the bot right away, and Details marks your posts as `You`.
- **Safety:** preview and confirm every post, allowed users only, identical replies blocked, one post per `REPLY_MIN_INTERVAL_SECONDS`, no automatic retries. If UiTM rejects a reply the draft stays ready; if it can't be confirmed you're asked to check the topic first.

## How it works

1. **Login** with your UiTM credentials. A rejected login stops the run (exit code `3`) without retrying.
2. **Dashboard and forums:** reads your enrolled courses and each course's weekly forum table.
3. **Change detection:** compares week post and topic counts with the last snapshot.
4. **Topic details:** fetches topic pages only for weeks that changed, then compares posts against every post ID already in SQLite, so new posts are exact.
5. **Classification:** posts are ordered by time; your own posts are excluded; replies to you and posts by the topic creator are marked important.
6. **Notify** in Telegram, then **send attachments** from new posts (images inline, other files as documents; up to 3 attempts; nothing written to disk).
7. **Clean up:** courses no longer on your dashboard (for example last semester's) are deleted from the database. A course whose page failed to load is kept; a failed run deletes nothing.
8. **Save the snapshot** and record the run status for `/status`.

The scheduler sends one Telegram alert after `SCRAPER_ALERT_AFTER` failed runs in a row, and a recovery message when it works again.

## Database

SQLite at `DB_PATH`. Tables are created and migrated automatically on start.

| Table | Holds |
|---|---|
| `courses`, `weeks`, `topics` | Your current courses, their weekly forums and topics |
| `posts` | Every scraped post: author, excerpt, time, thread position, `attachments_json`, `is_own` |
| `attachment_queue` | Attachments to relay to Telegram: status, attempts, size, Telegram `file_id` and kind. File contents are never stored. |
| `telegram_actions` | Data behind file buttons and older notification buttons |
| `telegram_messages`, `telegram_message_topics` | Sent notifications (for `« Back`) and which topic and post each is about (for swipe-reply) |
| `telegram_pending_replies` | Reply drafts: target, text, attachments, state |
| `telegram_bot_state` | Bot offset, muted courses, run status, one-time migration flags |

## Project structure

```
ufuture-telegram-notifier/
├── ufuture-scraper.py           # Scraper: login, parsing, change detection, notifications, attachments
├── scheduler.py                 # Runs the scraper every 10 minutes; failure/recovery alerts
├── telegram_interactive_bot.py  # Bot: buttons, commands, topic cards, drafts, posting replies
├── supervisor.py                # Starts and watches the scheduler and the bot in one container
├── Dockerfile                   # Image: installs pinned dependencies from uv.lock
├── pyproject.toml / uv.lock     # Dependencies (pinned)
├── docker-compose.yml           # Ready-to-run setup using the published image
├── .env.example                 # All settings, explained (copy to .env)
├── docs/setup-guide.md          # Step-by-step guide for non-technical users
├── docs/deploy/truenas.md       # TrueNAS SCALE setup
├── .github/workflows/publish.yml # Builds the image and setup zip on each release
└── README.md
```

## Development

```bash
uv sync --frozen
uv run python -m py_compile ufuture-scraper.py scheduler.py telegram_interactive_bot.py supervisor.py
docker build -f Dockerfile -t ufuture-telegram-notifier:dev .
```

- A real run contacts UiTM and Telegram. Use `DRY_RUN=true` with a temporary `DB_PATH` / `SNAPSHOT_PATH` to check changes safely.
- Dependencies are pinned in `uv.lock` and the image installs exactly those (`uv sync --frozen`). To upgrade them: `uv lock --upgrade`, test, commit, release.

## Troubleshooting

| Symptom | What to check |
|---|---|
| Exit code `3` / "UiTM login was rejected" | Username or password; the scraper doesn't retry to avoid locking the account |
| Exit code `75` / "DNS lookup failed" | Container or TrueNAS DNS, not the parser. Add `dns: [1.1.1.1, 8.8.8.8]` to the YAML; if you use Technitium, check it isn't blocking `ufuture.uitm.edu.my` |
| Exit code `2` | A required setting is missing (see the `… not set` line in the logs) |
| `env file … .env not found` / `no configuration file provided` | Run `docker compose` inside the folder that has `docker-compose.yml` and `.env` |
| TrueNAS problems | See [docs/deploy/truenas.md](docs/deploy/truenas.md#troubleshooting) |
| A `▸` or Details button says the action expired | File and older buttons expire after `TELEGRAM_ACTION_TTL_HOURS`; topic buttons on newer notifications never expire |
| Swipe-reply does nothing on an old notification | It needs a notification from v1.6.0 or later; tap `▸` first |
| "Telegram notification failed" | Bot token and chat ID |
| Bot logs a connection error at startup | The supervisor restarts it after `TELEGRAM_BOT_RESTART_SECONDS`; it recovers by itself |

## Security

- Credentials and tokens come from environment variables only; `.env` / `.envrc` are git-ignored.
- In TrueNAS the settings live in the app's YAML. Treat that YAML as a secret, and never commit it.
- Use `TELEGRAM_ALLOWED_USER_IDS` so only you can use the bot.
- Attachments are relayed in memory and never stored; only Telegram `file_id`s are kept.
- Posting to UiTM is off by default and always needs a preview and confirmation.

## Limitations

- Needs valid UiTM credentials and only covers forums you're enrolled in.
- Depends on uFuture's page layout; a layout change can break parsing (you'll get a failure alert).
- Topic details are capped at `TOPIC_DETAIL_LIMIT` pages per run to keep the load on UiTM low.
- Telegram limits: 50 MB uploads, 20 MB bot downloads (reply attachments).

## Releases

Changes are listed in [GitHub Releases](https://github.com/YazidYaakub/ufuture-telegram-notifier/releases). Each release publishes the image `ghcr.io/yazidyaakub/ufuture-telegram-notifier` and a `ufuture-setup.zip` with the setup files.

## License

Apache License 2.0; see [LICENSE](LICENSE).

## Disclaimer

For personal educational use. Use responsibly and respect UiTM's terms of service.
