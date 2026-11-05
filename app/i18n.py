"""
Everything the bot, the digest and the panel's server side say, in every language they speak.

Only output lives here. What the bot understands (Russian and English dates, confirmations,
command arguments) does not depend on the language it answers in. The panel's own page text
is in panel_static/i18n.js.
"""
from __future__ import annotations

from contextvars import ContextVar
from typing import Any

from app.config import settings

LANGUAGES = ("en", "ru")
FALLBACK_LANGUAGE = "en"
LANGUAGE_NAMES = {"en": "English", "ru": "Русский"}

# The panel's language for the current request. The bot never uses this: a chat's language
# is always passed explicitly, because replies and scheduled digests have no request around them.
_current: ContextVar[str | None] = ContextVar("ui_language", default=None)


def normalize(language: str | None) -> str:
    """Unset or unknown means the configured default, and English if that is wrong too."""
    for candidate in (language, settings.default_language):
        if candidate in LANGUAGES:
            return candidate  # type: ignore[return-value]
    return FALLBACK_LANGUAGE


def chat_language(chat: Any) -> str:
    return normalize(chat.language)


def use_language(language: str | None) -> None:
    _current.set(language if language in LANGUAGES else None)


def current_language() -> str:
    return normalize(_current.get())


def t(language: str | None, key: str, /, **values: Any) -> str:
    text = MESSAGES[normalize(language)][key]
    return text.format(**values) if values else text


def tr(key: str, /, **values: Any) -> str:
    """t() in the current request's language."""
    return t(_current.get(), key, **values)


def has_text(key: str) -> bool:
    return key in MESSAGES[FALLBACK_LANGUAGE]


EN: dict[str, str] = {
    # Telegram's own menu and profile
    "cmd.start": "What this bot does",
    "cmd.help": "List of commands",
    "cmd.tasks": "Show active tasks",
    "cmd.digest": "Digest: /digest [today|yesterday|week|3d|date date]",
    "cmd.task": "Create a task by hand: /task <text>",
    "cmd.clarify": "Tasks that need clarification",
    "cmd.settings": "Chat settings",
    "cmd.status": "Analysis status",
    "cmd.chatid": "Show the chat ID (for admins)",
    "cmd.set_timezone": "Time zone: /set_timezone <TZ>",
    "cmd.set_digest_time": "Digest time: /set_digest_time <HH:MM>",
    "cmd.set_weekly": "Weekly digest: /set_weekly <mon..sun|off>",
    "cmd.set_digest_chat": "Where to send reports: /set_digest_chat <id|here>",
    "cmd.digest_on": "Turn the digest on in this chat",
    "cmd.digest_off": "Turn the digest off in this chat",
    "cmd.language": "Chat language: /language <en|ru>",
    "cmd.reanalyze": "Repeat the analysis: /reanalyze <days>",
    "cmd.purge_history": "Delete history: /purge_history <days> confirm",
    "cmd.pin_dashboard": "Pin a link to the task site",
    "bot.short_description": "Reads a work chat and sends digests of topics, decisions and tasks.",
    "bot.description": (
        "Digest reads a work chat, groups the discussion by topic, picks out decisions and tasks, "
        "and sends a daily and weekly digest."
    ),
    # Task cards
    "status.new": "🆕 New",
    "status.needs_clarification": "❓ Needs clarification",
    "status.ready": "✅ Ready",
    "status.in_progress": "🔄 In progress",
    "status.done": "✔️ Done",
    "status.cancelled": "❌ Cancelled",
    "task.reporter": "🙋 From: {name}",
    "task.assignee": "👤 Assignee: {name}",
    "button.reopen": "🔄 Reopen",
    "button.progress": "🔄 Start",
    "button.done": "✔️ Done",
    "button.cancel": "❌ Cancel",
    # Menu
    "menu.tasks": "📋 Tasks",
    "menu.digest": "📊 Digest",
    "menu.clarify": "❓ Clarifications",
    "menu.settings": "⚙️ Settings",
    "menu.site": "🌐 Task site",
    "menu.open_all": "🌐 Open all on the site",
    "menu.open_site": "🌐 Open the task site",
    # Replies
    "bot.admin_only": "This command is for bot admins only.",
    "bot.admin_only_short": "Admins only",
    "bot.start": (
        "👋 Hi! I'm *Digest*. I analyze a work chat and collect the results of discussions.\n\n"
        "I follow the conversation, sort it into topics, pick out decisions and tasks, "
        "and send a daily and weekly digest.\n\n"
        "Choose an action below 👇"
    ),
    "bot.help": (
        "Digest: what I can do\n\n"
        "I read every message in the chat and pick out tasks on my own, no commands needed. "
        "Use the buttons below to see the results:\n\n"
        "📋 Tasks: active tasks, with status buttons\n"
        "📊 Digest: a summary for today\n"
        "❓ Clarifications: tasks with missing details\n"
        "⚙️ Settings: time zone and digest time\n\n"
        "A report for a period: /digest, /digest today, /digest yesterday, /digest week, /digest 3d, "
        "/digest 2026-11-01 2026-11-03\n\n"
        "Other commands: /task <text>, /status, /chatid\n\n"
        "For admins: /set_timezone <TZ>, /set_digest_time <HH:MM>, "
        "/set_weekly <mon..sun|off>, /set_digest_chat <id|here>, /digest_on, /digest_off, "
        "/language <en|ru>, /reanalyze <days>, /purge_history <days> confirm, /pin_dashboard"
    ),
    "bot.no_tasks": "No active tasks.",
    "bot.tasks_header": "*Active tasks* ({count}):",
    "bot.more_on_site": "…and {count} more. The full list is on the site 👇",
    "bot.more": "…and {count} more.",
    "bot.bad_period": (
        "I didn't understand the period. Examples: /digest, /digest today, /digest yesterday, "
        "/digest week, /digest 3d, /digest 2026-11-01 2026-11-03"
    ),
    "bot.digest_building": "⏳ Building the digest...",
    "bot.digest_failed": "Could not build the digest. Please try again later.",
    "bot.no_clarifications": "No tasks need clarification.",
    "bot.clarifications_header": "*Tasks that need clarification:*\n",
    "bot.weekly_on": "on {day}",
    "bot.off": "off",
    "bot.on": "on",
    "bot.this_chat": "this chat",
    "bot.settings": (
        "*Chat settings:*\n\n"
        "🌐 Time zone: `{timezone}`\n"
        "🕕 Digest time: `{digest_time}`\n"
        "📨 Digest in this chat: {enabled}\n"
        "🗓 Weekly: {weekly}\n"
        "📤 Send to: `{target}`\n"
        "🗣 Language: {language}\n"
        "🆔 Chat ID: `{chat_id}`\n\n"
        "Change: `/set_timezone <TZ>`, `/set_digest_time <HH:MM>`, `/set_weekly <mon..sun|off>`, "
        "`/set_digest_chat <id|here>`, `/digest_on`, `/digest_off`, `/language <en|ru>`"
    ),
    "bot.task_text_needed": "Give the task text: /task <task description>",
    "bot.task_created": "✅ Task created:\n*{title}*\nID: `{id}`",
    "bot.timezone_needed": "Give a time zone, for example: /set_timezone Europe/Moscow",
    "bot.timezone_unknown": "Unknown time zone: {value}\nExamples: Asia/Almaty, Europe/Moscow, UTC",
    "bot.timezone_set": "✅ Time zone updated: `{value}`",
    "bot.time_needed": "Give the time as HH:MM, for example: /set_digest_time 18:00",
    "bot.time_invalid": "Wrong time format: {value}\nExpected HH:MM, for example 18:00",
    "bot.time_set": "✅ Digest time updated: `{value}`",
    "bot.time_set_short": "Digest time: {value}",
    "bot.weekly_needed": "Give a day or off, for example: /set_weekly fri",
    "bot.weekly_off": "🔕 Weekly digest is off.",
    "bot.weekly_set": "✅ Weekly digest: {day}",
    "bot.target_needed": "Give a chat ID or here, for example: /set_digest_chat -1001234567890",
    "bot.target_not_allowed": "This chat is not in ALLOWED_CHAT_IDS.",
    "bot.target_set": "✅ Reports will go to: {target}",
    "bot.model_mock": "test mode without a model (mock)",
    "bot.model_local": "local model, data stays on the server",
    "bot.model_external": "external service",
    "bot.analysis_paused": "paused: {reason}",
    "bot.analysis_running": "running",
    "bot.status": "Model: {model} ({where})\nAnalysis: {state}\nMessages in the queue: {pending}",
    "bot.days_needed": "Give a number of days: /reanalyze 7",
    "bot.requeued": "Queued for analysis: {count}",
    "bot.purge_help": (
        "Deletion cannot be undone. Format: /purge_history <days> confirm\n"
        "Deletes messages older than the given number of days. "
        "0 deletes the chat's whole history along with its topics and reports; tasks stay."
    ),
    "bot.purged": "Messages deleted: {count}",
    "bot.digest_enabled": "🔔 The digest is on in this chat.",
    "bot.digest_disabled": "🔕 The digest is off in this chat. The bot keeps collecting tasks.",
    "bot.site_not_configured": "The task site is not set up (no DASHBOARD_SECRET / PUBLIC_BASE_URL in .env).",
    "bot.site_link": "🌐 This chat's tasks on the site:",
    "bot.pin_failed": (
        "I sent the link but could not pin it. Give the bot the \"Pin messages\" right "
        "in the group settings and run /pin_dashboard again."
    ),
    "bot.unknown_action": "Unknown action",
    "bot.task_not_found": "Task not found",
    "bot.status_changed": "Status: {status}",
    "bot.language_current": "Language of this chat: {language}. Choose:",
    "bot.language_unknown": "Unknown language: {value}\nAvailable: en, ru",
    "bot.language_set": "✅ Language of this chat: {language}",
    # Sent by the analysis
    "bot.assigned": "🔔 You have been assigned a task: {task} (chat: {chat})",
    "bot.work_chat": "work chat",
    "clarify.who_takes": "Who will take the task \"{title}\"?",
    "clarify.reply_hint": "\n\n↩️ Reply to this message so I can take your answer into account.",
    "mock.topic": "Work tasks",
    "mock.summary": "Tasks found: {count}.",
    # Weekdays, Monday first
    "weekday.0": "Monday",
    "weekday.1": "Tuesday",
    "weekday.2": "Wednesday",
    "weekday.3": "Thursday",
    "weekday.4": "Friday",
    "weekday.5": "Saturday",
    "weekday.6": "Sunday",
    # Digest
    "digest.title.daily": "Daily digest",
    "digest.title.weekly": "Weekly digest",
    "digest.title.manual": "Digest",
    "digest.totals.daily": "Summary of the day",
    "digest.totals.weekly": "Summary of the week",
    "digest.totals.manual": "Summary of the period",
    "digest.not_defined": "Not defined",
    "digest.no_topics": "No work topics found for this period.",
    "digest.topic": "Topic {number}. {title}",
    "digest.project": "Project: {project}",
    "digest.discussed": "Discussed: {summary}",
    "digest.proposals": "Proposals (not accepted)",
    "digest.decisions": "Decisions",
    "digest.no_decisions": "Decisions: no confirmed decisions",
    "digest.replaces": " (replaces: {old})",
    "digest.tasks": "Tasks",
    "digest.no_tasks": "Tasks: none",
    "digest.task_line": "{item} — deadline: {deadline}",
    "digest.some_unassigned": "{not_defined} (some tasks have no owner)",
    "digest.owners": "Owners: {names}",
    "digest.blockers": "Blockers and risks",
    "digest.questions": "Open questions",
    "digest.no_questions": "Open questions: none",
    "digest.next_steps": "Next steps",
    "digest.manual_tasks": "Tasks created by hand",
    "digest.manual_task_line": "{title} — owner: {owner}",
    "digest.totals_decided": "What was decided",
    "digest.totals_todo": "What needs to be done",
    "digest.totals_owners": "Who owns what",
    "digest.totals_questions": "What is still unresolved",
    "digest.none": "none",
    "digest.more": "…and {count} more",
    "digest.pending_notice": "⚠️ Messages not analyzed yet: {count}. They will go into the next report.",
    "digest.failed_notice": "⚠️ Messages that could not be analyzed: {count}.",
    # Model availability
    "llm.external_not_allowed": "an external LLM service is not allowed (LLM_EXTERNAL_ALLOWED=false)",
    "llm.no_api_key": "OPENAI_API_KEY is not set",
    # Doctor
    "doctor.what_to_do": "What to do: {fix}",
    "doctor.telegram_down": "Telegram is unreachable ({error})",
    "doctor.telegram_down_fix": "Check this machine's internet connection.",
    "doctor.token_rejected": "Telegram rejected the token (code {code})",
    "doctor.token_rejected_fix": "Issue a token in @BotFather and put it in BOT_TOKEN in the .env file.",
    "doctor.telegram_ok": "Telegram: bot @{username}",
    "doctor.privacy_on": "In groups the bot only sees commands",
    "doctor.privacy_on_fix": (
        "@BotFather → /mybots → the bot → Bot Settings → Group Privacy → Turn off, "
        "then remove the bot from the group and add it again."
    ),
    "doctor.chats_ok": "Allowed chats: {count}",
    "doctor.no_chats": "ALLOWED_CHAT_IDS is empty, the bot ignores every chat",
    "doctor.no_chats_fix": "Put chat IDs in .env (the /chatid command shows them).",
    "doctor.admins_ok": "Admins: {count}",
    "doctor.no_admins": "ADMIN_USER_IDS is empty, nobody can change settings",
    "doctor.no_admins_fix": "Get your ID from @userinfobot and put it in .env.",
    "doctor.db_down": "The database is unavailable ({error})",
    "doctor.db_down_fix": "Check DATABASE_URL in .env.",
    "doctor.db_ok": "Database: schema is up to date",
    "doctor.db_outdated": "The database schema is out of date",
    "doctor.db_outdated_fix": "Run: python -m app.init_db",
    "doctor.mock": "Test mode LLM_PROVIDER=mock: the meaning of messages is not analyzed",
    "doctor.mock_fix": "Set up a model in .env.",
    "doctor.paused": "Analysis is paused: {reason}",
    "doctor.paused_fix": "Connect a local model or get the data owner's permission.",
    "doctor.where_local": "local",
    "doctor.where_external": "external service",
    "doctor.pull_fix": "Run: ollama pull {model}",
    "doctor.model_gone_fix": "Check OPENAI_MODEL and the key: the service may have retired the model.",
    "doctor.model_unavailable": "Model {model} is unavailable ({where})",
    "doctor.model_silent": "Model {model} did not answer: {error}",
    "doctor.model_silent_fix": "Check that the model server is running.",
    "doctor.model_ok": "Model {model} ({where}): answered in {seconds:.0f} s",
    "doctor.env_ok": "The .env file was found",
    "doctor.no_env": "There is no .env file, settings come from the environment",
    "doctor.no_env_fix": "Copy .env.example to .env.",
    "doctor.all_good": "Everything is ready.",
    "doctor.to_fix": "Items to fix: {count}.",
    # Sandbox
    "sandbox.author": "Participant",
    "sandbox.chat": "Check",
    # Model stacks
    "stack.local": "Local: {model}",
    "stack.groq": "Groq cloud: {model}",
    "stack.current": "Current settings",
    # Task site
    "site.tasks_title": "{title}: tasks",
    "site.overview": "Digest: tasks by chat",
    "site.open": "open",
    "site.digest_at": "digest {time}",
    "site.col_task": "Task",
    "site.col_status": "Status",
    "site.col_priority": "Priority",
    "site.col_assignee": "Assignee",
    "site.no_tasks": "No tasks",
    "site.no_chats": "No active chats.",
    "site.nothing_found": "Nothing found",
    "site.filter_active": "Active",
    "site.filter_all": "All",
    "site.filter_done": "Done",
    "site.filter_cancelled": "Cancelled",
    "site.update_failed": "Could not update the status",
    # Panel: settings groups and fields
    "group.telegram": "Telegram",
    "group.model": "Model",
    "group.analysis": "Analysis",
    "group.reports": "Reports",
    "group.storage": "Storage",
    "group.task_site": "Task site",
    "group.server": "Server",
    "field.BOT_TOKEN": "Bot token",
    "field.BOT_TOKEN.help": "From @BotFather.",
    "field.ALLOWED_CHAT_IDS": "Allowed chats",
    "field.ALLOWED_CHAT_IDS.help": "IDs separated by commas. The bot ignores all other chats.",
    "field.ADMIN_USER_IDS": "Admins",
    "field.ADMIN_USER_IDS.help": "Telegram IDs of people who can change settings in the bot.",
    "field.LLM_PROVIDER": "Analysis mode",
    "field.LLM_PROVIDER.help": "mock does not understand meaning and is only good for testing.",
    "field.OPENAI_BASE_URL": "Model address",
    "field.OPENAI_BASE_URL.help": "Ollama: http://localhost:11434/v1",
    "field.OPENAI_MODEL": "Model",
    "field.OPENAI_MODEL.help": "Check a new model with scripts/eval_llm.py.",
    "field.OPENAI_API_KEY": "API key",
    "field.OPENAI_API_KEY.help": "Any value works for Ollama.",
    "field.LLM_EXTERNAL_ALLOWED": "Allow an external service",
    "field.LLM_EXTERNAL_ALLOWED.help": "Chat content goes to a third party. Needs the data owner's permission.",
    "field.LLM_REASONING_EFFORT": "Model reasoning",
    "field.LLM_REASONING_EFFORT.help": "Use none for gemma4, otherwise answers are slow and cut off.",
    "field.LLM_MAX_OUTPUT_TOKENS": "Answer length limit",
    "field.LLM_MAX_OUTPUT_TOKENS.help": "In tokens.",
    "field.LLM_TIMEOUT_SECONDS": "Request timeout",
    "field.LLM_TIMEOUT_SECONDS.help": "In seconds. Without a GPU use 900.",
    "field.ANALYSIS_INTERVAL_SECONDS": "Check the queue every",
    "field.ANALYSIS_INTERVAL_SECONDS.help": "In seconds.",
    "field.ANALYSIS_BATCH_MAX_MESSAGES": "Messages per batch",
    "field.ANALYSIS_BATCH_MAX_CHARS": "Characters per batch",
    "field.ANALYSIS_MIN_BATCH_MESSAGES": "Start at this many messages",
    "field.ANALYSIS_MIN_BATCH_MESSAGES.help": "Or when the first one has waited longer than the limit below.",
    "field.ANALYSIS_MAX_WAIT_SECONDS": "Longest wait",
    "field.ANALYSIS_MAX_WAIT_SECONDS.help": "In seconds.",
    "field.CLARIFICATIONS_ENABLED": "Ask who will take a task",
    "field.CLARIFICATION_COOLDOWN_HOURS": "Pause between questions",
    "field.CLARIFICATION_COOLDOWN_HOURS.help": "In hours.",
    "field.DEFAULT_LANGUAGE": "Default language",
    "field.DEFAULT_LANGUAGE.help": "For every chat that has not chosen its own language.",
    "field.DEFAULT_TIMEZONE": "Time zone of new chats",
    "field.DEFAULT_DIGEST_TIME": "Report time of new chats",
    "field.DIGEST_CATCHUP_HOURS": "Catch up on a missed report",
    "field.DIGEST_CATCHUP_HOURS.help": "In hours.",
    "field.MESSAGE_RETENTION_DAYS": "Keep messages",
    "field.MESSAGE_RETENTION_DAYS.help": "In days, 0 means forever. Old messages are deleted for good.",
    "field.RAW_UPDATE_RETENTION_DAYS": "Keep raw Telegram data",
    "field.RAW_UPDATE_RETENTION_DAYS.help": "In days, 0 means forever.",
    "field.DASHBOARD_SECRET": "Task site secret",
    "field.PUBLIC_BASE_URL": "Public address of the task site",
    "field.APP_ENV": "Run mode",
    "field.APP_ENV.help": "production closes the task site and the webhook if they have no secret.",
    "field.DATABASE_URL": "Database address",
    "field.DATABASE_URL.help": (
        "Changing the address does not move data: the bot starts from an empty database. "
        "Postgres has not been tested."
    ),
    "field.REDIS_URL": "Redis address",
    "field.REDIS_URL.help": "Optional: without Redis the bot works through the database.",
    "field.WEBHOOK_URL": "Webhook address",
    "field.WEBHOOK_URL.help": "Leave empty for long polling, the main mode.",
    "field.WEBHOOK_SECRET": "Webhook secret",
    "field.WEBHOOK_SECRET.help": "Required for a webhook outside local mode.",
    "field.TELEGRAM_API_BASE": "Own Bot API server",
    "field.TELEGRAM_API_BASE.help": "Usually empty.",
    # Panel: validation and errors
    "panel.one_line": "The value must fit on one line.",
    "panel.required": "Fill in this field.",
    "panel.expected_bool": "Expected true or false.",
    "panel.expected_int": "Expected a whole number of at least {minimum}.",
    "panel.expected_ids": "Expected numbers separated by commas.",
    "panel.expected_url": "Expected an address like https://example.com.",
    "panel.expected_time": "Expected a time like 18:00.",
    "panel.unknown_timezone": "Unknown time zone.",
    "panel.unknown_timezone_example": "Unknown time zone. Example: Asia/Almaty.",
    "panel.pick_from_list": "Choose a value from the list.",
    "panel.sign_in": "Sign in to the panel.",
    "panel.foreign_origin": "A request from another site was refused.",
    "panel.too_many_attempts": "Too many attempts. Wait {seconds} s.",
    "panel.wrong_password": "Wrong password.",
    "panel.not_editable": "This setting cannot be changed from the panel.",
    "panel.locked": "The value is set on the server and cannot be changed here.",
    "panel.confirm_owner": "Confirm that the data owner has allowed an external service.",
    "panel.step_token": "Paste the bot token in the Setup section.",
    "panel.step_admin": "Enter your Telegram ID in the Setup section.",
    "panel.step_chat": "Add the bot to a group, write a message there and allow it in the Setup section.",
    "panel.step_model": "Choose a model in the Setup section. Analysis is paused for now.",
    "panel.step_pull": "Download the model {model} in the Setup section.",
    "panel.unknown_status": "Unknown status.",
    "panel.task_not_found": "Task not found.",
    "panel.chat_not_found": "Chat not found.",
    "panel.token_refused": "Telegram did not accept the token. Paste a working token.",
    "panel.telegram_unreachable": "Could not reach Telegram.",
    "panel.chats_locked": "The chat list is set on the server and cannot be changed here.",
    "panel.confirm_delete": "Confirm the deletion.",
    "panel.bad_weekday": "The day of the week is wrong.",
    "panel.bad_language": "Unknown language.",
    "panel.target_not_allowed": "Reports can only go to an allowed chat.",
    "panel.model_name_needed": "Enter a model name, for example gemma4:26b.",
    "panel.stack_name_needed": "Enter a name on one line.",
    "panel.expected_tokens": "Expected a number of at least 256.",
    "panel.expected_timeout": "Expected a number of at least 5.",
    "panel.stack_not_deletable": "This option cannot be deleted.",
    "panel.stack_not_found": "Option not found.",
    "panel.stack_confirm_owner": "Confirm that the data owner has allowed this service.",
    "panel.stack_key_in_settings": "This option needs an API key. Set it in the settings.",
    "panel.stack_locked": "Values set on the server: {keys}.",
    "panel.paste_chat": "Paste a conversation.",
    "panel.text_too_long": "The text is too long, the limit is {limit} characters.",
    "panel.confirm_no_work_data": "The text goes to an external service. Confirm that it has no work data.",
    "panel.stack_key_needed": "This option needs an API key.",
    "panel.model_no_answer": "The model did not answer: {error}",
    "panel.pull_failed": "Could not download the model. Check the name and that Ollama is running.",
    "panel.pull_busy": "Another model is already downloading.",
}

RU: dict[str, str] = {
    "cmd.start": "Описание бота",
    "cmd.help": "Список команд",
    "cmd.tasks": "Показать активные задачи",
    "cmd.digest": "Дайджест: /digest [today|yesterday|week|3d|дата дата]",
    "cmd.task": "Создать задачу вручную: /task <текст>",
    "cmd.clarify": "Задачи, требующие уточнения",
    "cmd.settings": "Настройки чата",
    "cmd.status": "Состояние анализа",
    "cmd.chatid": "Показать ID чата (для администраторов)",
    "cmd.set_timezone": "Часовой пояс: /set_timezone <TZ>",
    "cmd.set_digest_time": "Время дайджеста: /set_digest_time <HH:MM>",
    "cmd.set_weekly": "Еженедельный дайджест: /set_weekly <пн..вс|off>",
    "cmd.set_digest_chat": "Куда слать отчёты: /set_digest_chat <id|here>",
    "cmd.digest_on": "Включить дайджест в этом чате",
    "cmd.digest_off": "Выключить дайджест в этом чате",
    "cmd.language": "Язык чата: /language <en|ru>",
    "cmd.reanalyze": "Повторить анализ: /reanalyze <дней>",
    "cmd.purge_history": "Удалить историю: /purge_history <дней> confirm",
    "cmd.pin_dashboard": "Закрепить ссылку на сайт с задачами",
    "bot.short_description": "Читает рабочий чат и присылает дайджесты: темы, решения и задачи.",
    "bot.description": (
        "Digest читает рабочий чат, разбирает обсуждения по темам, выделяет решения и задачи "
        "и присылает ежедневный и еженедельный дайджест."
    ),
    "status.new": "🆕 Новая",
    "status.needs_clarification": "❓ Нужно уточнение",
    "status.ready": "✅ Готова",
    "status.in_progress": "🔄 В работе",
    "status.done": "✔️ Выполнена",
    "status.cancelled": "❌ Отменена",
    "task.reporter": "🙋 От: {name}",
    "task.assignee": "👤 Исполнитель: {name}",
    "button.reopen": "🔄 Возобновить",
    "button.progress": "🔄 В работу",
    "button.done": "✔️ Готово",
    "button.cancel": "❌ Отмена",
    "menu.tasks": "📋 Задачи",
    "menu.digest": "📊 Дайджест",
    "menu.clarify": "❓ Уточнения",
    "menu.settings": "⚙️ Настройки",
    "menu.site": "🌐 Сайт с задачами",
    "menu.open_all": "🌐 Открыть все на сайте",
    "menu.open_site": "🌐 Открыть сайт с задачами",
    "bot.admin_only": "Эта команда доступна только администраторам бота.",
    "bot.admin_only_short": "Только для администраторов",
    "bot.start": (
        "👋 Привет! Я *Digest* — анализирую рабочий чат и собираю итоги обсуждений.\n\n"
        "Я слежу за обсуждениями, разбираю их по темам, выделяю решения и задачи "
        "и присылаю ежедневный и еженедельный дайджест.\n\n"
        "Выбери действие на кнопках ниже 👇"
    ),
    "bot.help": (
        "Digest — что я умею:\n\n"
        "Я сам читаю все сообщения в чате и выделяю из них задачи — команды для "
        "этого не нужны. Кнопки ниже — чтобы посмотреть результат:\n\n"
        "📋 Задачи — активные задачи (с кнопками статусов)\n"
        "📊 Дайджест — сводка за сегодня\n"
        "❓ Уточнения — задачи, где не хватает данных\n"
        "⚙️ Настройки — часовой пояс и время дайджеста\n\n"
        "Отчёт за период: /digest, /digest today, /digest yesterday, /digest week, /digest 3d, "
        "/digest 2026-11-01 2026-11-03\n\n"
        "Другие команды: /task <текст>, /status, /chatid\n\n"
        "Для администраторов: /set_timezone <TZ>, /set_digest_time <HH:MM>, "
        "/set_weekly <пн..вс|off>, /set_digest_chat <id|here>, /digest_on, /digest_off, "
        "/language <en|ru>, /reanalyze <дней>, /purge_history <дней> confirm, /pin_dashboard"
    ),
    "bot.no_tasks": "Активных задач нет.",
    "bot.tasks_header": "*Активные задачи* ({count}):",
    "bot.more_on_site": "…и ещё {count}. Полный список — на сайте 👇",
    "bot.more": "…и ещё {count}.",
    "bot.bad_period": (
        "Не понял период. Примеры: /digest, /digest today, /digest yesterday, "
        "/digest week, /digest 3d, /digest 2026-11-01 2026-11-03"
    ),
    "bot.digest_building": "⏳ Формирую дайджест...",
    "bot.digest_failed": "Не удалось сформировать дайджест. Попробуйте позже.",
    "bot.no_clarifications": "Нет задач, требующих уточнения.",
    "bot.clarifications_header": "*Задачи, требующие уточнения:*\n",
    "bot.weekly_on": "день: {day}",
    "bot.off": "выключен",
    "bot.on": "включён",
    "bot.this_chat": "этот чат",
    "bot.settings": (
        "*Настройки чата:*\n\n"
        "🌐 Часовой пояс: `{timezone}`\n"
        "🕕 Время дайджеста: `{digest_time}`\n"
        "📨 Дайджест в этом чате: {enabled}\n"
        "🗓 Еженедельный: {weekly}\n"
        "📤 Куда отправлять: `{target}`\n"
        "🗣 Язык: {language}\n"
        "🆔 Chat ID: `{chat_id}`\n\n"
        "Изменить: `/set_timezone <TZ>`, `/set_digest_time <HH:MM>`, `/set_weekly <пн..вс|off>`, "
        "`/set_digest_chat <id|here>`, `/digest_on`, `/digest_off`, `/language <en|ru>`"
    ),
    "bot.task_text_needed": "Укажите текст задачи: /task <описание задачи>",
    "bot.task_created": "✅ Задача создана:\n*{title}*\nID: `{id}`",
    "bot.timezone_needed": "Укажите часовой пояс, например: /set_timezone Europe/Moscow",
    "bot.timezone_unknown": "Неизвестный часовой пояс: {value}\nПримеры: Asia/Almaty, Europe/Moscow, UTC",
    "bot.timezone_set": "✅ Часовой пояс обновлён: `{value}`",
    "bot.time_needed": "Укажите время в формате HH:MM, например: /set_digest_time 18:00",
    "bot.time_invalid": "Неверный формат времени: {value}\nОжидается HH:MM, например 18:00",
    "bot.time_set": "✅ Время дайджеста обновлено: `{value}`",
    "bot.time_set_short": "Время дайджеста: {value}",
    "bot.weekly_needed": "Укажите день или off, например: /set_weekly пт",
    "bot.weekly_off": "🔕 Еженедельный дайджест выключен.",
    "bot.weekly_set": "✅ Еженедельный дайджест: {day}",
    "bot.target_needed": "Укажите ID чата или here, например: /set_digest_chat -1001234567890",
    "bot.target_not_allowed": "Этого чата нет в ALLOWED_CHAT_IDS.",
    "bot.target_set": "✅ Отчёты будут приходить в: {target}",
    "bot.model_mock": "тестовый режим без модели (mock)",
    "bot.model_local": "локальная модель, данные не покидают сервер",
    "bot.model_external": "внешний сервис",
    "bot.analysis_paused": "приостановлен: {reason}",
    "bot.analysis_running": "работает",
    "bot.status": "Модель: {model} ({where})\nАнализ: {state}\nСообщений в очереди: {pending}",
    "bot.days_needed": "Укажите число дней: /reanalyze 7",
    "bot.requeued": "Поставлено в очередь на анализ: {count}",
    "bot.purge_help": (
        "Удаление необратимо. Формат: /purge_history <дней> confirm\n"
        "Удаляются сообщения старше указанного числа дней. "
        "0 удаляет всю историю чата вместе с темами и отчётами; задачи остаются."
    ),
    "bot.purged": "Удалено сообщений: {count}",
    "bot.digest_enabled": "🔔 Дайджест в этом чате включён.",
    "bot.digest_disabled": "🔕 Дайджест в этом чате выключен. Задачи бот собирать продолжит.",
    "bot.site_not_configured": "Сайт с задачами не настроен (нет DASHBOARD_SECRET / PUBLIC_BASE_URL в .env).",
    "bot.site_link": "🌐 Задачи этого чата на сайте:",
    "bot.pin_failed": (
        "Ссылку отправил, но закрепить не получилось — дайте боту право "
        "«Закреплять сообщения» в настройках группы и повторите /pin_dashboard."
    ),
    "bot.unknown_action": "Неизвестное действие",
    "bot.task_not_found": "Задача не найдена",
    "bot.status_changed": "Статус: {status}",
    "bot.language_current": "Язык этого чата: {language}. Выберите:",
    "bot.language_unknown": "Неизвестный язык: {value}\nДоступны: en, ru",
    "bot.language_set": "✅ Язык этого чата: {language}",
    "bot.assigned": "🔔 Тебе назначена задача: {task} (чат: {chat})",
    "bot.work_chat": "рабочий чат",
    "clarify.who_takes": "Кто возьмёт задачу «{title}»?",
    "clarify.reply_hint": "\n\n↩️ Ответьте на это сообщение (Reply), чтобы я учёл ваш ответ.",
    "mock.topic": "Рабочие задачи",
    "mock.summary": "Выявлено задач: {count}.",
    "weekday.0": "Понедельник",
    "weekday.1": "Вторник",
    "weekday.2": "Среда",
    "weekday.3": "Четверг",
    "weekday.4": "Пятница",
    "weekday.5": "Суббота",
    "weekday.6": "Воскресенье",
    "digest.title.daily": "Ежедневный дайджест",
    "digest.title.weekly": "Еженедельный дайджест",
    "digest.title.manual": "Дайджест",
    "digest.totals.daily": "Общие итоги дня",
    "digest.totals.weekly": "Общие итоги недели",
    "digest.totals.manual": "Общие итоги периода",
    "digest.not_defined": "Не определено",
    "digest.no_topics": "За период рабочих тем не выявлено.",
    "digest.topic": "Тема {number}. {title}",
    "digest.project": "Проект: {project}",
    "digest.discussed": "Обсудили: {summary}",
    "digest.proposals": "Предложения (не приняты)",
    "digest.decisions": "Принятые решения",
    "digest.no_decisions": "Принятые решения: нет подтверждённых решений",
    "digest.replaces": " (заменяет: {old})",
    "digest.tasks": "Задачи",
    "digest.no_tasks": "Задачи: нет",
    "digest.task_line": "{item} — срок: {deadline}",
    "digest.some_unassigned": "{not_defined} (часть задач без исполнителя)",
    "digest.owners": "Ответственные: {names}",
    "digest.blockers": "Блокеры и риски",
    "digest.questions": "Открытые вопросы",
    "digest.no_questions": "Открытые вопросы: нет",
    "digest.next_steps": "Следующие действия",
    "digest.manual_tasks": "Задачи, созданные вручную",
    "digest.manual_task_line": "{title} — ответственный: {owner}",
    "digest.totals_decided": "Что решили",
    "digest.totals_todo": "Что необходимо сделать",
    "digest.totals_owners": "Кто за что отвечает",
    "digest.totals_questions": "Какие вопросы остались нерешёнными",
    "digest.none": "нет",
    "digest.more": "…и ещё {count}",
    "digest.pending_notice": "⚠️ Ещё не проанализировано сообщений: {count}. Они войдут в следующий отчёт.",
    "digest.failed_notice": "⚠️ Не удалось проанализировать сообщений: {count}.",
    "llm.external_not_allowed": "внешний LLM-сервис не разрешён (LLM_EXTERNAL_ALLOWED=false)",
    "llm.no_api_key": "не задан OPENAI_API_KEY",
    "doctor.what_to_do": "Что сделать: {fix}",
    "doctor.telegram_down": "Telegram недоступен ({error})",
    "doctor.telegram_down_fix": "Проверьте интернет на этой машине.",
    "doctor.token_rejected": "Telegram отклонил токен (код {code})",
    "doctor.token_rejected_fix": "Выпустите токен в @BotFather и запишите в BOT_TOKEN в файле .env.",
    "doctor.telegram_ok": "Telegram: бот @{username}",
    "doctor.privacy_on": "Бот видит в группах только команды",
    "doctor.privacy_on_fix": (
        "@BotFather → /mybots → бот → Bot Settings → Group Privacy → Turn off, "
        "затем удалите бота из группы и добавьте снова."
    ),
    "doctor.chats_ok": "Разрешённых чатов: {count}",
    "doctor.no_chats": "ALLOWED_CHAT_IDS пуст, бот игнорирует все чаты",
    "doctor.no_chats_fix": "Впишите ID чатов в .env (команда /chatid).",
    "doctor.admins_ok": "Администраторов: {count}",
    "doctor.no_admins": "ADMIN_USER_IDS пуст, настройки менять некому",
    "doctor.no_admins_fix": "Узнайте свой ID у @userinfobot и впишите в .env.",
    "doctor.db_down": "База данных недоступна ({error})",
    "doctor.db_down_fix": "Проверьте DATABASE_URL в .env.",
    "doctor.db_ok": "База данных: схема актуальна",
    "doctor.db_outdated": "Схема базы устарела",
    "doctor.db_outdated_fix": "Выполните: python -m app.init_db",
    "doctor.mock": "Тестовый режим LLM_PROVIDER=mock: смысл сообщений не анализируется",
    "doctor.mock_fix": "Настройте модель в .env.",
    "doctor.paused": "Анализ на паузе: {reason}",
    "doctor.paused_fix": "Подключите локальную модель или получите разрешение владельца данных.",
    "doctor.where_local": "локально",
    "doctor.where_external": "внешний сервис",
    "doctor.pull_fix": "Выполните: ollama pull {model}",
    "doctor.model_gone_fix": "Проверьте OPENAI_MODEL и ключ: сервис мог отключить модель.",
    "doctor.model_unavailable": "Модель {model} недоступна ({where})",
    "doctor.model_silent": "Модель {model} не ответила: {error}",
    "doctor.model_silent_fix": "Проверьте, что сервер модели запущен.",
    "doctor.model_ok": "Модель {model} ({where}): ответила за {seconds:.0f} с",
    "doctor.env_ok": "Файл .env найден",
    "doctor.no_env": "Файла .env нет, настройки берутся из окружения",
    "doctor.no_env_fix": "Скопируйте .env.example в .env.",
    "doctor.all_good": "Всё готово к работе.",
    "doctor.to_fix": "Нужно исправить пунктов: {count}.",
    "sandbox.author": "Участник",
    "sandbox.chat": "Проверка",
    "stack.local": "Локально: {model}",
    "stack.groq": "Облако Groq: {model}",
    "stack.current": "Текущие настройки",
    "site.tasks_title": "{title} — задачи",
    "site.overview": "Digest — задачи по чатам",
    "site.open": "открыть",
    "site.digest_at": "дайджест {time}",
    "site.col_task": "Задача",
    "site.col_status": "Статус",
    "site.col_priority": "Приоритет",
    "site.col_assignee": "Исполнитель",
    "site.no_tasks": "Задач нет",
    "site.no_chats": "Активных чатов нет.",
    "site.nothing_found": "Ничего не найдено",
    "site.filter_active": "Активные",
    "site.filter_all": "Все",
    "site.filter_done": "Готово",
    "site.filter_cancelled": "Отменено",
    "site.update_failed": "Не удалось обновить статус",
    "group.telegram": "Telegram",
    "group.model": "Модель",
    "group.analysis": "Анализ",
    "group.reports": "Отчёты",
    "group.storage": "Хранение",
    "group.task_site": "Панель задач",
    "group.server": "Сервер",
    "field.BOT_TOKEN": "Токен бота",
    "field.BOT_TOKEN.help": "Из @BotFather.",
    "field.ALLOWED_CHAT_IDS": "Разрешённые чаты",
    "field.ALLOWED_CHAT_IDS.help": "ID через запятую. Остальные чаты бот игнорирует.",
    "field.ADMIN_USER_IDS": "Администраторы",
    "field.ADMIN_USER_IDS.help": "Telegram ID тех, кто может менять настройки в боте.",
    "field.LLM_PROVIDER": "Режим анализа",
    "field.LLM_PROVIDER.help": "mock не понимает смысл и подходит только для проверки.",
    "field.OPENAI_BASE_URL": "Адрес модели",
    "field.OPENAI_BASE_URL.help": "Ollama: http://localhost:11434/v1",
    "field.OPENAI_MODEL": "Модель",
    "field.OPENAI_MODEL.help": "Проверяйте новую модель через scripts/eval_llm.py.",
    "field.OPENAI_API_KEY": "Ключ API",
    "field.OPENAI_API_KEY.help": "Для Ollama подойдёт любое значение.",
    "field.LLM_EXTERNAL_ALLOWED": "Разрешить внешний сервис",
    "field.LLM_EXTERNAL_ALLOWED.help": "Переписка уйдёт стороннему сервису. Нужно разрешение владельца данных.",
    "field.LLM_REASONING_EFFORT": "Рассуждения модели",
    "field.LLM_REASONING_EFFORT.help": "Для gemma4 ставьте none, иначе ответы медленные и обрезаются.",
    "field.LLM_MAX_OUTPUT_TOKENS": "Предел длины ответа",
    "field.LLM_MAX_OUTPUT_TOKENS.help": "В токенах.",
    "field.LLM_TIMEOUT_SECONDS": "Таймаут запроса",
    "field.LLM_TIMEOUT_SECONDS.help": "В секундах. Без видеокарты ставьте 900.",
    "field.ANALYSIS_INTERVAL_SECONDS": "Проверять очередь каждые",
    "field.ANALYSIS_INTERVAL_SECONDS.help": "В секундах.",
    "field.ANALYSIS_BATCH_MAX_MESSAGES": "Сообщений в пакете",
    "field.ANALYSIS_BATCH_MAX_CHARS": "Символов в пакете",
    "field.ANALYSIS_MIN_BATCH_MESSAGES": "Начинать с сообщений",
    "field.ANALYSIS_MIN_BATCH_MESSAGES.help": "Или когда первое ждёт дольше предела ниже.",
    "field.ANALYSIS_MAX_WAIT_SECONDS": "Максимальное ожидание",
    "field.ANALYSIS_MAX_WAIT_SECONDS.help": "В секундах.",
    "field.CLARIFICATIONS_ENABLED": "Спрашивать, кто возьмёт задачу",
    "field.CLARIFICATION_COOLDOWN_HOURS": "Пауза между вопросами",
    "field.CLARIFICATION_COOLDOWN_HOURS.help": "В часах.",
    "field.DEFAULT_LANGUAGE": "Язык по умолчанию",
    "field.DEFAULT_LANGUAGE.help": "Для всех чатов, где язык не выбран отдельно.",
    "field.DEFAULT_TIMEZONE": "Часовой пояс новых чатов",
    "field.DEFAULT_DIGEST_TIME": "Время отчёта новых чатов",
    "field.DIGEST_CATCHUP_HOURS": "Догонять пропущенный отчёт",
    "field.DIGEST_CATCHUP_HOURS.help": "В часах.",
    "field.MESSAGE_RETENTION_DAYS": "Хранить сообщения",
    "field.MESSAGE_RETENTION_DAYS.help": "В днях, 0 значит бессрочно. Старые сообщения удалятся необратимо.",
    "field.RAW_UPDATE_RETENTION_DAYS": "Хранить сырые данные Telegram",
    "field.RAW_UPDATE_RETENTION_DAYS.help": "В днях, 0 значит бессрочно.",
    "field.DASHBOARD_SECRET": "Секрет панели задач",
    "field.PUBLIC_BASE_URL": "Публичный адрес панели задач",
    "field.APP_ENV": "Режим работы",
    "field.APP_ENV.help": "production закрывает панель задач и webhook, если у них нет секрета.",
    "field.DATABASE_URL": "Адрес базы данных",
    "field.DATABASE_URL.help": (
        "Смена адреса не переносит данные: бот начнёт с пустой базы. Работа с Postgres не проверялась."
    ),
    "field.REDIS_URL": "Адрес Redis",
    "field.REDIS_URL.help": "Необязательно: без Redis бот работает через базу.",
    "field.WEBHOOK_URL": "Адрес webhook",
    "field.WEBHOOK_URL.help": "Оставьте пустым для long polling, это основной режим.",
    "field.WEBHOOK_SECRET": "Секрет webhook",
    "field.WEBHOOK_SECRET.help": "Обязателен для webhook вне режима local.",
    "field.TELEGRAM_API_BASE": "Свой сервер Bot API",
    "field.TELEGRAM_API_BASE.help": "Обычно пусто.",
    "panel.one_line": "Значение должно быть в одну строку.",
    "panel.required": "Заполните поле.",
    "panel.expected_bool": "Ожидается true или false.",
    "panel.expected_int": "Ожидается целое число не меньше {minimum}.",
    "panel.expected_ids": "Ожидаются числа через запятую.",
    "panel.expected_url": "Ожидается адрес вида https://example.com.",
    "panel.expected_time": "Ожидается время вида 18:00.",
    "panel.unknown_timezone": "Неизвестный часовой пояс.",
    "panel.unknown_timezone_example": "Неизвестный часовой пояс. Пример: Asia/Almaty.",
    "panel.pick_from_list": "Выберите значение из списка.",
    "panel.sign_in": "Войдите в панель.",
    "panel.foreign_origin": "Запрос с другого сайта отклонён.",
    "panel.too_many_attempts": "Слишком много попыток. Подождите {seconds} с.",
    "panel.wrong_password": "Неверный пароль.",
    "panel.not_editable": "Эту настройку нельзя менять из панели.",
    "panel.locked": "Значение задано на сервере и здесь не меняется.",
    "panel.confirm_owner": "Подтвердите, что владелец данных разрешил внешний сервис.",
    "panel.step_token": "Вставьте токен бота в разделе «Настройка».",
    "panel.step_admin": "Укажите свой Telegram ID в разделе «Настройка».",
    "panel.step_chat": "Добавьте бота в группу, напишите там сообщение и разрешите её в разделе «Настройка».",
    "panel.step_model": "Выберите модель в разделе «Настройка». Сейчас анализ на паузе.",
    "panel.step_pull": "Скачайте модель {model} в разделе «Настройка».",
    "panel.unknown_status": "Неизвестный статус.",
    "panel.task_not_found": "Задача не найдена.",
    "panel.chat_not_found": "Чат не найден.",
    "panel.token_refused": "Telegram не принял токен. Вставьте рабочий токен.",
    "panel.telegram_unreachable": "Не удалось связаться с Telegram.",
    "panel.chats_locked": "Список чатов задан на сервере и здесь не меняется.",
    "panel.confirm_delete": "Подтвердите удаление.",
    "panel.bad_weekday": "День недели указан неверно.",
    "panel.bad_language": "Неизвестный язык.",
    "panel.target_not_allowed": "Отчёты можно отправлять только в разрешённый чат.",
    "panel.model_name_needed": "Укажите имя модели, например gemma4:26b.",
    "panel.stack_name_needed": "Укажите название в одну строку.",
    "panel.expected_tokens": "Ожидается число не меньше 256.",
    "panel.expected_timeout": "Ожидается число не меньше 5.",
    "panel.stack_not_deletable": "Этот вариант нельзя удалить.",
    "panel.stack_not_found": "Вариант не найден.",
    "panel.stack_confirm_owner": "Подтвердите, что владелец данных разрешил этот сервис.",
    "panel.stack_key_in_settings": "Для этого варианта нужен ключ API. Задайте его в настройках.",
    "panel.stack_locked": "Значения заданы на сервере: {keys}.",
    "panel.paste_chat": "Вставьте переписку.",
    "panel.text_too_long": "Слишком длинный текст, предел {limit} символов.",
    "panel.confirm_no_work_data": "Текст уйдёт внешнему сервису. Подтвердите, что в нём нет рабочих данных.",
    "panel.stack_key_needed": "Для этого варианта нужен ключ API.",
    "panel.model_no_answer": "Модель не ответила: {error}",
    "panel.pull_failed": "Не удалось скачать модель. Проверьте имя и что Ollama запущена.",
    "panel.pull_busy": "Другая модель уже скачивается.",
}

MESSAGES: dict[str, dict[str, str]] = {"en": EN, "ru": RU}
