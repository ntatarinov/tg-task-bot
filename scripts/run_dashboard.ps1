# Runs the web dashboard (FastAPI) only — scheduler disabled here since
# run_bot.ps1 (app.polling) already sends digests; running both would double-send them.
Set-Location (Join-Path $PSScriptRoot "..")
$env:ENABLE_SCHEDULER = "false"
.venv\Scripts\uvicorn.exe app.main:app --host 0.0.0.0 --port 8000
Read-Host "The site stopped (or crashed). Press Enter to close this window"
