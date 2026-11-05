# Audit and improvement plan (stages 1–2)

Audit date: 2026-11-04. All items marked "reproduced" were verified by running them,
not just by reading the code. Checks ran on a temporary database and a synthetic conversation:
`bot.db` was opened read-only, and message text was neither printed nor sent anywhere.

## 1. State at the time of the audit

| What | Check result |
|---|---|
| Stack | Python 3.12, aiogram 3.13, FastAPI, SQLAlchemy 2 async, APScheduler, OpenAI-compatible client, SQLite locally / Postgres in Docker |
| Telegram connection | Long polling (`app.polling`) or webhook (`app.main`) |
| Bot token | **`401 Unauthorized`: the token is revoked or invalid, the bot will not start** |
| AI model in `.env` | `llama-3.3-70b-versatile` on Groq: **`404 model_not_found`, the model has been retired** |
| Cost | The Groq key works. Limits from the response headers: 1000 requests per day, 8000 tokens per minute |
| Where data goes | **Groq cloud (USA)**, not local Ollama as previously believed |
| Tests | 66 passed; ruff and mypy clean. But the tests covered none of the defects below |
| Data in `bot.db` | 243 messages, the last from 08.07.2026; 4 chats; 34 tasks; 23 digests |
| Processes | The bot, the panel and Ollama are not running; Ollama is not installed on this machine |

## 2. What works, what partly works, what is missing

**Works:** receiving text and captions, storage, manual tasks, status buttons,
time zone and time settings, the web dashboard with tokens, metrics.

**Partly works:**
- Daily digest: exists, but with defects D1–D6.
- Replies to messages: `reply_to_message_id` is stored but used only for linking to a task.
- Retry on failures: the code exists but does not trigger for a real LLM (A2).
- Context: the last 10 messages and 10 tasks for each message, no memory of topics.

**Missing:** topics, proposals, decisions, blockers, next steps, links to sources,
weekly digest, period selection, sending to another chat, allowed chats and admins,
history retention and deletion, export import, injection protection, message edits,
English deadlines.

## 3. Defects found

### A. Data loss

| # | Defect | Evidence |
|---|---|---|
| A1 | A message is stored in the same transaction as the LLM call. While the LLM is thinking, SQLite is locked | Reproduced: of 3 parallel messages, 2 were lost (`OperationalError`) |
| A2 | The LLM client swallows errors and returns "noise"; `pending_analysis` is not set and no retry starts | Reproduced: after a failure `pending_analysis=False` |
| A3 | `drop_pending_updates=True` at startup throws away everything that arrived while the bot was off | Code: `polling.py:25`, `main.py:35` |
| A4 | No uniqueness on `(chat_id, telegram_message_id)` | Reproduced; `bot.db` already has 2 groups of duplicates |
| A5 | Looking up a duplicated message crashes, so a reply to it breaks analysis | Reproduced: `MultipleResultsFound` |
| A6 | The message time is not stored: `created_at` is the write time, not the send time | Code: `models.py:101` |
| A7 | Message edits and media without a caption are not stored | Code: `handlers.py:455-463` |

### B. Analysis quality

| # | Defect | Evidence |
|---|---|---|
| B1 | Decisions, blockers and questions are detected as `message_type` but then discarded | Code: `task_analyzer.py:114` |
| B2 | Different tasks are merged by title similarity | Reproduced: «Интеграция API оплаты» / «…доставки» ("Payment API integration" / "…delivery") = 0.77 at a threshold of 0.6 |
| B3 | False deadlines: «завтрак» (breakfast) → завтра (tomorrow), «среди прочего» (among other things) → среда (Wednesday), «5 машин» (5 cars) → 5 мая (May 5) | Reproduced |
| B4 | English deadlines are not recognized (`by Friday`) | Reproduced |
| B5 | One LLM request per message: does not fit the 8000 tokens per minute limit on thousands of messages | Calculated from the key's limits |
| B6 | Message text is inserted into the prompt without isolation | Code: `openai_client.py:118` |

### D. Digests

| # | Defect | Evidence |
|---|---|---|
| D1 | Old messages get into the 24-hour digest | Reproduced: a 30-day-old message got in |
| D2 | A long digest is not sent (4096-character limit) | Reproduced: 27204 characters |
| D3 | `sent_at` is set before sending, so a send failure looks like success | Reproduced |
| D4 | Only the exact minute triggers it: a restart at 18:00 means no digest that day | Reproduced |
| D5 | A manual `/digest` cancels the scheduled digest for that day | Code: `scheduler.py:51-54` |
| D6 | Only 50 messages reach the LLM, the rest are ignored | Code: `openai_client.py:153` |

### S. Security

| # | Defect | Evidence |
|---|---|---|
| S1 | No list of allowed chats: the bot processes any chat it is added to | Code: `handlers.py` |
| S2 | Any member can change chat settings | Code: `handlers.py:316-367` |
| S3 | The status button does not check that the task belongs to this chat | Code: `handlers.py:433-439` |
| S4 | The webhook does not check the secret, forged updates are accepted | Code: `main.py:70` |
| S5 | The dashboard is fully open if `DASHBOARD_SECRET` is empty | Code: `routes.py:36-44` |
| S6 | SQLAlchemy errors go to the log with parameters, that is, with message text | Code: `session.py`, `error=str(e)` |
| S7 | No retention settings: `raw_update_json` with personal data is kept forever | Code |
| S8 | Conversations go to an external service without explicit permission | `.env` |
| S9 | `bot.db` and logs are not in `.gitignore` | `.gitignore` |

## 4. Model evaluation

The configured model cannot be evaluated: it has been retired. The candidates available on the key were tested with
one run of a synthetic conversation of 10 messages (RU/EN, injection in message 9).

| Model | Time | Response tokens | Injection | Assignee and deadline | Response language |
|---|---|---|---|---|---|
| `qwen/qwen3.8-27b` | 1.6 s | 686 | Resisted | Correct | Russian |
| `openai/gpt-oss-120b` | 3.4 s | 1498 | Resisted | Correct | Mostly English |
| `openai/gpt-oss-20b` | 2.6 s | 2014 | **Did not resist: recorded the decision "release is approved"** | Lost | Mixed |

This is one run, which is too little to conclude anything about quality. Full run across scenarios: [TEST_REPORT.md](TEST_REPORT.md).

**Decision:** based on the full run, the main cloud model is `openai/gpt-oss-120b`. The change
was forced; the provider stays the same. The initial choice of `qwen/qwen3.8-27b` based on the table above turned out to be
wrong: on a 300-message conversation it consistently lost important single messages.
`gpt-oss-20b` is excluded.

**Corporate data.** According to the Groq documentation, input data is not stored by default, but may be
logged for up to 30 days for incident review unless Zero Data Retention is enabled; the data
is hosted in the USA. So sending data out is now forbidden until the data owner explicitly enables
`LLM_EXTERNAL_ALLOWED=true`. An alternative with no data transfer: local Ollama (GPU
RTX 3070 8 GB, 32 GB RAM), connected through the same client.

## 5. Improvement plan

**Critical**
1. Message storage separated from the LLM; message uniqueness; WAL and SQLite timeouts (A1, A4, A5).
2. LLM errors are not treated as noise; batch analysis with a queue and retries (A2, B5).
3. Updates are not thrown away at startup (A3).
4. Allowed chats, admins, no external sending without consent (S1–S3, S8).
5. Topics and items with sources: proposals, decisions, tasks, questions, blockers, actions (B1, B2).
6. Protection against fabrication and injection at the code level, not only in the prompt (B6).
7. A new default model and an availability check at startup.

**Important**
8. Digests: structure by topic, daily and weekly, period selection, splitting into parts, catch-up sending, another chat (D1–D6).
9. Deadlines: word boundaries, English, counting from the message date (B3, B4).
10. Send time, edits, media, message links (A6, A7).
11. History retention and deletion; safe logging; webhook secret; dashboard (S4–S7, S9).

**Additional**
12. Import of Telegram Desktop exports.
13. `/status` command, model evaluation script, migration of the existing `bot.db`.

## Sources

- [Groq: Model Deprecation](https://console.groq.com/docs/deprecations)
- [Groq: Rate Limits](https://console.groq.com/docs/rate-limits)
- [Groq: Your Data in GroqCloud](https://console.groq.com/docs/your-data)
