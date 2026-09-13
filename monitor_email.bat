@echo off
rem OilWatch email monitor - records supplier quote replies and discount codes, then clears the mail.
cd /d "%~dp0"
rem Task Scheduler runs this with no console, so keep a log to read afterwards.
set OILWATCH_LOG_FILE=data\oilwatch.log
set MICROSOFT_CLIENT_ID=7767e037-c75b-489a-a4fc-7177d0067013
.venv\Scripts\python -m oilwatch.cli monitor-email
