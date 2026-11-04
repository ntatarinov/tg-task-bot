# Opens the ngrok tunnel using the domain configured as PUBLIC_BASE_URL in .env,
# so the domain only has to be changed in one place if it's ever regenerated.
Set-Location (Join-Path $PSScriptRoot "..")
$envPath = ".env"

if (-not (Test-Path $envPath)) {
    Write-Host "Не найден файл .env рядом со скриптом." -ForegroundColor Red
    Read-Host "Нажмите Enter, чтобы закрыть окно"
    exit 1
}

$line = Get-Content $envPath | Where-Object { $_ -match '^PUBLIC_BASE_URL=' } | Select-Object -First 1
if (-not $line) {
    Write-Host "PUBLIC_BASE_URL не задан в .env — туннель не запущен." -ForegroundColor Yellow
    Read-Host "Нажмите Enter, чтобы закрыть окно"
    exit 1
}

$domain = ($line -replace '^PUBLIC_BASE_URL=', '') -replace '^https?://', ''
$domain = $domain.Trim().TrimEnd('/')

if (-not $domain) {
    Write-Host "PUBLIC_BASE_URL пустой в .env — туннель не запущен." -ForegroundColor Yellow
    Read-Host "Нажмите Enter, чтобы закрыть окно"
    exit 1
}

Write-Host "Открываю туннель на https://$domain ..." -ForegroundColor Cyan
ngrok http 8000 --domain=$domain
Read-Host "Туннель остановился (или упал). Нажмите Enter, чтобы закрыть окно"
