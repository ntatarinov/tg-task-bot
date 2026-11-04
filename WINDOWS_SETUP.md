# Запуск на Windows

Инструкция для запуска бота и модели на одном компьютере.

## Что понадобится

- Python 3.12 с отмеченной при установке опцией **Add python.exe to PATH**.
- Ollama: `winget install Ollama.Ollama`.
- Токен бота от [@BotFather](https://t.me/BotFather).
- Для `gemma4:26b` нужно 32 ГБ ОЗУ и около 20 ГБ на диске.

## Установка

В папке проекта откройте PowerShell:

```powershell
py -m venv .venv
.venv\Scripts\python.exe -m pip install -e ".[dev]"
copy .env.example .env
ollama pull gemma4:26b
```

## Настройка

Откройте `.env` в Блокноте и заполните:

```
BOT_TOKEN=<токен от BotFather>
ALLOWED_CHAT_IDS=<ID чатов через запятую>
ADMIN_USER_IDS=<ваш Telegram ID>
ADMIN_PANEL_PASSWORD=<любой длинный пароль>
OPENAI_BASE_URL=http://localhost:11434/v1
OPENAI_API_KEY=ollama
OPENAI_MODEL=gemma4:26b
LLM_REASONING_EFFORT=none
LLM_MAX_OUTPUT_TOKENS=6000
LLM_TIMEOUT_SECONDS=900
```

Остальное можно менять в панели управления.

## Запуск

```powershell
scripts\start_local.ps1
```

Скрипт запускает Ollama, обновляет базу (сначала копирует её в `bot.db.bak-<дата>`), проверяет настройки
и стартует бота. Если что-то не готово, он покажет, что исправить. Окно должно оставаться открытым.

Панель управления запускается отдельно и открывается по адресу http://localhost:8090:

```powershell
scripts\start_panel.ps1
```

Чтобы компьютер не засыпал: **Параметры → Система → Питание → Спящий режим → Никогда**.

## Если что-то пошло не так

- **`python` не является командой:** Python установлен без опции PATH, переустановите его.
- **Ошибка `Conflict`:** с этим токеном работает второй экземпляр бота, остановите его.
- **Другая ошибка:** выполните `.venv\Scripts\python.exe -m app.doctor`, команда покажет, какой пункт не работает.

Перенос на сервер описан в [DEPLOY.md](DEPLOY.md).
