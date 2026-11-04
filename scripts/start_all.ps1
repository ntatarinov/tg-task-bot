# Launches the bot, the web dashboard, and the ngrok tunnel, each in its own
# visible PowerShell window — same three commands from WINDOWS_SETUP.md, just
# started together instead of typed by hand. Run this manually, or drop a
# shortcut to it in the Startup folder to launch automatically at login
# (see setup_autostart.ps1).
$scriptDir = $PSScriptRoot

Start-Process powershell.exe -ArgumentList '-NoExit', '-ExecutionPolicy', 'Bypass', '-File', (Join-Path $scriptDir 'run_bot.ps1')
Start-Sleep -Seconds 2
Start-Process powershell.exe -ArgumentList '-NoExit', '-ExecutionPolicy', 'Bypass', '-File', (Join-Path $scriptDir 'run_dashboard.ps1')
Start-Sleep -Seconds 3
Start-Process powershell.exe -ArgumentList '-NoExit', '-ExecutionPolicy', 'Bypass', '-File', (Join-Path $scriptDir 'run_tunnel.ps1')
