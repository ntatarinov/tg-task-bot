# Opens the ngrok tunnel using the domain configured as PUBLIC_BASE_URL in .env,
# so the domain only has to be changed in one place if it's ever regenerated.
Set-Location (Join-Path $PSScriptRoot "..")
$envPath = ".env"

if (-not (Test-Path $envPath)) {
    Write-Host "No .env file found next to the script." -ForegroundColor Red
    Read-Host "Press Enter to close this window"
    exit 1
}

$line = Get-Content $envPath | Where-Object { $_ -match '^PUBLIC_BASE_URL=' } | Select-Object -First 1
if (-not $line) {
    Write-Host "PUBLIC_BASE_URL is not set in .env, the tunnel was not started." -ForegroundColor Yellow
    Read-Host "Press Enter to close this window"
    exit 1
}

$domain = ($line -replace '^PUBLIC_BASE_URL=', '') -replace '^https?://', ''
$domain = $domain.Trim().TrimEnd('/')

if (-not $domain) {
    Write-Host "PUBLIC_BASE_URL is empty in .env, the tunnel was not started." -ForegroundColor Yellow
    Read-Host "Press Enter to close this window"
    exit 1
}

Write-Host "Opening the tunnel at https://$domain ..." -ForegroundColor Cyan
ngrok http 8000 --domain=$domain
Read-Host "The tunnel stopped (or crashed). Press Enter to close this window"
