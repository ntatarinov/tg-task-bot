# Undoes setup_autostart.ps1 — removes the Startup-folder shortcut.
$startupDir = [Environment]::GetFolderPath("Startup")
$shortcutPath = Join-Path $startupDir "TgTaskBot-AutoStart.lnk"

if (Test-Path $shortcutPath) {
    Remove-Item $shortcutPath -Force
    Write-Host "Autostart is off (shortcut removed)." -ForegroundColor Green
} else {
    Write-Host "Autostart shortcut not found, it is already off." -ForegroundColor Yellow
}
