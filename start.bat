@echo off
REM ============================================================
REM  Knowledge Gardener launcher
REM  Starts Obsidian, then the watchdog.
REM
REM  Obsidian must be running for Obsidian Sync to work, so phone-
REM  written triggers reach this watchdog and reports sync back.
REM  That's why this launches Obsidian too, not just the watchdog.
REM
REM  Obsidian reopens its last-used vault, which is the local
REM  Knowledge Garden — no vault path needs to be passed.
REM
REM  To auto-start on login: press Win+R, type  shell:startup ,
REM  and drop a SHORTCUT to this file into that folder.
REM ============================================================

start "" "C:\Program Files\Obsidian\Obsidian.exe"

REM Give Obsidian a few seconds to open and begin syncing.
timeout /t 10 /nobreak >nul

cd /d "C:\Users\parre\knowledge-gardener"
"C:\Users\parre\knowledge-gardener\venv\Scripts\python.exe" src\obsidian_watchdog.py
