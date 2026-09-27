@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo.
echo   Arena -- web view AND web edit
echo   -----------------------------------
echo.

where python >nul 2>&1
if errorlevel 1 goto nopython

set "DBFILE=arena.db"
if not "%ARENA_DB%"=="" set "DBFILE=%ARENA_DB%"

echo   DB:   %DBFILE%
echo   URL:  http://127.0.0.1:8765
echo.
echo   You can read AND write here (new / submit / rewrite / confirm).
echo   It is not a second write path -- same functions cli.py calls.
echo   NOT wired up: casting a vote (see DECLARATION 13.5).
echo.
echo   Ctrl-C to stop.
echo.

rem Start the server first, THEN open the browser. Reversed, the browser
rem arrives before the server and shows "can't reach this page".
start "" /b python serve.py --port 8765 --db "%DBFILE%"

rem Wait for OUR server to answer. Kept in a separate file because
rem cmd.exe cannot handle a multi-line python -c.
rem Exit codes: 0 = ours, 1 = nothing listening, 2 = port busy with
rem something else. Only 0 may proceed.
python wait_port.py 8765
if errorlevel 2 goto foreign
if errorlevel 1 goto failed

start "" http://127.0.0.1:8765

echo.
echo   (Server is up. Closing this window stops it.)
echo.
pause
exit /b 0

:nopython
echo   [ERR] "python" not found on PATH.
echo.
echo   This view needs Python. Install it, then run this file again.
echo.
pause
exit /b 1

:foreign
echo.
echo   [ERR] Port 8765 is taken by something that is NOT Arena.
echo.
echo   Another program is using it. Either stop that program, or use
echo   a different port by running serve.py by hand:
echo       python serve.py --port 8766
echo.
pause
exit /b 2

:failed
echo.
echo   [ERR] Server did not come up.
echo.
echo   Most likely the port is already held by a leftover server from
echo   an earlier run. See the error serve.py printed above.
echo.
echo   For the full output, run it by hand:
echo       python serve.py --port 8765
echo.
pause
exit /b 1
