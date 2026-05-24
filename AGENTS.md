# AGENTS.md

## Project Scope

This repository is a personal UiTM forum scraper and Telegram notifier. Treat it as a small operational Python service with sensitive credentials, live external dependencies, and stateful local data.

## Working Agreements

- Do not hardcode, print, commit, or preserve secrets. This includes UiTM credentials, Telegram bot tokens, chat IDs, Crawl4AI tokens, cookies, session headers, and `.env` values.
- Do not run the live scraper, contact UiTM, send Telegram messages, or call Crawl4AI unless the user explicitly asks for that action in the current turn.
- Do not store secrets, credentials, cookies, personal portal data, live scrape output, database rows, or Telegram payloads in Codex memories.
- Keep code changes narrow. Prefer fixing the existing single-file scraper and scheduler structure before introducing new modules.
- Use `uv` for local Python dependency workflows when dependency commands are needed.
- Use `rtk` for large shell output when available, especially `rtk git status`, `rtk git diff`, `rtk grep`, `rtk read`, and `rtk test`.
- Prefer `rg` over `grep` for repo searches.
- Use `apply_patch` for manual file edits.
- Do not edit `.env`, `.envrc`, `data/`, or `logs/` unless the user explicitly asks.

## Scraper Safety

- The current scraper is the source of truth unless the user explicitly asks to replace it.
- Crawl4AI is a research/rendering/fallback tool, not a full scraper replacement by default.
- Avoid broad crawling of authenticated UiTM pages. If Crawl4AI is used for UiTM, limit traversal to explicit dashboard, course, forum lobby, and forum topic URLs.
- Keep rate limits conservative. The existing scraper sleeps between course and forum requests; preserve or improve that behavior.
- Do not store rendered pages or screenshots that may contain personal information unless the user asks and confirms the destination.

## Validation

- For scraper-only Python changes, run at least `python -m py_compile ufuture-scraper.py scheduler.py`.
- For dependency or Docker changes, also validate the relevant Docker or `uv` command when feasible.
- For Codex policy changes under `.codex/`, validate JSON/TOML/Python syntax and run `codex execpolicy check` when Codex is available.
- Report any validation that could not be run, including the reason.

## Memory Policy

- Useful memories for this repo are stable preferences and workflow facts only: prefer RTK for large output, use `uv` for Python dependency work, keep Crawl4AI as a research/rendering fallback, and treat the existing scraper as the source of truth.
- Do not use memories as the only source of mandatory rules. Keep required instructions in this file or other checked-in documentation.
- If memory behavior conflicts with this file, follow this file.

## Known Repo Notes

- Main scraper: `ufuture-scraper.py`.
- Scheduler: `scheduler.py`, runs the scraper every 10 minutes in Docker.
- Local state: `data/ufuture_forums.db` and `data/telegram_last_snapshot.json`.
- Docker file name is `Dockerfile` to match TrueNAS/Linux case-sensitive builds.
- README references an older script name; verify filenames before following README commands.
