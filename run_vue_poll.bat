@echo off
REM ============================================================
REM  run_vue_poll.bat
REM  Convenience wrapper for the CDP-attach flow:
REM    1) launches Edge in remote-debugging mode on a dedicated
REM       profile and opens the Vue Enschede page
REM    2) waits for you to clear the Cloudflare "Just a moment..."
REM       check (once; the profile remembers it afterwards)
REM    3) runs vue_poll.py --cdp, forwarding every arg you passed
REM
REM  Usage (same args as vue_poll.py):
REM    run_vue_poll.bat --days 14 --start 2026-07-20 --after 18:00 --weekdays --reset
REM ============================================================
setlocal

set "EDGE=C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
set "PROFILE=%USERPROFILE%\.vue_edge_cdp"
set "PORT=9222"
set "VUE_URL=https://www.vuecinemas.nl/cinema/enschede/nu-in-de-bioscoop"

if not exist "%EDGE%" (
    echo ERROR: Edge not found at "%EDGE%"
    echo Edit the EDGE path at the top of this script.
    exit /b 1
)

echo.
echo [1/3] Launching Edge with remote debugging on port %PORT% ...
REM A dedicated --user-data-dir forces a brand-new Edge instance so the
REM debug port is actually honored even if your normal Edge is running.
start "" "%EDGE%" --remote-debugging-port=%PORT% --user-data-dir="%PROFILE%" "%VUE_URL%"

echo.
echo [2/3] An Edge window is opening on the Vue page.
echo       If you see a "Just a moment..." / "Verify you are human" check,
echo       solve it. (After the first time this profile remembers it.)
echo.
pause

echo.
echo [3/3] Running the scraper attached to that Edge ...
py "%~dp0vue_poll.py" --cdp --cdp-url "http://localhost:%PORT%" %*
set "RC=%ERRORLEVEL%"

echo.
echo Done (exit code %RC%). The Edge window is left open so its
echo Cloudflare clearance is reused on the next run.
endlocal & exit /b %RC%
