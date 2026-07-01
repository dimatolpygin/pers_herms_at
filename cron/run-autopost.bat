@echo off
chcp 65001 > nul
REM Hermes autoposter launcher (stage 6). Register in Windows Task Scheduler to
REM run every 1-5 minutes. Secrets are auto-loaded from %LOCALAPPDATA%\hermes\.env
REM by autopost.py, so none are needed here. No LLM is called.
cd /d D:\claude\хермес
if not exist "%LOCALAPPDATA%\hermes\logs" mkdir "%LOCALAPPDATA%\hermes\logs"
python cron\autopost.py >> "%LOCALAPPDATA%\hermes\logs\autopost.log" 2>&1
