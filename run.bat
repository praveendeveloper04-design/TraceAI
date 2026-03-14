@echo off
REM ============================================================================
REM  TraceAI — Main Launcher
REM  Double-click this file to start TraceAI.
REM  Shows a menu to choose what you want to do.
REM ============================================================================

title TraceAI
cd /d "%~dp0"

REM --- Activate virtual environment ---
if not exist ".venv\Scripts\activate.bat" (
    color 0C
    echo.
    echo  ERROR: Virtual environment not found.
    echo  Please run "setup.bat" first.
    echo.
    pause
    exit /b 1
)
call .venv\Scripts\activate.bat

REM --- Load .env if it exists ---
if exist ".env" (
    for /f "usebackq tokens=1,* delims==" %%a in (".env") do (
        set "line=%%a"
        if not "!line:~0,1!"=="#" (
            set "%%a=%%b" 2>nul
        )
    )
)

:MENU
cls
color 0B
echo.
echo  ============================================
echo       TraceAI - AI Investigation Platform
echo  ============================================
echo.
echo    1.  Run Setup Wizard (first-time config)
echo    2.  Fetch My Tasks
echo    3.  Investigate a Task
echo    4.  View Investigation History
echo    5.  Show Status
echo    6.  Scan / Rescan Repository
echo    7.  Start API Server (for VS Code)
echo    8.  Open Interactive Terminal
echo    9.  Run Tests
echo    0.  Exit
echo.
echo  ============================================
echo.

set /p choice="  Select an option [0-9]: "

if "%choice%"=="1" goto SETUP
if "%choice%"=="2" goto TASKS
if "%choice%"=="3" goto INVESTIGATE
if "%choice%"=="4" goto HISTORY
if "%choice%"=="5" goto STATUS
if "%choice%"=="6" goto PROFILE
if "%choice%"=="7" goto SERVE
if "%choice%"=="8" goto TERMINAL
if "%choice%"=="9" goto TESTS
if "%choice%"=="0" goto EXIT

echo  Invalid option. Try again.
timeout /t 2 >nul
goto MENU

:SETUP
cls
echo.
echo  Starting Setup Wizard...
echo  ========================
echo.
ta setup
echo.
pause
goto MENU

:TASKS
cls
echo.
set /p assignee="  Enter assignee email (or press Enter to skip): "
echo.
if "%assignee%"=="" (
    ta tasks
) else (
    ta tasks --assigned-to "%assignee%"
)
echo.
pause
goto MENU

:INVESTIGATE
cls
echo.
set /p taskid="  Enter Task ID to investigate: "
if "%taskid%"=="" (
    echo  No task ID provided.
    pause
    goto MENU
)
echo.
echo  Starting AI investigation on task %taskid%...
echo  This may take 1-2 minutes...
echo.
ta investigate %taskid% --output "investigation_%taskid%.md"
echo.
if exist "investigation_%taskid%.md" (
    echo  Report saved to: investigation_%taskid%.md
    echo.
    set /p openreport="  Open report in Notepad? [Y/N]: "
    if /i "%openreport%"=="Y" notepad "investigation_%taskid%.md"
)
echo.
pause
goto MENU

:HISTORY
cls
echo.
echo  Recent Investigations
echo  =====================
echo.
ta history --limit 20
echo.
pause
goto MENU

:STATUS
cls
echo.
echo  TraceAI Status
echo  ==============
echo.
ta status
echo.
echo  ---
echo  Data directory: %USERPROFILE%\.task-analyzer
echo  Config file:    %USERPROFILE%\.task-analyzer\config.json
echo.
pause
goto MENU

:PROFILE
cls
echo.
set /p repopath="  Enter repository path (or press Enter for current dir): "
echo.
if "%repopath%"=="" (
    ta profile
) else (
    ta profile "%repopath%"
)
echo.
pause
goto MENU

:SERVE
cls
echo.
echo  Starting TraceAI API Server...
echo  ==============================
echo  Server: http://127.0.0.1:7420
echo  API Docs: http://127.0.0.1:7420/docs
echo  Press Ctrl+C to stop.
echo.
ta serve
pause
goto MENU

:TERMINAL
cls
echo.
echo  TraceAI Interactive Terminal
echo  ============================
echo  Virtual environment is active. Type 'ta --help' for commands.
echo  Type 'exit' to return to the menu.
echo.
cmd /k "echo. && ta --help && echo."
goto MENU

:TESTS
cls
echo.
echo  Running Tests...
echo  ================
echo.
pytest tests/ -v
echo.
pause
goto MENU

:EXIT
echo.
echo  Goodbye!
echo.
exit /b 0
