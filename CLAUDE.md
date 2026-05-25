# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```sh
# Build + run (long-polling bot). Recreates the container on code change.
docker compose up --build -d
docker compose logs -f bot
docker compose down

# Inspect SQLite state (DB lives in the `data/` volume on the host).
docker compose exec bot sqlite3 /data/shifts.db "SELECT * FROM submissions ORDER BY created_at DESC LIMIT 20;"

# Trigger scheduled jobs on demand (DM to bot, sender must be in ADMIN_IDS):
#   /remind kickoff | lastcall | nudge | report
```

There are no tests, lint config, or local Python venv flow — the bot is exclusively run via Docker against the live Telegram API. The image is rebuilt on every `up --build` (≈seconds when layers are cached).

## Architecture

Single-process Python bot using `python-telegram-bot` long-polling. State lives in SQLite at `/data/shifts.db`. All times are `Asia/Jerusalem` (`TZ` env).

**Wiring** (`bot/main.py`): builds the PTB `Application`, registers `CommandHandler`s (`/start`, `/help`, `/remind`), a text `MessageHandler`, and one `CallbackQueryHandler` for inline-keyboard taps. `allowed_updates` includes both `message` and `callback_query` — adding new update types requires expanding that list. `scheduler.register_jobs` attaches APScheduler jobs to PTB's `job_queue`.

**Two parallel input paths**:
1. **Free-text submissions** (group or DM) → `handlers.on_text` → `parser.parse_shift` → DB. Regex-first; falls back to an OpenAI call only when day(s) appear but no time range was found and the message looks shift-related (`parser._looks_shift_related`). The bot reacts with 👍 in groups and replies with a summary in DMs.
2. **Inline-button wizard** (DM only, after `/start`) → multi-step flow tracked in `context.user_data`:
   `onboarding_step` (name → employee_id) → `selected_days` (day picker) → `shift_mode` per day (`10h` auto-advances after arrival; `specific` asks arrival then departure) → `time_chosen` → final 40-hour validation → saved as a submission. State is **in-memory per user** — a bot restart wipes any in-progress wizard.

Both paths converge on `db.add_submission`; **the latest submission per (user, week) wins** when the report is built (`db.latest_submissions_for_week` uses `MAX(id) … GROUP BY user_id`). Don't add deletion or "edit" logic — just append a new submission.

**Scheduling** (`bot/scheduler.py`): four jobs run weekly via PTB's APScheduler-backed `job_queue`. Note PTB's weekday numbering is **Mon=0..Sun=6** (differs from Python's `datetime.weekday()` which also uses Mon=0 but differs from many calendars). `JOBS_BY_NAME` is the registry used by both `/remind` and `register_jobs`.

**Week math** (`bot/week.py`): `upcoming_week()` returns the next Sun–Sat block (or the current one if invoked on a Sunday). `Week.id` is `YYYYMMDD` of the Sunday. Day keys are lowercase English (`sunday..saturday`) everywhere internally; Hebrew display labels come from `DAY_LABEL_HE`. If you touch parsing or reports, keep this key convention.

**DB** (`bot/db.py`): schema is created via `CREATE TABLE IF NOT EXISTS` in `init()`. **Adding columns**: append to the `SCHEMA` string *and* add an idempotent `ALTER TABLE ... ADD COLUMN` block inside `init()` (see the `display_name`/`employee_id` precedent) — production rows pre-date new columns. `submissions.parsed_json` is the canonical shift list (`[{"day": "...", "time_range": "..."}]`); `raw_text` is kept for debugging only.

**Telegram quirks**:
- Bot privacy mode must be disabled in BotFather, otherwise the group `MessageHandler` only sees commands.
- `TELEGRAM_CHAT_ID` is the single configured group; messages from other groups are ignored by `on_text`.
- The picker's `CallbackQueryHandler` pattern is a single regex covering all prefixes (`day:|picker:|mode:|arr:|dep:`). When adding a new wizard step, extend that pattern in `main.py`.

## LLM integration

`parser.parse_with_llm` is a **fallback**, not the primary path. It loads the `openai` SDK lazily inside the function so the bot runs without it installed, and it returns `None` on any parsing/API failure (the regex path then wins). When switching providers or models, only this function and `config.OPENAI_API_KEY`/`OPENAI_MODEL` need to change.

---

## Behavioral guidelines

These reduce common LLM coding mistakes in this repo. They bias toward caution over speed — use judgment on trivial tasks.

### 1. Think before coding

Don't assume. Don't hide confusion. Surface tradeoffs.

- State assumptions explicitly. If uncertain, ask.
- If multiple interpretations exist, present them — don't pick silently.
- If a simpler approach exists, say so. Push back when warranted.
- If something is unclear, stop. Name what's confusing. Ask.

### 2. Simplicity first

Minimum code that solves the problem. Nothing speculative.

- No features beyond what was asked.
- No abstractions for single-use code.
- No "flexibility" or "configurability" that wasn't requested.
- No error handling for impossible scenarios.
- If you wrote 200 lines and it could be 50, rewrite it.

### 3. Surgical changes

Touch only what you must. Clean up only your own mess.

- Don't "improve" adjacent code, comments, or formatting.
- Don't refactor things that aren't broken.
- Match existing style, even if you'd do it differently.
- If you notice unrelated dead code, mention it — don't delete it.
- Remove imports/variables/functions that *your* changes orphaned; leave pre-existing dead code alone unless asked.

Every changed line should trace directly to the user's request.

### 4. Goal-driven execution

Define success criteria. Loop until verified.

- "Add validation" → "Write tests for invalid inputs, then make them pass."
- "Fix the bug" → "Write a test that reproduces it, then make it pass."
- "Refactor X" → "Ensure tests pass before and after."

For multi-step tasks, state a brief plan: step → verify, step → verify.
