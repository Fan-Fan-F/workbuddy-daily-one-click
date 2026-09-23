@echo off
setlocal
cd /d "%~dp0"

rem ---------------------------------------------------------------
rem  WorkBuddy-Daily one-click launcher
rem
rem  IMPORTANT: keep this file pure ASCII.
rem  cmd.exe reads a .bat file by byte offset using the *current*
rem  code page. Non-ASCII bytes (e.g. Chinese saved as UTF-8) get
rem  decoded as GBK on a Chinese Windows, which desynchronises the
rem  batch parser and makes it execute fragments of words as
rem  commands (symptoms: "'fined' is not recognized", "'-id' ...").
rem  So: no non-ASCII text here, and no "chcp" in this file.
rem  deploy.py sets the console to UTF-8 by itself.
rem
rem  Structure note: uses labels + goto instead of nested if-blocks,
rem  because a "set /p" inside a parenthesised block needs delayed
rem  expansion and that is easy to get wrong.
rem ---------------------------------------------------------------

rem ---- locate a usable Python ----
set "PY="
where py >nul 2>nul && set "PY=py -3"
if not defined PY (
  where python >nul 2>nul && set "PY=python"
)
if defined PY goto :check_deploy
goto :no_python

rem ================= no Python =================
:no_python
echo.
echo   [X] Python not found on this machine.
echo.

where winget >nul 2>nul
if errorlevel 1 goto :manual

echo     Python is required to run the deployer.
echo     You can install it automatically now ^(winget^).
echo.
echo       1^) Install Python 3.12 automatically   ^(recommended^)
echo       2^) Show manual install steps
echo       3^) Exit
echo.
set "PCHOICE="
set /p "PCHOICE=  Choose [1]: "
if "%PCHOICE%"=="" set "PCHOICE=1"
if "%PCHOICE%"=="2" goto :manual
if "%PCHOICE%"=="3" goto :cancelled

echo.
echo   Installing Python 3.12 via winget ...
echo   A UAC prompt may appear - please allow it.
echo.
winget install -e --id Python.Python.3.12 --accept-source-agreements --accept-package-agreements
echo.
echo   ---------------------------------------------------------------
echo   Install finished.
echo.
echo   This window cannot see the new PATH yet, so the deployer has to
echo   be started again from a fresh window - otherwise it will still
echo   report "Python not found".
echo.
echo   Please close this window and double-click the launcher again.
echo   ---------------------------------------------------------------
echo.
pause
exit /b 0

:manual
echo   Install Python first, either of these works:
echo.
echo       winget install -e --id Python.Python.3.12
echo       https://www.python.org/downloads/
echo.
echo   When installing, tick "Add Python to PATH".
echo.
pause
exit /b 1

:cancelled
echo.
echo   Cancelled - nothing was changed.
echo.
pause
exit /b 1

rem ================= run =================
:check_deploy
if not exist "%~dp0deploy.py" (
  echo.
  echo   [X] deploy.py not found next to this file.
  echo       Please keep the whole folder together:
  echo       %~dp0
  echo.
  pause
  exit /b 1
)

rem No arguments -> show the start menu.
rem The menu itself is rendered by deploy.py so the Chinese text is encoded right.
if "%~1"=="" (
  %PY% "%~dp0deploy.py" --menu
) else (
  %PY% "%~dp0deploy.py" %*
)
set "RC=%ERRORLEVEL%"

rem no arguments = double-clicked -> keep window open
if "%~1"=="" (
  echo.
  if "%RC%"=="0" (
    echo   Done. Press any key to close.
  ) else (
    echo   Exit code %RC%. See the messages above.
    echo   Press any key to close.
  )
  pause >nul
)

exit /b %RC%
