@echo off
chcp 65001 > nul
REM Hermes autoposter launcher (stage 6). Register in Windows Task Scheduler to
REM run every 1-5 minutes. Secrets are auto-loaded from %LOCALAPPDATA%\hermes\.env
REM by autopost.py, so none are needed here. No LLM is called.
REM %~dp0 is this .bat's own folder (…\cron\) — reliable even with a non-ASCII path,
REM so we don't rely on `cd` into a Cyrillic directory. autopost.py uses absolute paths.
set "HERE=%~dp0"
if not exist "%LOCALAPPDATA%\hermes\logs" mkdir "%LOCALAPPDATA%\hermes\logs"
set "PY=C:\Python314\python.exe"
if not exist "%PY%" set "PY=python"
"%PY%" "%HERE%autopost.py" >> "%LOCALAPPDATA%\hermes\logs\autopost.log" 2>&1
