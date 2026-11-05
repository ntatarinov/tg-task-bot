# Runs the Telegram bot itself (long-polling). Keep this window open.
Set-Location (Join-Path $PSScriptRoot "..")
# Brings the database up to date. Safe to repeat, and it backs bot.db up first.
.venv\Scripts\python.exe -m app.init_db
.venv\Scripts\python.exe -m app.polling
Read-Host "The bot stopped (or crashed). Press Enter to close this window"
