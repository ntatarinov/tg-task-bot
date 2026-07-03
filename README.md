# TG Task Bot («Тудушка»)

Telegram-бот для анализа командного группового чата и автоматического формирования задач для разработчиков.

> ## 🧭 Для разработчиков — начните отсюда
> Прежде чем брать любую задачу, прочитайте в этом порядке:
> 1. **[HANDOFF.md](HANDOFF.md)** — текущее состояние: что работает, как запустить, **чего не трогать**.
> 2. **[docs/BACKLOG.md](docs/BACKLOG.md)** — все задачи пользователя и их статус (сделано / в работе / план).
> 3. **[docs/JOURNAL.md](docs/JOURNAL.md)** — что уже пробовали и что **не сработало** (чтобы не повторять).
>
> Запуск и деплой: [WINDOWS_SETUP.md](WINDOWS_SETUP.md) (Windows + Groq), [DEPLOY.md](DEPLOY.md) (сервер).
> ⚠️ `.env` и `bot.db` — секреты/приватные данные, в git не попадают (см. `.gitignore`).

## Что умеет

- Читает все сообщения из группового чата
- Сохраняет их в PostgreSQL (включая raw update JSON для отладки)
- Анализирует обсуждения через LLM (mock без API-ключа, OpenAI если есть ключ)
- Выделяет задачи из хаотичного обсуждения
- Группирует сообщения по задачам (reply / related_task_id / похожесть заголовка)
- Задаёт уточняющие вопросы (антиспам-защита через Redis с фолбэком на БД)
- Принимает ответ на уточнение (reply на вопрос бота) и обновляет задачу
- Ежедневно в digest_time по timezone **каждого** чата отправляет дайджест
- Если LLM недоступна — сохраняет сообщение как `pending_analysis` и повторяет анализ позже

### Где используется Redis

- Антиспам уточнений: ключ `clarify:cooldown:<chat_id>` с TTL = `CLARIFICATION_COOLDOWN_HOURS`.
- Дедупликация дайджеста: `SET NX` ключа `digest:sent:<chat_id>:<дата>`, чтобы не отправить дважды.

Если Redis недоступен, обе функции автоматически переключаются на запросы к БД — бот продолжает работать.

---

## 1. Создать Telegram-бота через BotFather

1. Откройте [@BotFather](https://t.me/BotFather) в Telegram
2. Отправьте `/newbot`
3. Задайте имя и username бота
4. Скопируйте **BOT_TOKEN** — он понадобится в `.env`

---

## 2. Отключить Privacy Mode (обязательно для группового чата)

По умолчанию бот видит только сообщения, начинающиеся с `/`. Чтобы читать **все** сообщения:

**Вариант A — отключить Privacy Mode:**
1. Напишите [@BotFather](https://t.me/BotFather) команду `/mybots`
2. Выберите вашего бота → **Bot Settings** → **Group Privacy**
3. Нажмите **Turn off**

**Вариант B — сделать бота администратором:**
1. Добавьте бота в группу
2. Откройте настройки группы → Администраторы → добавьте бота
3. Дайте права на чтение сообщений

---

## 3. Добавить бота в групповой чат

1. Откройте нужную группу в Telegram
2. Нажмите на название группы → Добавить участника
3. Найдите вашего бота по username и добавьте
4. Убедитесь, что Privacy Mode отключён (шаг 2)

---

## 4. Запуск локально через Docker Compose

```bash
# 1. Клонировать / перейти в директорию
cd /Users/whooch/tg-task-bot

# 2. Создать .env из примера
cp .env.example .env

# 3. Заполнить .env:
#    BOT_TOKEN=your_token_here
#    WEBHOOK_URL=https://your-domain.com/telegram/webhook
#    LLM_PROVIDER=mock  # или openai если есть OPENAI_API_KEY

# 4. Запустить контейнеры
docker compose up -d

# 5. Применить миграции
docker compose exec app alembic upgrade head

# 6. Проверить статус
curl http://localhost:8000/health
curl http://localhost:8000/ready
```

---

## 5. Настройка Webhook

Telegram требует HTTPS-URL для webhook. Варианты:

**Через ngrok (для локальной разработки):**
```bash
ngrok http 8000
# Скопируйте URL вида https://xxxx.ngrok.io
# Установите WEBHOOK_URL=https://xxxx.ngrok.io/telegram/webhook в .env
```

**Установить webhook вручную:**
```bash
curl -X POST "https://api.telegram.org/bot<BOT_TOKEN>/setWebhook" \
  -H "Content-Type: application/json" \
  -d '{"url": "https://your-domain.com/telegram/webhook"}'
```

**Проверить webhook:**
```bash
curl "https://api.telegram.org/bot<BOT_TOKEN>/getWebhookInfo"
```

> **Примечание:** Приложение автоматически устанавливает webhook при запуске, если `WEBHOOK_URL` задан в `.env`.

### Локальный запуск без webhook (long polling)

Если нет публичного HTTPS-домена, проще запустить бота в режиме long polling — webhook не нужен.
Оставьте `WEBHOOK_URL` пустым и запустите отдельный процесс:

```bash
# локально (venv)
python -m app.polling

# или в Docker (postgres/redis подняты через compose)
docker compose run --rm app python -m app.polling
```

Polling-процесс сам удаляет webhook, поднимает планировщик (дайджесты + retry) и читает
сообщения через getUpdates. Это рекомендованный способ для разработки и проверки бота локально.

---

## 6. Миграции базы данных

```bash
# Применить все миграции
docker compose exec app alembic upgrade head

# Откатить последнюю миграцию
docker compose exec app alembic downgrade -1

# Создать новую миграцию
docker compose exec app alembic revision --autogenerate -m "описание"

# Статус миграций
docker compose exec app alembic current
```

---

## 7. Запуск тестов

```bash
# Установить зависимости для разработки
pip install -e ".[dev]"

# Запустить все тесты
pytest

# Запустить с подробным выводом
pytest -v

# Запустить конкретный файл
pytest tests/test_llm_schema.py -v

# Линтер и типы
ruff check .
ruff format .
mypy app/
```

---

## 8. Команды бота

| Команда | Описание |
|---------|----------|
| `/start` | Приветствие и краткое описание бота |
| `/help` | Список всех команд |
| `/tasks` | Показать текущие активные задачи |
| `/digest` | Сформировать дайджест прямо сейчас |
| `/task <текст>` | Создать задачу вручную |
| `/clarify` | Показать задачи, где нужны уточнения |
| `/settings` | Показать настройки чата (timezone, время дайджеста) |
| `/set_timezone <TZ>` | Изменить часовой пояс чата (например `Europe/Moscow`) |
| `/set_digest_time <HH:MM>` | Изменить время ежедневного дайджеста |

Под каждой задачей в `/tasks` есть inline-кнопки **🔄 В работу / ✔️ Готово / ❌ Отмена**
(после закрытия — **🔄 Возобновить**) для управления статусом прямо из чата.
Бот также сохраняет подписи (caption) к фото и документам — задачи из медиа-сообщений не теряются.

---

## Конфигурация (.env)

| Переменная | По умолчанию | Описание |
|-----------|-------------|----------|
| `BOT_TOKEN` | — | Токен бота от BotFather |
| `WEBHOOK_URL` | — | HTTPS URL для webhook |
| `DATABASE_URL` | postgresql+asyncpg://... | URL PostgreSQL |
| `REDIS_URL` | redis://localhost:6379/0 | URL Redis |
| `LLM_PROVIDER` | `mock` | Провайдер LLM: `mock` или `openai` |
| `OPENAI_API_KEY` | — | API-ключ OpenAI (если `LLM_PROVIDER=openai`) |
| `OPENAI_MODEL` | `gpt-4o-mini` | Модель OpenAI |
| `DEFAULT_TIMEZONE` | `Asia/Almaty` | Часовой пояс по умолчанию |
| `DEFAULT_DIGEST_TIME` | `18:00` | Время ежедневного дайджеста |
| `CONTEXT_MESSAGES_LIMIT` | `20` | Кол-во сообщений контекста для LLM |
| `CLARIFICATION_COOLDOWN_HOURS` | `4` | Минимальный интервал между уточнениями |
| `CLARIFICATION_MIN_CONFIDENCE` | `0.7` | Мин. уверенность LLM для вопроса |

---

## Архитектура

```
Telegram Update
     │
     ▼
POST /telegram/webhook (FastAPI)
     │
     ▼
aiogram Dispatcher → Handlers
     │
     ├── Command handlers (/tasks, /digest, ...)
     │
     └── handle_message (все текстовые сообщения)
              │
              ▼
         MessageIngestor
              │
              ├── ChatRepository.get_or_create
              ├── UserRepository.get_or_create
              ├── MessageRepository.create
              │
              └── TaskAnalyzer.analyze_and_process
                       │
                       ├── LLMClient.analyze_message (mock / OpenAI)
                       ├── TaskClusterer.find_or_create_task
                       └── ClarificationService.create (если нужно)

APScheduler (каждую минуту) → digest_tick
     │   для каждого активного чата: local time == digest_time? уже отправляли сегодня?
     └── DigestService.build_digest → LLMClient.summarize_for_digest → отправка в чат

APScheduler (каждые N сек) → retry_pending_analysis
     │   берёт сообщения с pending_analysis=true
     └── повторный TaskAnalyzer.analyze_and_process
```

---

## Веб-панель и метрики

FastAPI-приложение (поднимается через `docker compose up`) дополнительно отдаёт:

| Endpoint | Назначение |
|----------|-----------|
| `GET /dashboard` | Read-only HTML-панель: чаты и их задачи |
| `GET /api/chats` | JSON-список активных чатов со счётчиком задач |
| `GET /api/chats/{chat_id}/tasks?status=` | JSON-список задач чата (фильтр по статусу) |
| `GET /metrics` | Метрики Prometheus (`tg_messages_ingested_total`, `tg_tasks_created_total`, `tg_clarifications_asked_total`, `tg_digests_sent_total`, `tg_llm_errors_total`) |

```bash
open http://localhost:8000/dashboard
curl http://localhost:8000/metrics
```

> Эти endpoint'ы доступны в webhook-режиме (FastAPI). В polling-режиме запущен только бот.

## Реализовано

- [x] Чтение и сохранение сообщений группового чата (+ raw update JSON, + подписи к медиа)
- [x] LLM-анализ с абстрактным клиентом (mock / OpenAI) и Pydantic-валидацией ответов
- [x] Выделение и кластеризация задач (reply / related_task_id / похожесть заголовка)
- [x] Уточняющие вопросы с антиспам-защитой (Redis cooldown + фолбэк на БД)
- [x] Приём ответа на уточнение (reply на вопрос бота) и обновление задачи
- [x] Ежедневный дайджест в digest_time по timezone каждого чата + ручной `/digest`
- [x] Retry для `pending_analysis` сообщений (когда LLM временно недоступна)
- [x] Polling-режим для локальной разработки без webhook
- [x] Настройка timezone / времени дайджеста командами бота
- [x] Статусная машина задач через inline-кнопки
- [x] Read-only веб-панель и JSON API
- [x] Метрики Prometheus на `/metrics`

## Следующие версии (roadmap)

- [ ] Интеграция с Jira / Linear / GitHub Issues (экспорт задач) — сознательно отложено по ТЗ
- [ ] Полноценная веб-панель с управлением (сейчас read-only)
- [ ] Поддержка вложений медиа (не только подписей)
- [ ] Многоязычность (i18n)
- [ ] Алерты на основе метрик (Alertmanager)
