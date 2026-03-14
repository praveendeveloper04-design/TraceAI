@echo off
REM ============================================================================
REM  TraceAI — Quick Investigate
REM  Double-click to quickly investigate a task by ID.
REM ============================================================================

title TraceAI - Quick Investigate
cd /d "%~dp0"

if not exist ".venv\Scripts\activate.bat" (
    color 0C
    echo  Run "setup.bat" first.
    pause
    exit /b 1
)
call .venv\Scripts\activate.bat

REM --- Load .env ---
if exist ".env" (
    for /f "usebackq tokens=1,* delims==" %%a in (".env") do (
        set "line=%%a"
        if not "!line:~0,1!"=="#" (
            set "%%a=%%b" 2>nul
        )
    )
)

color 0B
echo.
echo  ============================================
echo    TraceAI - Quick Investigation
echo  ============================================
echo.

set /p taskid="  Enter Task ID: "
if "%taskid%"=="" (
    echo  No task ID provided. Exiting.
    pause
    exit /b 0
)

echo.
echo  Investigating task %taskid%...
echo  Please wait, AI is analyzing...
echo.

ta investigate %taskid% --output "investigation_%taskid%.md"

echo.
if exist "investigation_%taskid%.md" (
    color 0A
    echo  ============================================
    echo    Investigation Complete!
    echo    Report: investigation_%taskid%.md
    echo  ============================================
    echo.
    set /p openfile="  Open report? [Y/N]: "
    if /i "%openfile%"=="Y" notepad "investigation_%taskid%.md"
) else (
    color 0E
    echo  Investigation finished. Check the output above.
)

echo.
pause
