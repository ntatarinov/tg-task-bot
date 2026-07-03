# HANDOFF — состояние проекта

> **Читать первым.** Это точка входа для разработчика, который продолжает работу.
> Порядок чтения перед тем как брать любую задачу:
> 1. **HANDOFF.md** (этот файл) — что есть сейчас, как запустить, чего не трогать.
> 2. **[docs/BACKLOG.md](docs/BACKLOG.md)** — что просил пользователь и статус каждой задачи.
> 3. **[docs/JOURNAL.md](docs/JOURNAL.md)** — что уже пробовали и что НЕ сработало (чтобы не повторять ошибки).

Проект: Telegram-бот **«Тудушка»** (`@im_tudushka_bot`) — читает рабочий групповой
чат, автоматически выделяет задачи из хаотичной переписки, отсекает личное/оффтоп,
показывает карточки задач, шлёт ежедневный дайджест.

Стек: Python 3.12 (целевой), aiogram 3.x, FastAPI, SQLAlchemy 2.x (async), Alembic,
APScheduler, Pydantic, pytest. Абстрактный LLM-клиент (mock / OpenAI-совместимый:
OpenAI, **Groq**, локальная **Ollama**).

---

## 1. Что сейчас РАБОТАЕТ

- Приём и сохранение сообщений группового чата (текст + подписи к медиа + raw JSON).
- LLM-анализ: выделяет **рабочие** задачи, отсекает личное/шутки. Даёт `message_type`,
  `contains_task`, `confidence`, `assignee`, `requirements`, и т.п. (Pydantic-валидация,
  устойчивая к «грязному» выводу модели).
- Кластеризация: reply → related_task_id → похожесть заголовка → новая задача.
- **Карточка задачи**: название, приоритет, статус, **🙋 От: постановщик**,
  **👤 Исполнитель**, дедлайн. Управление статусом **inline-кнопками**
  (🔄 В работу / ✔️ Готово / ❌ Отмена / 🔄 Возобновить).
- **Кнопочное меню** (/start, /help): 📋 Задачи, 📊 Дайджест, ❓ Уточнения, ⚙️ Настройки.
- Уточняющие вопросы с антиспамом (Redis cooldown + фолбэк на БД). Ответ на вопрос
  бота (reply) закрывает уточнение и обновляет задачу.
- **Дайджест per-chat** в заданное время по timezone чата. Управление через
  `/digest_on` / `/digest_off` и поле `chats.digest_enabled` (сейчас включён только в SAP СК).
- **Дедлайны считаются детерминированно** ([app/utils/dates.py](app/utils/dates.py)) из
  текста сообщения — модели даты НЕ доверяем (она выдумывает год/срок).
- Retry анализа для сообщений, сохранённых при недоступной LLM (`pending_analysis`).
- REST API + read-only HTML `/dashboard` + Prometheus `/metrics`.
- Тесты: **55 passed**, `ruff` и `mypy` чисто.

## 2. Где сейчас запущен бот (важно!)

Пользователь перенёс прод-бота **на отдельный Windows-компьютер**, LLM — через
облачный **Groq** (`llama-3.3-70b-versatile`, OpenAI-совместимый API). Запуск там:
`.venv\Scripts\python.exe -m app.polling`. SQLite-база `bot.db` перенесена с Mac
(вся память: ~117 сообщений, ~15 задач, 3 чата).

Причина переезда: на Mac грелся из-за локальной модели (Ollama на CPU). Groq снимает
нагрузку (думает облако), поэтому комп не греется.

> ⚠️ Бот работает, только пока Windows-комп включён и запущен процесс `app.polling`.
> Настоящий 24/7 без своего компа — только сервер (см. [DEPLOY.md](DEPLOY.md), отложено).

## 3. Как запустить локально (для разработки)

```bash
python -m venv .venv && source .venv/bin/activate   # Windows: py -3.12 -m venv .venv
pip install -e ".[dev]"
cp .env.example .env          # заполнить BOT_TOKEN; LLM: mock | Groq | Ollama
python -m app.init_db         # создать схему в SQLite (для Postgres — alembic upgrade head)
python -m app.polling         # long polling (webhook не нужен)
```
LLM-режимы задаются только в `.env` (`LLM_PROVIDER`, `OPENAI_BASE_URL`, `OPENAI_MODEL`,
`OPENAI_API_KEY`). Без ключа → `mock` (эвристика по ключевым словам).

## 4. Проверки

```bash
pytest -q            # 55 passed
ruff check app/ tests/
mypy app/
```

## 5. Карта проекта (кратко)

- `app/main.py` — FastAPI (webhook, /health, /ready, /metrics, /dashboard, /api/*).
- `app/polling.py` — entrypoint long polling (основной способ запуска).
- `app/init_db.py` — создание схемы для SQLite (dev). Prod — Alembic.
- `app/config.py` — настройки из `.env` (pydantic-settings).
- `app/db/` — `models.py` (7 таблиц), `repositories.py`, `session.py`, `redis.py` (graceful).
- `app/llm/` — `base.py`, `mock_client.py`, `openai_client.py` (он же для Groq/Ollama), `schemas.py`.
- `app/services/` — `message_ingestor`, `task_analyzer` (оркестрация), `task_clusterer`,
  `clarification_service`, `digest_service`, `scheduler` (per-chat дайджест + retry).
- `app/bot/` — `handlers.py` (команды/меню/кнопки/ingestion), `dispatcher.py`, `commands.py`,
  `utils.py` (`safe_send_message`/`safe_answer` — fallback на plain при ошибке Markdown).
- `app/utils/` — `dates.py` (детерминированные дедлайны), `time.py`, `metrics.py`, `logging.py`.
- `app/api/` — REST + dashboard.
- `alembic/versions/` — миграции 0001–0004 (⚠️ под Postgres/JSONB; на SQLite не идут — там `init_db`).
- `tests/` — 12 файлов.

## 6. КРИТИЧЕСКИЕ предупреждения (не наступать!)

- **⚠️ Секреты и данные НЕ коммитить:** `.env` (боевой токен + Groq-ключ) и `bot.db`
  (реальная переписка коллег) в `.gitignore`. Не убирать оттуда.
- **⚠️ `app/db/models.py` использует `Optional`/`List`, НЕ `X | None`.** SQLAlchemy
  вычисляет `Mapped[...]` в рантайме, а PEP 604 ломается на Python 3.9. `ruff --fix`
  это ломает — есть `per-file-ignores` в `pyproject.toml`. Не «чинить».
- **⚠️ Дедлайны — только детерминированно из текста** (`normalize_deadline`). Не давать
  модели вычислять/подставлять даты (выдумывает).
- **⚠️ Pydantic пиннут `==2.9.2`** (aiogram 3.13.1 не совместим с 2.10+). Не поднимать.
- **⚠️ `psycopg2-binary` убран** (требует компилятор, не нужен — используем asyncpg/aiosqlite).
- **⚠️ Любое изменение кода требует ПЕРЕЗАПУСКА `app.polling`** — бот держит старый код.
- **Alembic-миграции под Postgres.** На SQLite новые колонки добавлялись `ALTER TABLE`
  вручную; схему для dev создаёт `app.init_db` (create_all).

## 7. Что дальше — см. [docs/BACKLOG.md](docs/BACKLOG.md)

Активная задача: **личные уведомления исполнителю** (при назначении @username слать
в личку) — реализуется на Windows-компе, где живёт прод-код. Подробности и статусы — в бэклоге.
