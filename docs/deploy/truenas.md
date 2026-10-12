# Running on TrueNAS SCALE

Uses the published image, so there's no build, no GitHub token and no `pull_policy: build`.

1. **Apps → Discover Apps → Custom App** (Install via YAML).
2. Paste the YAML below and fill in your values (see [`.env.example`](../../.env.example) for what each one is).
3. Click **Install**, then open the app's **Logs**: you should see `Logged in successfully` and `Scraper completed with code 0`.

```yaml
services:
  ufuture-telegram-notifier:
    # Pin a version (recommended) or use :latest.
    image: ghcr.io/yazidyaakub/ufuture-telegram-notifier:v1.7.0
    container_name: ufuture-telegram-notifier
    restart: unless-stopped
    # Optional: public DNS, if your LAN DNS is filtered or unreliable.
    dns:
      - 1.1.1.1
      - 8.8.8.8
    environment:
      - UFUTURE_USERNAME=your_student_id
      - UFUTURE_PASSWORD=your_password
      - TELEGRAM_BOT_TOKEN=your_bot_token
      - TELEGRAM_CHAT_ID=your_chat_id
      - TELEGRAM_ALLOWED_USER_IDS=your_telegram_user_id
      - UFUTURE_REPLY_ENABLED=false
      - DB_PATH=/app/data/ufuture_forums.db
      - SNAPSHOT_PATH=/app/data/telegram_last_snapshot.json
      - LOG_DIR=/app/logs
    volumes:
      - ufuture-data:/app/data
      - ufuture-logs:/app/logs

volumes:
  ufuture-data:
  ufuture-logs:
```

## Upgrading

Change the version in `image:` (e.g. `v1.7.0` → `v1.8.0`) and click **Update**. With `:latest`, use the app's **Pull Images / Update** action instead.

## Moving from a build-from-source setup

If your current YAML has `build: context: https://...github.com/...` and `pull_policy: build`, replace those two keys with the `image:` line above. Keep the same volume names so your existing database is reused.

## Troubleshooting

| Symptom | What to check |
|---|---|
| Update fails with "Invalid YAML" | The YAML was damaged when pasting (long lines broken); paste again into an empty editor |
| `DNS lookup failed` (exit code 75) | Container DNS; keep the `dns:` lines, or check your LAN DNS (e.g. Technitium) isn't blocking `ufuture.uitm.edu.my` |
| Image can't be pulled | Check the image name and that the package is public on GitHub (Packages → package settings → visibility) |
