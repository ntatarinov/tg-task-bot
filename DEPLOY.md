# Деплой на VPS (24/7, polling + Groq)

Бот работает в режиме **long polling** — публичный домен и HTTPS не нужны.
Анализ — через **Groq** (бесплатный облачный LLM, OpenAI-совместимый).
Нужен самый дешёвый VPS (хватит **1 ГБ RAM**), потому что тяжёлую модель считает Groq, а не сервер.

> ⚠️ Приватность: при Groq сообщения чата уходят в облако провайдера для анализа.
> Для рабочей переписки убедись, что это допустимо (иначе — вариант с локальной Ollama на своём сервере).

---

## Шаг 0. Ключ Groq (бесплатно, 2 минуты)
1. Зайди на **https://console.groq.com** → зарегистрируйся.
2. **API Keys** → **Create API Key** → скопируй ключ (`gsk_...`).

## Шаг 1. VPS
Любой провайдер (Hetzner, Timeweb, DigitalOcean, Aeza…). Бери **Ubuntu 22.04/24.04, 1 ГБ RAM**.
Запиши IP и root-пароль, зайди по SSH:
```bash
ssh root@ТВОЙ_IP
```

## Шаг 2. Docker
```bash
curl -fsSL https://get.docker.com | sh
```

## Шаг 3. Залить проект на сервер
С **локального** Mac (из корня проекта), без мусора:
```bash
rsync -av --exclude '.venv' --exclude '__pycache__' --exclude '.pytest_cache' \
      --exclude '.mypy_cache' --exclude 'bot.db' --exclude '.env' \
      /Users/whooch/tg-task-bot/  root@ТВОЙ_IP:/opt/tg-task-bot/
```
(Или через git, если заведёшь репозиторий. `.env` НЕ копируем — создадим на сервере.)

## Шаг 4. Настроить .env на сервере
```bash
cd /opt/tg-task-bot
cp .env.example .env
nano .env
```
Заполни минимум:
```
BOT_TOKEN=123456789:ABC...   # токен бота от @BotFather
WEBHOOK_URL=                 # пусто (polling)
LLM_PROVIDER=openai
OPENAI_API_KEY=gsk_...       # ключ Groq из шага 0
OPENAI_MODEL=llama-3.3-70b-versatile
OPENAI_BASE_URL=https://api.groq.com/openai/v1
DATABASE_URL=postgresql+asyncpg://postgres:postgres@postgres:5432/tg_task_bot
REDIS_URL=redis://redis:6379/0
DEFAULT_TIMEZONE=Asia/Almaty
DEFAULT_DIGEST_TIME=18:00
```
(Сохранить в nano: `Ctrl+O`, `Enter`, `Ctrl+X`.)

## Шаг 5. Запуск + миграции
```bash
docker compose -f docker-compose.prod.yml up -d --build
docker compose -f docker-compose.prod.yml run --rm bot alembic upgrade head
```

## Шаг 6. Проверить, что бот живой
```bash
docker compose -f docker-compose.prod.yml logs -f bot
```
Ждём строку: `Run polling for bot @im_tudushka_bot`. Теперь пиши в чат — бот работает 24/7.

---

## Управление

```bash
# логи
docker compose -f docker-compose.prod.yml logs -f bot

# перезапуск / остановка
docker compose -f docker-compose.prod.yml restart bot
docker compose -f docker-compose.prod.yml down          # остановить всё (данные в volume сохранятся)

# обновить после изменений кода (заново rsync, затем):
docker compose -f docker-compose.prod.yml up -d --build
docker compose -f docker-compose.prod.yml run --rm bot alembic upgrade head   # если были новые миграции

# бэкап базы
docker compose -f docker-compose.prod.yml exec postgres pg_dump -U postgres tg_task_bot > backup.sql
```

---

## Заметки
- **Порты наружу не открываются** — Postgres/Redis доступны только внутри docker-сети, бот ходит только исходящими (Telegram + Groq). Дополнительный firewall не обязателен, но не помешает (`ufw allow OpenSSH`).
- **Автоперезапуск:** `restart: unless-stopped` — контейнеры сами поднимутся после ребута сервера/падения.
- **Смена модели Groq:** если `llama-3.3-70b-versatile` окажется медленной/недоступной — попробуй `llama-3.1-8b-instant` (быстрее, чуть проще). Актуальный список — в консоли Groq.
- **Приватность важнее облака?** Тогда вместо Groq — Ollama на этом же (более мощном, ≥16 ГБ RAM) сервере: в `.env` укажи `OPENAI_BASE_URL=http://ollama:11434/v1`, `OPENAI_MODEL=qwen2.5:7b`, `OPENAI_API_KEY=ollama` и добавь сервис `ollama` в compose.
- **Даты** считаются детерминированно на стороне бота (не зависят от модели), так что смена LLM их не ломает.
- **Ноут больше не нужен** — можно закрывать. Локальный бот (Ollama) и серверный не конфликтуют только если запущен ОДИН: перед деплоем останови локальный `pkill -f app.polling`, иначе два процесса будут драться за getUpdates.
