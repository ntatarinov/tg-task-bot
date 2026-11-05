# Installs a shortcut in the current user's Startup folder so start_all.ps1
# runs automatically at every Windows logon. Safe to re-run (just overwrites
# the shortcut). To undo, run remove_autostart.ps1 or just delete the
# shortcut from shell:startup.
$scriptDir = $PSScriptRoot
$target = Join-Path $scriptDir "start_all.ps1"
$startupDir = [Environment]::GetFolderPath("Startup")
$shortcutPath = Join-Path $startupDir "TgTaskBot-AutoStart.lnk"

$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($shortcutPath)
$shortcut.TargetPath = "powershell.exe"
$shortcut.Arguments = "-WindowStyle Hidden -ExecutionPolicy Bypass -File `"$target`""
$shortcut.WorkingDirectory = $scriptDir
$shortcut.Description = "Tg Task Bot: bot + dashboard + ngrok tunnel"
$shortcut.Save()

Write-Host "Done: the autostart shortcut was created here:" -ForegroundColor Green
Write-Host $shortcutPath
