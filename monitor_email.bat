@echo off
rem Daily OilWatch email monitor - records supplier quote replies and deletes them.
cd /d "%~dp0"
set MICROSOFT_CLIENT_ID=7767e037-c75b-489a-a4fc-7177d0067013
.venv\Scripts\python -m oilwatch.cli monitor-email
