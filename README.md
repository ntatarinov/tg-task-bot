# Digest: work chat analysis in Telegram

**English** | [Русский](README.ru.md)

![CI](https://github.com/ntatarinov/tg-task-bot/actions/workflows/ci.yml/badge.svg)
![Python](https://img.shields.io/badge/python-3.12-blue)
![License: MIT](https://img.shields.io/badge/license-MIT-green)

The bot reads allowed work chats, groups discussions by topic and sends daily and weekly digests:
what was discussed, what was decided, who is doing what and which questions are still open.

## What it does

- Stores messages from allowed chats: author, send time, replies, threads, forwards, edits, media.
- Understands Russian, English and mixed conversations.
- Merges messages on one topic, even when they were written at different times and in different languages.
- For each topic, extracts proposals, decisions, tasks, owners, deadlines, open questions, blockers and next steps.
- Keeps links to the source messages for every conclusion.
- Does not treat a proposal as a decision and does not invent assignees or deadlines: if the data is missing, it writes "Not defined".
- Produces reports on a schedule and on command for any period, in this chat or another allowed chat.

### What the bot cannot do

These are Telegram Bot API limits, not settings:

- **Old history is not available.** The bot sees only messages sent after it was added to the chat. Past messages can be loaded from a Telegram Desktop export (see section 8).
- **Messages from other bots** are not delivered.
- **Downtime longer than 24 hours.** Telegram keeps undelivered updates for about a day; older ones are lost.
- **Message deletion** is not tracked: Telegram does not report it to bots.
- **Message links** work only in supergroups. In regular groups the message number is shown instead of a link.

---

## Language

Bot messages, digests and the task site default to English. Russian is available.

- **Chat language.** `/language` (admin only) shows buttons; `/language ru` or `/language en` sets the language for that chat. The current value is shown in `/settings`.
- **Default.** `DEFAULT_LANGUAGE=en` in `.env` (`en` or `ru`) applies to every chat that has not picked its own language. It can also be changed in the panel.
- **Model output.** The chat language also sets the language of what the model writes: topic titles, summaries, items. Quotes stay in the original language. Text already stored is not rewritten when the language changes.
- **Messages and commands.** Russian and English chat messages are both understood whatever the chat language is. Commands accept Russian arguments too: `/set_weekly пт` or `/set_weekly fri`, `/digest вчера` or `/digest yesterday`.
- **Telegram menu.** Telegram's own command menu and bot description follow each user's Telegram app language: English by default, Russian for Russian clients.
- **Panel.** A language switch (English / Русский) sits on the login screen and in the header. English is the default, and the choice is remembered in the browser. Each chat card has a report language setting.
- **Task site.** It follows the chat language and has an EN / RU switch.
- **Command line.** Command-line tools and scripts (`install.sh`, the PowerShell scripts, `python -m app.bundle`) print English. `python -m app.doctor` follows `DEFAULT_LANGUAGE`.

---

## 1. Quick start (Windows, local SQLite)

```powershell
py -m venv .venv
.venv\Scripts\python.exe -m pip install -e ".[dev]"
copy .env.example .env      # then fill it in, see sections 2–4
scripts\start_local.ps1     # starts the model, updates the database, checks everything and starts the bot
```

`scripts\start_local.ps1` starts Ollama, updates the database (an existing `bot.db` is first
copied to `bot.db.bak-<date>`) and runs the check. If something is not ready, it stops and tells you what to fix.

To check everything without starting the bot, run `python -m app.doctor`. It reports OK or an error
with a hint for each item: token, group mode, chat list, database, model. It does not print secrets.

Detailed Windows instructions: [WINDOWS_SETUP.md](WINDOWS_SETUP.md). Server and Docker: [DEPLOY.md](DEPLOY.md).

### Control panel

```powershell
scripts\start_panel.ps1      # opens http://localhost:8090
```

It requires a password in `ADMIN_PANEL_PASSWORD`. On a server the panel is opened through an SSH tunnel, see [DEPLOY.md](DEPLOY.md).

| Section | What is there |
|---|---|
| Summary | Decisions, tasks, blockers and questions for a chat, a report for a period. If the instance is not set up, a list of steps is shown at the top |
| 1. Setup | Token, model and chats; other parameters are collapsed |
| 2. Check | A check of the whole system with one button and analysis of a test conversation. Telegram is not needed and the working database is not touched |
| 3. Move | Download an archive for another instance, and the install commands |

### Test chat first, then the work chat

1. Create a test group, add the bot and write any message.
2. In the **Setup** section the group appears as pending. Click **Allow** and restart the bot.
3. Check how it works in the test group.
4. When you are satisfied, add the bot to the work group and allow it the same way.
5. For the test group, click **Block** and **Clear chat data**.

Chats are isolated: each has its own topics, tasks and reports.

The **Where is the bot** button asks Telegram about each known chat. Telegram does not provide a full list
of a bot's chats, so a new group appears once the bot is running and has been added there. If it was added while
the bot had been off for more than a day, remove it from the group and add it again.

### Changing the model

1. In the **Check** section, pick a model and run it on the test conversation. Models can be compared.
2. In the **Setup** section, click **Select** and restart the bot.

Any service with an OpenAI-compatible API works: it can be added in the **Setup** section.
An external service must be confirmed separately: the conversations will leave the server.

### Moving to a server

First get everything working on this computer, then move it in one archive. You can download it
in the panel in the **Move** section or build it with this command:

```powershell
.venv\Scripts\python.exe -m app.bundle        # builds digest-bot-<date>.zip
```

The archive contains the code, the settings (`.env`) and a copy of the database. **It includes the token and all conversations**: send it only
over a secure channel and delete it after installing. For code without data: `python -m app.bundle --code-only`.

On the server (Linux, 32 GB RAM or more):

```bash
unzip digest-bot-*.zip -d digest-bot && cd digest-bot
sh install.sh           # bot, panel and model on this server
sh install.sh cloud     # bot and panel, model in the cloud (requires LLM_EXTERNAL_ALLOWED=true)
```

The script installs Docker, detects an NVIDIA GPU, downloads the model, creates the panel password, updates the database and runs the check at the end.
Before starting the bot on the server, stop it on this computer: two bots cannot run with one token.

---

## 2. Connecting the Telegram bot

1. Get a token from [@BotFather](https://t.me/BotFather) and put it in `BOT_TOKEN`.
   For an existing bot: `/mybots` → bot → **API Token**.
2. Turn off Privacy Mode, otherwise the bot sees only commands: `/mybots` → bot → **Bot Settings** → **Group Privacy** → **Turn off**.
3. Add the bot to the group. If Privacy Mode was changed while the bot was already in the group, remove it and add it again.
4. Get your Telegram ID from [@userinfobot](https://t.me/userinfobot) and put it in `ADMIN_USER_IDS`.
5. Start the bot and send `/chatid` in the chat you want. Put the reply in `ALLOWED_CHAT_IDS` and restart the bot.

```
ADMIN_USER_IDS=123456789
ALLOWED_CHAT_IDS=-1001234567890,-1009876543210
```

**While `ALLOWED_CHAT_IDS` is empty, the bot ignores all chats.** This is deliberate: a bot added to someone
else's group by accident stores nothing.

---

## 3. Setting up the AI model

| Option | Where the data is processed | Setup |
|---|---|---|
| Groq (cloud, free tier) | USA, third-party service | Below |
| Ollama (local) | On your computer | Below |
| `mock` | Nowhere | Development only, does not understand meaning |

**Groq:**
```
LLM_PROVIDER=openai
OPENAI_BASE_URL=https://api.groq.com/openai/v1
OPENAI_API_KEY=gsk_...
OPENAI_MODEL=openai/gpt-oss-120b
LLM_EXTERNAL_ALLOWED=true
```

> ⚠️ `LLM_EXTERNAL_ALLOWED=true` sends conversation text to a third-party service. Enable it only with
> the data owner's permission. While the value is `false`, messages are stored and wait in the queue, nothing
> leaves the machine, and `/status` shows the reason for the pause.
>
> According to the [Groq documentation](https://console.groq.com/docs/your-data), requests are not stored by default,
> but may be logged for up to 30 days for incident review. To prevent this, enable
> Zero Data Retention in the Data Controls section of the Groq console.

**Ollama:**
```
LLM_PROVIDER=openai
OPENAI_BASE_URL=http://localhost:11434/v1
OPENAI_API_KEY=ollama
OPENAI_MODEL=gemma4:26b
```

Check the quality of a local model before using it. A model change is always checked the same way:

```powershell
.venv\Scripts\python.exe scripts\eval_llm.py --model gemma4:26b --pause 0 --large 1
```

Always include the large scenario (`--large`): `qwen/qwen3.8-27b` passes short conversations
but loses individual important messages among the chatter. Running the bot together with the model on a rented
server is described in [DEPLOY.md](DEPLOY.md).

The script sends only synthetic conversations and does not read the bot's database.
Latest results: [docs/TEST_REPORT.md](docs/TEST_REPORT.md).

Groq retires models from time to time. At startup the bot checks the model and writes
`startup.model_unavailable` to the log if it is missing. If the model becomes unavailable while running, messages
stay in the queue and are processed after the model is changed.

---

## 4. Report schedule

By default the daily digest arrives at 18:00 and the weekly one on Fridays, time zone Asia/Almaty.
Settings are stored separately for each chat.

| Command | What it does |
|---|---|
| `/settings` | Shows the settings, including the chat language, and buttons for 18:00, 20:00, 21:00, 23:00 |
| `/set_digest_time 19:30` | Custom time |
| `/set_timezone Europe/Moscow` | Time zone |
| `/set_weekly fri` | Day of the weekly report (`mon`…`sun`, also `пн`…`вс`, for example `/set_weekly пт`) or `off` |
| `/set_digest_chat -100…` | Send reports to another allowed chat (`here` returns them to this one) |
| `/digest_on`, `/digest_off` | Turn reports on or off |

If the bot was off at send time, the report goes out within `DIGEST_CATCHUP_HOURS` (6 hours).
A daily report covers the time since the end of the previous one, so there are no gaps between reports.

### Report on command

| Command | Period |
|---|---|
| `/digest` | Last 24 hours |
| `/digest today` | Since midnight in the chat's time zone |
| `/digest yesterday` | Yesterday |
| `/digest week` | Last 7 days |
| `/digest 3d` | Last N days |
| `/digest 2026-11-01` | One day (also `01.11.2026`) |
| `/digest 2026-11-01 2026-11-03` | Date range |

Period words can also be given in Russian: `/digest вчера`.

A manual report does not cancel the scheduled report.

---

## 5. All commands

**For all members of an allowed chat:** `/start`, `/help`, `/tasks`, `/task <text>`, `/clarify`,
`/settings`, `/digest`, `/status`, `/chatid`.

**Only for `ADMIN_USER_IDS`:** `/set_timezone`, `/set_digest_time`, `/set_weekly`, `/set_digest_chat`,
`/digest_on`, `/digest_off`, `/language`, `/reanalyze <days>`, `/purge_history <days> confirm`, `/pin_dashboard`.

`/language ru` or `/language en` sets the chat language; without an argument it shows buttons.

`/reanalyze 30` queues messages from the last 30 days for re-analysis. The same way you can process
messages stored by an old version of the bot. Note that they will be sent to the model.

---

## 6. How it works

```
Telegram ─→ AccessMiddleware ─→ MessageIngestor ─→ DB (pending_analysis queue)
            drops other         only stores,
            chats               no model call

APScheduler, every minute
 ├─ analysis_tick ─→ ConversationAnalyzer ─→ model (one request per batch)
 │                    └─ response checks ─→ topics, items, sources, tasks
 ├─ digest_tick ───→ DigestService ─→ report from verified data ─→ Telegram
 └─ retention_tick ─→ deletion by retention period
```

**Storage is separate from analysis.** A slow or unavailable model cannot lose a message.

**Batch analysis.** The model receives new messages and a short memory of the topics, not the whole history,
so the request size does not grow with the conversation. This also matters for limits: the free
tier allows about 8000 tokens per minute and 1000 requests per day.

**The model is not taken at its word.** Everything it returns is checked by code:

| Check | What happens |
|---|---|
| Source | An item without existing source messages is discarded |
| Decision | Without a verbatim quote from the source, or with wording like «предлагаю» ("I suggest") or «обсудим» ("let's discuss"), it is downgraded to a proposal |
| Assignee | Kept only if named in the sources or if that person wrote the message |
| Deadline | Computed by code from the message text, relative to the message date, not today |
| Project | Different projects are not merged, even if the model linked them |
| Injections | Messages like «игнорируй инструкции» ("ignore the instructions") are flagged, and conclusions based only on them are discarded |

The "who will take this task" question comes from a template: the model does not decide what the bot writes to the chat.
Commands in message text are not executed: the bot does not base any actions on them at all.

**What happens on failures:**

| Failure | Behavior |
|---|---|
| Model unavailable | Messages wait in the queue, attempts are not used up |
| Model returned garbage | The batch is halved; after `ANALYSIS_MAX_ATTEMPTS` the messages are set aside and the report says so |
| Telegram rejects the report | The report is not counted as sent; a retry continues from the unsent part |
| Message delivered again | A unique key (chat, message number) prevents a duplicate |
| Process crash | A batch is applied in one transaction: it is either written in full or processed again |

If some messages could not be processed in time, the report says so directly.

---

## 7. Security and retention

- Tokens and keys are stored only in `.env`, which is not committed to git. They are masked in logs.
- Message text is not written to logs, and database errors do not include query parameters.
- Access is limited by `ALLOWED_CHAT_IDS` and `ADMIN_USER_IDS`.
- Without a secret, the task site and the webhook are closed everywhere except `APP_ENV=local`.

Retention periods (0 means keep forever, which is the default):

```
MESSAGE_RETENTION_DAYS=90       # delete messages older than 90 days
RAW_UPDATE_RETENTION_DAYS=7     # delete raw Telegram data older than 7 days
```

> ⚠️ After `MESSAGE_RETENTION_DAYS` is enabled, old messages are deleted the next time the job
> runs (once an hour). Conclusions and links to the source messages in Telegram remain.

`/purge_history 30 confirm` deletes the chat's messages older than 30 days. `/purge_history 0 confirm`
deletes the chat's entire history together with topics and reports; tasks remain. Deletion is irreversible.

---

## 8. Importing old history

1. In Telegram Desktop: chat menu → **Export chat history** → format **JSON**.
2. Load the file:

```powershell
.venv\Scripts\python.exe -m app.importer "C:\path\to\result.json" --chat-id -1001234567890
```

The chat must be in `ALLOWED_CHAT_IDS`. A repeated import creates no duplicates. Imported messages
go through the analysis queue and are therefore sent to the model. With the `--no-analyze` flag they are only stored.
The export file contains the entire conversation: delete it after loading.

---

## 9. Verification

```powershell
.venv\Scripts\python.exe -m pytest                    # automated tests
.venv\Scripts\python.exe -m ruff check app tests scripts
.venv\Scripts\python.exe -m mypy app
.venv\Scripts\python.exe scripts\e2e_fake_telegram.py # real bot process and a fake Telegram
.venv\Scripts\python.exe scripts\eval_llm.py --runs 2 --large 3   # model quality, needs a key
```

Reports: [docs/AUDIT.md](docs/AUDIT.md) (audit and plan), [docs/TEST_REPORT.md](docs/TEST_REPORT.md) (results).
Decision history: [docs/BACKLOG.md](docs/BACKLOG.md) and [docs/JOURNAL.md](docs/JOURNAL.md).

---

## 10. Task site and metrics

| Address | Purpose |
|---|---|
| `GET /dashboard/{chat_id}?token=` | Tasks of one chat with status buttons |
| `GET /dashboard?token=` | Overview of all chats |
| `GET /api/chats?token=`, `GET /api/chats/{id}/tasks?token=` | JSON API |
| `GET /metrics` | Prometheus metrics |

The task site works when `app.main` is running. Alongside `app.polling`, start it with
`ENABLE_SCHEDULER=false`, otherwise reports are sent twice. Setting up external access is described in [WINDOWS_SETUP.md](WINDOWS_SETUP.md).

The task site follows the chat language and has an EN / RU switch.

All parameters, with comments, are listed in [.env.example](.env.example).
