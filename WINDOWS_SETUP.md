# Running on Windows

**English** | [Русский](WINDOWS_SETUP.ru.md)

Instructions for running the bot and the model on one computer.

## What you need

- Python 3.12 with the **Add python.exe to PATH** option checked during installation.
- Ollama: `winget install Ollama.Ollama`.
- A bot token from [@BotFather](https://t.me/BotFather).
- `gemma4:26b` needs 32 GB RAM and about 20 GB of disk space.

## Installation

Open PowerShell in the project folder:

```powershell
py -m venv .venv
.venv\Scripts\python.exe -m pip install -e ".[dev]"
copy .env.example .env
ollama pull gemma4:26b
```

## Configuration

Open `.env` in Notepad and fill in:

```
BOT_TOKEN=<token from BotFather>
ALLOWED_CHAT_IDS=<chat IDs, comma-separated>
ADMIN_USER_IDS=<your Telegram ID>
ADMIN_PANEL_PASSWORD=<any long password>
OPENAI_BASE_URL=http://localhost:11434/v1
OPENAI_API_KEY=ollama
OPENAI_MODEL=gemma4:26b
LLM_REASONING_EFFORT=none
LLM_MAX_OUTPUT_TOKENS=6000
LLM_TIMEOUT_SECONDS=900
```

The language is set by `DEFAULT_LANGUAGE=en` (`en` or `ru`): the value applies to every chat that has not picked its own language.

Everything else can be changed in the control panel.

## Running

```powershell
scripts\start_local.ps1
```

The script starts Ollama, updates the database (first copying it to `bot.db.bak-<date>`), checks the settings
and starts the bot. If something is not ready, it shows what to fix. The window must stay open.

The control panel is started separately and opens at http://localhost:8090:

```powershell
scripts\start_panel.ps1
```

To keep the computer from sleeping: **Settings → System → Power → Sleep → Never**.

## If something goes wrong

- **`python` is not recognized as a command:** Python was installed without the PATH option; reinstall it.
- **`Conflict` error:** a second instance of the bot is running with this token; stop it.
- **Any other error:** run `.venv\Scripts\python.exe -m app.doctor`; it shows which item is not working.

Moving to a server is described in [DEPLOY.md](DEPLOY.md).
