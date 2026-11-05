# BACKLOG — project tasks (what was requested and status)

> A list of all tasks and user requests with their status. **Before taking a task:**
> find it here, check its status, and look in [JOURNAL.md](JOURNAL.md): it may
> already have been tried (and there may be a reason it was done this way).
>
> Statuses: ✅ done · 🔨 in progress · 📋 planned · ❌ decided not to do.

---

## 🔨 In progress

### B-01. Direct notifications to the assignee
When a task is assigned to `@username`, send that person a **direct message**:
"🔔 You have been assigned a task: …".
- **Telegram limits:** the assignee is given as `@username`; they must write `/start` to the bot
  in a private chat once themselves (the bot does not write first, otherwise `TelegramForbiddenError`).
- **Plan:** `UserRepository.get_by_username`; `find_or_create_task` returns a "created"
  flag; when creating a task with `@assignee`, `task_analyzer` finds the user and returns
  the notification upward; `handle_message` sends it through `safe_send_message` (in try/except).
- **Where it is done:** on the **Windows PC** (the production code lives there). Do not duplicate on the Mac
  until we sync.

---

## ✅ Done

- **B-02. Collecting tasks from a chat without commands** — the bot passively reads all messages (needs
  Privacy Mode turned off for the bot) and extracts tasks itself; commands are needed only to
  view the result.
- **B-03. LLM analysis with an abstraction** — mock (by keywords), OpenAI-compatible
  (OpenAI / Groq / local Ollama), switched only through `.env`.
- **B-04. Telling personal from work** — a stronger prompt; heuristics in mock. Actually
  works on a proper model (Groq/Ollama). Mock is crude.
- **B-05. Clustering messages into tasks** — reply / related_task_id / title similarity.
- **B-06. Clarifying questions + anti-spam** — Redis cooldown with a database fallback; a reply to
  the bot's question closes the clarification and updates the task.
- **B-07. Per-chat daily digest** — by each chat's timezone and time; manual `/digest`.
- **B-08. Digest only in the selected chat** — the `chats.digest_enabled` field, commands
  `/digest_on` / `/digest_off`. Currently enabled only in **SAP СК**.
- **B-09. Button menu** instead of commands — 📋 Tasks / 📊 Digest / ❓ Clarifications / ⚙️ Settings.
- **B-10. Task status control with buttons** — 🔄 Start / ✔️ Done / ❌ Cancel / 🔄 Reopen.
- **B-11. "From" field (reporter)** — `tasks.reporter_text`, shown in the card and the digest.
- **B-12. Correct deadlines** — a deterministic date parser ([app/utils/dates.py](../app/utils/dates.py));
  the model is not trusted with dates; no deadline phrase means no deadline.
- **B-13. Chat settings via commands** — `/set_timezone`, `/set_digest_time`.
- **B-14. Media messages** — ingestion of captions for photos and documents.
- **B-15. Web dashboard and metrics** — `/dashboard`, `/api/*`, `/metrics` (Prometheus).
- **B-16. Resilience** — `pending_analysis` retry, graceful Redis, safe Markdown sending.
- **B-17. Local run and deployment** — polling, `init_db` for SQLite, `docker-compose.prod.yml`,
  `DEPLOY.md`, move to **Windows + Groq** (`WINDOWS_SETUP.md`).
- **B-26. Repository readiness** — `LICENSE` (MIT), GitHub Actions CI
  (`.github/workflows/ci.yml`: ruff + mypy + pytest on every push/PR), navigation in the README.
- **B-25. i18n (multiple languages)** — done on 2026-11-05: English by default, Russian optional.
  Previously rejected as overkill while the bot was Russian-only.

---

## 📋 Planned / ideas

- **B-18. Real 24/7 without my own computer** — deployment to a VPS/server (see [DEPLOY.md](../DEPLOY.md)).
  Postponed: for now the user keeps the bot on a Windows PC that stays on. With Ollama on the server,
  RAM ≥16 GB is needed; Groq is simpler.
- **B-19. Autostart when the computer boots** — launchd on the Mac was blocked by the environment; on
  Windows, Task Scheduler or a startup .bat; on a server, systemd/`restart: unless-stopped`.
- **B-20. Date accuracy** — extend the parser (fuller coverage of spelled-out months, «в начале/середине недели»
  ("at the start/middle of the week")), or use a larger model.
- **B-21. Chat history import** — the bot sees only messages after it was added. A one-off import
  from a Telegram Desktop JSON export through a separate script.
- **B-22. Forum group topic filter** — listen only to the work topic (`telegram_thread_id` is already in the database).
- **B-23. `task_min_confidence` threshold** — do not create a task when the model's confidence is low.

---

## ❌ Decided not to do (per the spec / deliberately)

- **B-24. Jira / Linear / YouTrack / GitHub Issues integrations** — the spec explicitly excludes them from the first stage.

---

## ✅ Done during the rework into Digest

- **B-20. Topic-based conversation analysis** — proposals, decisions, tasks, owners, deadlines, questions, blockers with links to messages.
- **B-21. Daily and weekly digests** — period of choice, sending to another chat, catch-up sending.
- **B-22. Allowed chats and admins, no external sending without consent, retention periods.**
- **B-23. Control panel** — check on a test conversation, model change, settings, chats.
- **B-24. Moving to a server** — archive (`python -m app.bundle`) and `install.sh`.
- **B-25. Import of Telegram Desktop exports.**

## 📋 Planned

- **B-30. First run on real Telegram and a server** — the Docker image and `install.sh` have not yet been run in full.
