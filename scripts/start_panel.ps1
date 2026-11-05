# Opens the settings panel on this computer: http://localhost:8090
# Keep this window open while you use it.
Set-Location (Join-Path $PSScriptRoot "..")
.venv\Scripts\python.exe -m app.init_db
Start-Process "http://localhost:8090"
.venv\Scripts\python.exe -m app.admin_panel
Read-Host "The panel stopped. Press Enter to close this window"
