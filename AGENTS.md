# AGENTS.md

## Project Scope

This repository is a self-hosted UiTM uFuture forum scraper and Telegram notifier, shared privately with a few classmates. Treat it as a small operational Python service with sensitive credentials, live external dependencies, and stateful local data.

## Working Agreements

- Default to planning mode for this repository: return an implementation plan first and wait for explicit approval before making code changes, repo changes, config changes, commits, live network calls, container actions, or deployment changes.
- Do not make code changes, repo changes, generated file changes, dependency changes, or documentation changes without explicit user approval in the current turn. Reading files and running read-only inspection commands is allowed.
- Do not hardcode, print, commit, or preserve secrets. This includes UiTM credentials, Telegram bot tokens, chat IDs, Crawl4AI tokens, cookies, session headers, and `.env` values.
- Do not run the live scraper, contact UiTM, send Telegram messages, or call Crawl4AI unless the user explicitly asks for that action in the current turn.
- Do not store secrets, credentials, cookies, personal portal data, live scrape output, database rows, or Telegram payloads in AI assistant memories.
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

## RTK Command Policy

- Use `rtk` as the default wrapper for supported shell commands that may produce verbose output or benefit from token filtering.
- Prefer explicit RTK commands over raw equivalents when inspecting files, git state, logs, tests, builds, dependency state, containers, or command output.

### Preferred RTK Commands

- Files/search: `rtk ls`, `rtk read`, `rtk smart`, `rtk find`, `rtk grep`, `rtk diff`
- Git read-only: `rtk git status`, `rtk git log`, `rtk git diff`
- Git state-changing: use `rtk git add`, `rtk git commit`, `rtk git push`, and `rtk git pull` only after user approval
- GitHub CLI: `rtk gh pr list`, `rtk gh pr view`, `rtk gh issue list`, `rtk gh run list`
- Tests: `rtk pytest`, `rtk playwright test`, `rtk test <cmd>`, `rtk err <cmd>`
- Build/lint: `rtk ruff check`, `rtk lint`, `rtk prettier --check`, `rtk tsc`
- Package/dependency inspection: `rtk pip list`, `rtk pip outdated`, `rtk deps`
- Containers: `rtk docker ps`, `rtk docker images`, `rtk docker logs`, `rtk docker compose ps`
- Logs/data: `rtk log`, `rtk json`, `rtk summary`, `rtk proxy`
- RTK analytics: `rtk gain`, `rtk gain --history`, `rtk gain --daily`, `rtk discover`, `rtk session`

### Fallbacks

- Use raw commands only when RTK does not support the command, the RTK-filtered output is insufficient, or exact raw output is required for debugging or validation.
- RTK does not relax repository safety rules: approval requirements, live network restrictions, secret-handling rules, and destructive-command restrictions still apply.

## Validation

- After any approved implementation, validate the changed surface before reporting completion.
- After any approved implementation, run at least one smoke test that exercises the affected behavior without contacting UiTM or Telegram unless the user explicitly approved a live test.
- For scraper-only Python changes, run at least `python -m py_compile ufuture-scraper.py scheduler.py`.
- For dependency or Docker changes, also validate the relevant Docker or `uv` command when feasible.
- For `docker-compose.yml`, `.env.example` or workflow changes, validate with `docker compose config` and a YAML/workflow linter.
- Report any validation that could not be run, including the reason.

## Smoke Test Artifacts

- Keep smoke-test files out of the remote repo by default.
- Prefer temporary paths outside the repo for smoke-test databases, snapshots, logs, HTML captures, screenshots, or generated fixtures.
- If a smoke-test artifact must be created in the repo, put it under an ignored path and remove it before finishing unless the user explicitly asks to keep it.
- Before final reporting, check `git status --short` and call out any untracked smoke-test artifacts.

## Memory Policy

- Useful memories for this repo are stable preferences and workflow facts only: prefer RTK for large output, use `uv` for Python dependency work, keep Crawl4AI as a research/rendering fallback, and treat the existing scraper as the source of truth.
- Do not use memories as the only source of mandatory rules. Keep required instructions in this file or other checked-in documentation.
- If memory behavior conflicts with this file, follow this file.

## Known Repo Notes

- Main scraper: `ufuture-scraper.py`.
- Scheduler: `scheduler.py`, runs the scraper every 10 minutes in Docker and sends failure/recovery alerts.
- Interactive bot: `telegram_interactive_bot.py` (buttons, commands, reply drafts, posting replies to UiTM). It imports `ufuture-scraper.py` for login, parsing and formatting, so changes there can affect the bot.
- Supervisor: `supervisor.py` starts and restarts the scheduler and the bot in one container.
- Local state: `data/ufuture_forums.db` and `data/telegram_last_snapshot.json`.
- Docker file name is `Dockerfile` to match TrueNAS/Linux case-sensitive builds. The image installs pinned dependencies from `uv.lock` (`uv sync --frozen`).
- Release flow: merge to `main`, then publish a GitHub release `vX.Y.Z`. The `Publish image and setup files` workflow builds `ghcr.io/yazidyaakub/ufuture-telegram-notifier:vX.Y.Z` (+ `latest`, amd64/arm64) and attaches `ufuture-setup.zip`. Users update with `docker compose pull && docker compose up -d`; TrueNAS changes the image tag.
- Shared with classmates: keep docs and messages understandable for non-technical users, and never commit personal data (student IDs, hostnames, tokens). `.env.example` and `docs/setup-guide.md` must stay in sync with the settings the code reads.
- Posting to UiTM (`UFUTURE_REPLY_ENABLED`) creates real, public forum posts. Never test it live without explicit approval in the current turn.
- For interactive-bot changes, also run `python -m py_compile telegram_interactive_bot.py supervisor.py`.
