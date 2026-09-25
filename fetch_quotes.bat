@echo off
rem One click: fetch every supplier's price, work through the reply window with a
rem mailbox sweep and a page rebuild every ten minutes, then open the page.
rem
rem   fetch_quotes.bat [minutes]      the reply window, in minutes (60)
rem
rem The fetch is a refresh: `quote-all --browser`, the command AGENTS.md names as
rem the explicit act of refreshing. It drives real browsers, so it takes 1-3
rem minutes, opens one visible window per supplier that needs one, and fails per
rem supplier rather than as a whole.
rem
rem The minutes after it are for the two kinds of reply that only arrive by mail -
rem the suppliers whose quote tool emails a copy of the quote it has just
rem generated, and the ones with no quote page at all - and they are worked
rem through in passes of ten minutes. Each pass reads the mailbox, recording those
rem replies and clearing what it read, and rebuilds the page, so the copy opened
rem at the end carries every reply that arrived. A window shorter than ten minutes
rem is one pass.
rem
rem Closing this window stops it where it is. What has already been fetched and
rem recorded is kept, and the replies can be read later with
rem `oilwatch monitor-email`.
cd /d "%~dp0"
rem The shared env file, so the log path is written once rather than here.
call "%~dp0oilwatch_env.bat"

set "WAIT_MINUTES=%~1"
if not defined WAIT_MINUTES set "WAIT_MINUTES=60"
set /a "WAIT_SECONDS=WAIT_MINUTES*60"
rem Passes of ten minutes, and never none: a short window still sweeps once.
set /a "PASSES=WAIT_SECONDS/600"
if %PASSES% lss 1 set "PASSES=1"
set /a "PASS_SECONDS=WAIT_SECONDS/PASSES"
set /a "PASS_MINUTES=PASS_SECONDS/60"
set /a "PASS=0"

echo.
echo === OilWatch: new quotes from every supplier ===
echo.
echo Fetching (1-3 minutes; Chrome windows will open)...
.venv\Scripts\python -m oilwatch.cli quote-all --browser
if errorlevel 1 echo WARNING: the sweep exited with an error - see the lines above.

:pass
set /a "PASS+=1"
echo.
echo Reply window: pass %PASS% of %PASSES%, waiting %PASS_MINUTES% minute(s).
echo Close this window to stop here; the prices already fetched are kept.
timeout /t %PASS_SECONDS% /nobreak

echo Reading the mailbox for the replies...
.venv\Scripts\python -m oilwatch.cli monitor-email

echo Rebuilding the results page...
.venv\Scripts\python tools\build_explorer.py
if %PASS% lss %PASSES% goto pass

echo.
echo Opening the results page...
start "" "%~dp0data\oilwatch-explorer.html"

echo.
echo Done - swept the mailbox %PASSES% time(s), and the results page is open.
pause
