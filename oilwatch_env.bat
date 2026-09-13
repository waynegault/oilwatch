@echo off
rem Shared environment for OilWatch's unattended runs. Both start_scheduler.bat
rem and monitor_email.bat call this, so the log path is written once here rather
rem than copied into each - two copies of one value drift apart.
rem
rem %~dp0 is this file's own directory, so the path is absolute however the
rem caller was launched (Startup folder, Task Scheduler, or a terminal).
set "OILWATCH_LOG_FILE=%~dp0data\oilwatch.log"
