@echo off
rem Run OilWatch's in-process scheduler: quotes on their interval, discovery
rem weekly, and the supplier-email sweep hourly - all from the intervals in
rem config/settings.json. Long-running by design, so it is started at logon
rem rather than run by hand; Ctrl-C (or closing the window) stops it.
cd /d "%~dp0"
rem Minimised and console-less, so this run keeps a log; the path lives in the
rem shared env file rather than here.
call "%~dp0oilwatch_env.bat"
.venv\Scripts\python -m oilwatch.cli schedule
