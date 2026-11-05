# JOURNAL — work history, attempts and decisions

> A chronology of what has already been done, which approaches **did not work** and why.
> Read it before a task so you do not repeat the same mistakes. Append new notable decisions
> here in order, top to bottom (at the end).

Entry format: **what was done → problem → solution / what did NOT work**.

---

## Phase 1. Audit of the base MVP and finishing it

The base skeleton (models, repositories, mock LLM, commands) existed. Finished:
- Redis client with graceful degradation (falls back to the database when unavailable).
- The scheduler was rewritten **per-chat** (a tick once a minute, checking local time == the chat's digest_time) + a retry job for `pending_analysis`.
- Clarification reply flow: added `clarifications.question_message_id` (migration 0002),
  a correct `handle_possible_answer` (reply to the bot's question → close the clarification, update the task).
- **Bug:** `TaskMessageRepository.link` consumed `Result` twice (`scalar_one_or_none()` then
  `scalar_one()`) → fixed.
- `safe_send_message`/`safe_answer` — fallback to plain text on a Markdown error.

## Phase 2. Roadmap features

- Ingestion of media captions (`message.caption`).
- Commands `/set_timezone`, `/set_digest_time` (+ tz/HH:MM validators).
- Inline task status buttons (`on_task_action`, state machine).
- REST API (`/api/chats`, `/api/chats/{id}/tasks`) + HTML `/dashboard`.
- Prometheus `/metrics` (graceful: no-op if the library is not installed).
- **Deliberately NOT done** (per the spec): Jira/Linear/GitHub Issues integrations; i18n.

## Phase 3. First live run (local, Mac)

- Created `.env` and `app/init_db.py` (create_all for SQLite).
- **Problem:** `create_async_engine(..., pool_size=..., max_overflow=...)` crashed on SQLite
  (`TypeError`). → Pool parameters are now only for non-SQLite (`app/db/session.py`).
- **`ruff --fix` broke `models.py`:** it rewrote `Mapped[Optional[str]]` → `Mapped[str | None]`,
  which crashes SQLAlchemy on Python 3.9 (PEP 604 at runtime is 3.10+ only). → Rolled back to
  `Optional`/`List` + `per-file-ignores` in `pyproject.toml`. **Do not repeat.**

## Phase 4. Quick fixes right after the run

- **`/help` was silent:** the text contained `/set_timezone` and `/set_digest_time`; Telegram Markdown
  took the `_` character for italics, "can't find end of entity", and the whole message
  was rejected. → `/help` and `/start` are sent as plain text / through `safe_answer`.
- The menu was moved to **inline buttons** (`_main_menu_keyboard`, `on_menu`, `_send_*` extracted).

## Phase 5. Local LLM (Ollama) instead of mock

- Installed Ollama (brew), model `qwen2.5:7b`. `.env` → OpenAI-compatible
  `http://localhost:11434/v1`. The same `OpenAILLMClient`.
- Strengthened the system prompt: explicitly "ignore personal/household/humor/off-topic".
- **Problem:** local models wrap JSON in ``` blocks or add text. →
  `_extract_json` (strips the wrappers, takes the outer `{...}`).
- **Problem:** the model sent `"priority": null` → Pydantic rejected the whole response. →
  `MessageAnalysis` got normalizing validators (null/invalid → default, not a crash).
  ⚠️ This deliberately changed the contract: the schema no longer raises ValidationError on
  an invalid priority/confidence, it normalizes them.

## Phase 6. Dates (important!)

- **Problem:** the model set deadlines with a past year (`2023-...`) because it does not know "today".
- Attempt 1: passed the current date into the prompt (`_today_str`). **Did NOT help reliably**: the 7B model
  ignored it.
- **Problem 2:** the model invented deadlines that were not in the message at all.
- **Solution:** stopped trusting the model with dates. `app/utils/dates.py` is a deterministic
  parser of Russian expressions («до среды» (by Wednesday), «к 5 июля» (by July 5), «через 3 дня» (in 3 days),
  «до конца недели» (by the end of the week), «DD.MM») relative to the real "today". A deadline is taken **only** from explicit text; no phrase
  means no deadline. Wired into `task_analyzer` (`normalize_deadline`).

## Phase 7. "From" field (reporter)

- `tasks.reporter_text` (model + migration 0003 + `ALTER TABLE` on the live `bot.db`).
- Filled with the message author (`_user_label`), for `/task` with the command author.
  Shown in `/tasks`, the digest and the API.

## Phase 8. Autonomous running on the Mac

- **Goal:** the bot keeps working when the laptop is left alone.
- **launchd attempt** (`~/Library/LaunchAgents/*.plist`) was **blocked** by the environment's
  protection as "unauthorized persistence". → Gave up on autostart.
- **Solution:** started `ollama serve`, `app.polling` and `caffeinate -dis` as detached
  background processes (`nohup`). `caffeinate` keeps the Mac from sleeping with the lid open.
  The limitation was stated openly: it survives sleep but not a reboot; a closed lid means sleep.

## Phase 9. Digest in only one chat

- User: the digest goes everywhere (private chat + 2 groups), it is only needed in "SAP СК".
- Added the `chats.digest_enabled` field (migration 0004 + `ALTER` on `bot.db`), a filter in
  `scheduler.digest_tick`, commands `/digest_on` / `/digest_off`, display in `/settings`.
- Set in the database: `digest_enabled=1` only for SAP СК (tg_id `-5277304015`), `0` for the others.

## Phase 10. Move to Windows + Groq

- Reason: the Mac was overheating from the local model. Solution: cloud **Groq** (the computer does no computing
  and does not heat up). The same `OpenAILLMClient`, only `.env` changes.
- Built `tg-task-bot.zip` (without `.venv`/caches/logs, with `bot.db` and `.env`) and `WINDOWS_SETUP.md`.
- **`aiosqlite` was moved from dev to the main dependencies** (needed to run on SQLite).
- A series of clean-install problems on Windows (important for the future):
  1. **hatchling** did not find the package (project name `tg_task_bot` ≠ folder `app`) →
     `[tool.hatch.build.targets.wheel] packages = ["app"]`.
  2. **`psycopg2-binary`** required a compiler and is not needed → **removed** (we use asyncpg/aiosqlite).
  3. **Version conflict:** `pydantic==2.10.3` is incompatible with `aiogram 3.13.1` → pinned to `==2.9.2`.
  4. The user installed **Python 3.14**: there are no prebuilt wheels for it and a compiler was required
     → installed **Python 3.12** (direct link to 3.12.8).
  5. **`greenlet==3.2.5`** was compiling on Windows → replaced with **`greenlet==3.1.1`** (has a wheel).
- Result: the bot runs successfully on Windows through Groq.
- **Delivery lesson:** the production code now lives on the Windows PC, separate from the Mac. New
  changes are made on the computer where the bot runs
  (see BACKLOG, "direct notifications to the assignee").

## Phase 11. Direct notifications to the assignee (in progress)

- Request: when a task is assigned to `@username`, send that person a direct message.
- Telegram limits: the assignee must be given as `@username` and must write `/start` to the bot
  in a private chat once themselves (the bot does not write first).
- Being implemented on the Windows PC where the production bot runs. Status is in BACKLOG.

---

## Phase: rework into Digest

The bot was renamed from "Tudushka" (Тудушка) to Digest and rebuilt from task extraction into topic-based conversation analysis.
Details: [AUDIT.md](AUDIT.md) and [TEST_REPORT.md](TEST_REPORT.md).

- Analysis one message at a time → **problem:** free tier limits and message loss with a slow model. **Solution:** a message is stored immediately, and analysis runs in batches from a queue (`conversation_analyzer.py`). `task_analyzer.py` was removed.
- The model's response cannot be taken at its word → decisions, assignees, deadlines and item closures are checked by code against the source messages.
- **Did not work:** choosing `qwen3.8-27b` from one run. The model is good on a short conversation, but on 300 messages it consistently lost important single messages. A model is chosen only with the large scenario.
- **Did not work:** `gemma4` with default settings. Reasoning eats up the response limit. `reasoning_effort: none` is needed, and Ollama ignores `think=false` through `/v1`.
- **Did not work:** closing items without evidence. The model closed live blockers and decisions. Closing requires a quote from another message, and a decision can only be replaced.
- **Did not work:** text similarity that ignores numbers: «релиз 10 ноября» ("release on November 10") and «релиз 17 ноября» ("release on November 17") were counted as duplicates.
- On Windows, `write_text` turns line endings into CRLF and breaks `install.sh`. Fixed in `.gitattributes`.

## Phase: English by default (2026-11-05)

- Owner request: English by default → bot messages, digests, the task site, the panel and the docs are now English by default, with Russian as an option (`/language` per chat, `DEFAULT_LANGUAGE` for the rest). This reverses the Phase 2 decision not to do i18n; see BACKLOG B-25.
- The chat language also sets the language of what the model writes. Quotes stay in the original language, and text already stored is not rewritten when the language changes. Russian and English messages and command arguments are understood whatever the chat language is.
- User-facing docs have `.ru.md` copies; internal docs in `docs/` are English only.
- **Not evaluated:** the English model output mode has not been evaluated on a real model yet. All results in [TEST_REPORT.md](TEST_REPORT.md) were measured with Russian output.
