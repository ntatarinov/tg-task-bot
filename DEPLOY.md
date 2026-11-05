# Deploying Digest on a server

**English** | [Русский](DEPLOY.ru.md)

Digest is a Telegram bot that analyzes work chats and sends digests. This guide describes
installing it on a Linux server from a ready-made archive.

## What will run

| Container | Purpose | Ports |
|---|---|---|
| `bot` | Receives messages (long polling), analysis, reports | Outbound only |
| `ollama` | Local AI model | Inside the Docker network only |
| `panel` | Control panel | `127.0.0.1:8090` |
| `model-pull` | Downloads the model and exits | — |

The SQLite database is in `./data`, the settings are in `./.env`. Postgres and Redis are not needed.

## Requirements

| | Minimum |
|---|---|
| OS | Linux x86_64 with `sh`, `curl`, `unzip` |
| RAM | 32 GB, 64 GB recommended |
| Disk | 50 GB |
| CPU | 8 threads |
| GPU | Optional. NVIDIA requires the NVIDIA Container Toolkit; `install.sh` detects the card itself |

**Network.** No inbound ports are needed except SSH. Outbound access on 443 is needed to `api.telegram.org`
at all times, and to Docker Hub, `pypi.org` and `registry.ollama.ai` during installation.

## Installation

You will receive an archive `digest-bot-<date>.zip`. **It contains the bot token and all stored conversations.** Send it
only over a secure channel and delete it after installing.

1. Stop the bot on the previous computer: only one instance can run with one token.
2. Install:

```bash
unzip digest-bot-*.zip -d digest-bot && cd digest-bot
sh install.sh
```

The script installs Docker if it is missing, sets permissions `600` on `.env`, creates the panel password and prints it,
downloads the model, updates the database and runs the check. The first run takes several minutes.

Another option: `sh install.sh cloud` runs the bot without a local model. The model then comes from the settings
in `.env`; this is an external service and requires `LLM_EXTERNAL_ALLOWED=true` with the data owner's permission.

3. Make sure the check at the end shows "Everything is ready." You can repeat it like this:

```bash
docker compose -f docker-compose.selfhost.yml exec bot python -m app.doctor
```

## Control panel

The panel is open only on the server's `127.0.0.1`. **Do not publish port 8090 and do not put it behind an external proxy:**
the panel changes tokens and settings. From your work computer, connect through an SSH tunnel:

```bash
ssh -L 8090:127.0.0.1:8090 <user>@<server>
```

Then open http://localhost:8090. The password is in `.env`, on the `ADMIN_PANEL_PASSWORD` line.

In the panel you can check the system and the model on a test conversation, switch the model option, change settings,
allow and block chats, and restart the bot. The bot restarts itself: the panel creates a flag file, the bot exits,
and Docker brings it back up. The panel has no access to Docker.

## Maintenance

The commands below are for `docker-compose.selfhost.yml`. With a GPU, add `-f docker-compose.gpu.yml`.

```bash
# Logs and status
docker compose -f docker-compose.selfhost.yml logs -f bot
docker compose -f docker-compose.selfhost.yml ps

# Stop and start
docker compose -f docker-compose.selfhost.yml down
docker compose -f docker-compose.selfhost.yml up -d
```

Logs contain no message text and no tokens. Containers come back up on their own after a server reboot.

### Backup

Save `data/backup.db` and `.env` to a secure place. They contain the conversations and tokens.

```bash
docker compose -f docker-compose.selfhost.yml exec -T bot python -c "import sqlite3; s=sqlite3.connect('/data/bot.db'); d=sqlite3.connect('/data/backup.db'); s.backup(d); d.close()"
```

This command is safe while the bot is running. Do not copy the `bot.db` file directly: part of the data
is in `bot.db-wal`.

### Restore

```bash
docker compose -f docker-compose.selfhost.yml down
rm -f data/bot.db-wal data/bot.db-shm
cp <copy> data/bot.db
docker compose -f docker-compose.selfhost.yml up -d
```

### Update

Build an archive without data (`python -m app.bundle --code-only`), unpack it over the current folder and run
the installation. `.env` and `data/` are not changed.

```bash
unzip -o digest-bot-*.zip -d digest-bot && cd digest-bot
sh install.sh
```

If the database schema changes, `data/bot.db.bak-<date>` is created automatically before the update.
These copies contain the conversations and are not deleted automatically: remove them once the update is confirmed.

**Rollback:** unpack the previous archive, restore the database from the copy and run `sh install.sh`.

### Moving to another host

The **Move** section of the panel downloads an archive with the code, settings and data; this requires entering the password again.
Stop the bot on the old server and install the archive on the new one the same way as the first installation.
Then, in the **Setup** section, click **Where is the bot**: allow the chats you need and block the rest.
If the new instance uses a different bot, first paste its token and add it to the groups.

### Changing the model

The panel is the easiest way: the **Check** section shows the result on a test conversation, and in the **Setup** section
you can select and download a model. Full quality check on synthetic scenarios:

```bash
docker compose -f docker-compose.selfhost.yml exec bot python scripts/eval_llm.py --model <name> --pause 0 --large 3
```

For reasoning models such as `gemma4`, keep `LLM_REASONING_EFFORT=none`.

## Security

- Conversations do not leave the server: the model runs locally. The server belongs to the hosting provider, so the choice of provider must be agreed with the data owner.
- The retention period is set in the panel; by default data is kept forever.
- The bot works only in chats from `ALLOWED_CHAT_IDS`.
- The panel password is long and random. You can change it in `.env`, then run `docker compose -f docker-compose.selfhost.yml up -d --force-recreate panel`.

## What has not been verified

When this guide was prepared, Docker on the development machine was off, so the first installation may need fixes:

- The image was not built in full and the containers were not run. Verified: the format of the compose files, the syntax of `install.sh`, the `pip install` step, and running the bot and the panel without Docker.
- Work with real Telegram was not tested.
- Model speed was measured on a desktop PC with an 8 GB GPU. On a server without a GPU it will be lower; one batch may take several minutes.

`docker-compose.yml` and `docker-compose.prod.yml` with Postgres belong to the previous deployment scheme and were not tested. Use the options from this guide.
