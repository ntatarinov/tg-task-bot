# Undoes setup_autostart.ps1 — removes the Startup-folder shortcut.
$startupDir = [Environment]::GetFolderPath("Startup")
$shortcutPath = Join-Path $startupDir "TgTaskBot-AutoStart.lnk"

if (Test-Path $shortcutPath) {
    Remove-Item $shortcutPath -Force
    Write-Host "Автозапуск отключён (ярлык удалён)." -ForegroundColor Green
} else {
    Write-Host "Ярлык автозапуска не найден — уже отключён." -ForegroundColor Yellow
}
