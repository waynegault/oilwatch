@echo off
rem Run OilWatch's in-process scheduler: quotes on their interval, discovery
rem weekly, and the supplier-email sweep hourly - all from the intervals in
rem config/settings.json. Long-running by design, so it is started at logon
rem rather than run by hand; Ctrl-C (or closing the window) stops it.
cd /d "%~dp0"
rem This runs minimised from the Startup folder, so keep a log to read afterwards.
set OILWATCH_LOG_FILE=data\oilwatch.log
.venv\Scripts\python -m oilwatch.cli schedule
