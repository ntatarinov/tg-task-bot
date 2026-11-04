# Opens the settings panel on this computer: http://localhost:8090
# Keep this window open while you use it.
Set-Location (Join-Path $PSScriptRoot "..")
Start-Process "http://localhost:8090"
.venv\Scripts\python.exe -m app.admin_panel
Read-Host "Панель остановлена. Нажмите Enter, чтобы закрыть окно"
