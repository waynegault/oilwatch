@echo off
rem One click: fetch every supplier's price, give the suppliers an hour to email
rem their quotes, then open the results page.
rem
rem   fetch_quotes.bat [minutes]      how long to wait for emailed quotes (60)
rem
rem The fetch is a refresh: `quote-all --browser`, the command AGENTS.md names as
rem the explicit act of refreshing. It drives real browsers, so it takes 1-3
rem minutes, opens one visible window per supplier that needs one, and fails per
rem supplier rather than as a whole. The hour after it is for the two kinds of
rem reply that only arrive by mail - the supplier whose quote tool emails a copy
rem of the quote it has just generated, and the supplier with no quote page at
rem all - so the mailbox sweep at the end records those and clears the mail it
rem read.
rem
rem Closing this window stops it where it is. What has already been fetched is
rem kept, and the replies can be read later with `oilwatch monitor-email`.
cd /d "%~dp0"
rem The shared env file, so the log path is written once rather than here.
call "%~dp0oilwatch_env.bat"

set "WAIT_MINUTES=%~1"
if not defined WAIT_MINUTES set "WAIT_MINUTES=60"
set /a "WAIT_SECONDS=WAIT_MINUTES*60"

echo.
echo === OilWatch: new quotes from every supplier ===
echo.
echo Fetching (1-3 minutes; Chrome windows will open)...
.venv\Scripts\python -m oilwatch.cli quote-all --browser
if errorlevel 1 echo WARNING: the sweep exited with an error - see the lines above.

echo.
echo Waiting %WAIT_MINUTES% minute(s) for emailed quotes.
echo Close this window to stop here; the prices already fetched are kept.
timeout /t %WAIT_SECONDS% /nobreak

echo.
echo Reading the mailbox for the replies...
.venv\Scripts\python -m oilwatch.cli monitor-email

echo.
echo Building the results page...
.venv\Scripts\python tools\build_explorer.py
start "" "%~dp0data\oilwatch-explorer.html"

echo.
echo Done - the results page is open, and this window can be closed.
pause
