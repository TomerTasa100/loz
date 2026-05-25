# Loz — Shift Organizer Telegram Bot

Weekly shift coordinator for a Telegram group. Runs in Docker, uses long-polling, stores state in SQLite.

## What it does

- **Thursday 18:00** — posts a kickoff reminder in the group.
- **Saturday 16:00** — DMs people who haven't submitted yet (only those who've `/start`'d the bot); group-mentions the rest.
- **Saturday 20:00** — last-call reminder in the group.
- **Saturday 23:00** — posts the per-day report for the upcoming Sun–Sat week.

Submissions are free-text Hebrew, e.g. `ראשון 14:00-19:00, שלישי 18-22`. Latest message from a user replaces their prior submission for the week. Submissions are accepted in the group or via DM.

## Setup

1. Create a bot via [@BotFather](https://t.me/BotFather), copy the token.
2. Get your Telegram user ID (DM [@userinfobot](https://t.me/userinfobot)).
3. Add the bot to the group, then disable privacy mode via `@BotFather` → `/setprivacy` → `Disable` (otherwise the bot only sees commands, not regular text).
4. Copy env file and fill in values:
   ```sh
   cp .env.example .env
   # edit .env
   ```
5. Optional: set `OPENAI_API_KEY` (and optionally `OPENAI_MODEL`, default `gpt-4o-mini`) to enable an LLM fallback parser for messy messages.

## Run

```sh
docker compose up --build -d
docker compose logs -f bot
```

The SQLite database lives in `./data/shifts.db` on the host.

## Admin commands

DM the bot (must be in `ADMIN_IDS`):

- `/remind kickoff` — fire the Thursday kickoff message now
- `/remind lastcall` — fire the Saturday last-call message now
- `/remind nudge` — fire the DM nudge to non-submitters now
- `/remind report` — build and post the report now

## Inspecting state

```sh
docker compose exec bot sqlite3 /data/shifts.db \
  "SELECT u.first_name, s.parsed_json FROM submissions s JOIN users u ON u.telegram_user_id=s.user_id ORDER BY s.created_at DESC LIMIT 20;"
```
