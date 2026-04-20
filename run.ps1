$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location "$ScriptDir\src"
..\venv\Scripts\python.exe obsidian_watchdog.py