# Starts everything on this computer: the local model, the database update and the bot.
# Keep this window open while the bot is working.
Set-Location (Join-Path $PSScriptRoot "..")

$ollama = Join-Path $env:LOCALAPPDATA "Programs\Ollama\ollama.exe"
$running = $false
try {
    Invoke-WebRequest -UseBasicParsing -TimeoutSec 2 http://127.0.0.1:11434/api/tags | Out-Null
    $running = $true
} catch {}

if (-not $running) {
    if (-not (Test-Path $ollama)) {
        Write-Host "Ollama is not installed. Run: winget install Ollama.Ollama"
        Read-Host "Press Enter to close this window"
        exit 1
    }
    # Without this Ollama cuts a batch off at about 4K tokens.
    $env:OLLAMA_CONTEXT_LENGTH = "12288"
    $env:OLLAMA_KEEP_ALIVE = "-1"
    Start-Process -FilePath $ollama -ArgumentList "serve" -WindowStyle Hidden
    Start-Sleep -Seconds 5
}

.venv\Scripts\python.exe -m app.init_db
.venv\Scripts\python.exe -m app.doctor
if ($LASTEXITCODE -ne 0) {
    Read-Host "Fix the items above and run this again. Press Enter to close this window"
    exit 1
}
# Exit code 75 means the settings panel asked for a restart.
do {
    .venv\Scripts\python.exe -m app.polling
} while ($LASTEXITCODE -eq 75)
Read-Host "The bot stopped (or crashed). Press Enter to close this window"
