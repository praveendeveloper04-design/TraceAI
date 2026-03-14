@echo off
REM ============================================================================
REM  TraceAI — First-Time Setup Script
REM  Run this ONCE to install everything and configure the platform.
REM  Double-click this file or run it from Command Prompt.
REM ============================================================================

title TraceAI - First Time Setup
color 0A
echo.
echo  ============================================
echo    TraceAI - First Time Setup
echo    AI-Powered Developer Investigation Platform
echo  ============================================
echo.

REM --- Navigate to project directory ---
cd /d "%~dp0"
echo [1/7] Project directory: %CD%
echo.

REM --- Check Python ---
echo [2/7] Checking Python installation...
python --version >nul 2>&1
if %ERRORLEVEL% neq 0 (
    color 0C
    echo  ERROR: Python is not installed or not in PATH.
    echo  Please install Python 3.11+ from https://www.python.org/downloads/
    echo.
    pause
    exit /b 1
)
for /f "tokens=2" %%i in ('python --version 2^>^&1') do set PYVER=%%i
echo  Found Python %PYVER%
echo.

REM --- Create virtual environment ---
echo [3/7] Creating virtual environment...
if exist ".venv\Scripts\activate.bat" (
    echo  Virtual environment already exists. Skipping.
) else (
    python -m venv .venv
    if %ERRORLEVEL% neq 0 (
        color 0C
        echo  ERROR: Failed to create virtual environment.
        pause
        exit /b 1
    )
    echo  Virtual environment created at .venv\
)
echo.

REM --- Activate virtual environment ---
echo [4/7] Activating virtual environment...
call .venv\Scripts\activate.bat
echo  Activated: %VIRTUAL_ENV%
echo.

REM --- Install dependencies ---
echo [5/7] Installing TraceAI and all dependencies...
echo  This may take 2-3 minutes on first run...
echo.
pip install -e ".[all]" --quiet --quiet
if %ERRORLEVEL% neq 0 (
    color 0C
    echo.
    echo  ERROR: pip install failed. Retrying with verbose output...
    pip install -e ".[all]"
    pause
    exit /b 1
)
echo  All dependencies installed successfully.
echo.

REM --- Create .env file if missing ---
echo [6/7] Checking environment configuration...
if not exist ".env" (
    copy .env.example .env >nul
    echo  Created .env from template.
    echo.
    color 0E
    echo  =====================================================
    echo   IMPORTANT: You need to set your Anthropic API key!
    echo  =====================================================
    echo.
    echo  Option A: Edit the .env file now:
    echo    Open .env in any text editor and replace
    echo    "your-api-key-here" with your actual key.
    echo.
    echo  Option B: Set it in this terminal:
    echo    set ANTHROPIC_API_KEY=sk-ant-your-key-here
    echo.
    echo  Get a key at: https://console.anthropic.com/settings/keys
    echo.
    color 0A
) else (
    echo  .env file already exists.
)
echo.

REM --- Verify installation ---
echo [7/7] Verifying installation...
ta --version
if %ERRORLEVEL% neq 0 (
    color 0C
    echo  ERROR: 'ta' command not found. Installation may have failed.
    pause
    exit /b 1
)
echo.

REM --- Done ---
echo  ============================================
echo    Setup Complete!
echo  ============================================
echo.
echo  Next steps:
echo.
echo    1. Set your ANTHROPIC_API_KEY in .env
echo       (or run: set ANTHROPIC_API_KEY=your-key)
echo.
echo    2. Run the configuration wizard:
echo       ta setup
echo.
echo    3. Or double-click "run.bat" to start TraceAI
echo.
echo  ============================================
echo.
pause
