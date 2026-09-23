@echo off
rem OilWatch email monitor - records supplier quote replies and discount codes, then clears the mail.
cd /d "%~dp0"
rem The scheduled task runs this through "C:\Programs\bat Files\Wrap\run-hidden.vbs"
rem (wscript + WshShell.Run SW_HIDE): its principal is an interactive token, which
rem would otherwise draw a real cmd.exe window every hour. Run it from a normal
rem shell when you want visible output - it keeps its own log, and that path is
rem set in the shared env file below rather than here.
call "%~dp0oilwatch_env.bat"
set MICROSOFT_CLIENT_ID=7767e037-c75b-489a-a4fc-7177d0067013
.venv\Scripts\python -m oilwatch.cli monitor-email
