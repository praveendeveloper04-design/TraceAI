@echo off
REM ═══════════════════════════════════════════════════════════════════════════
REM  TraceAI — One-Time Bootstrap Script
REM
REM  This script replaces setup.bat, run.bat, investigate.bat, setup-vscode.bat
REM  Run once to set up the development environment. After that, just open
REM  VS Code — the extension handles everything automatically.
REM ═══════════════════════════════════════════════════════════════════════════

setlocal enabledelayedexpansion

echo.
echo  ╔══════════════════════════════════════════════════════════════╗
echo  ║                    TraceAI Bootstrap                        ║
echo  ║          AI-Powered Developer Investigation Platform        ║
echo  ╚══════════════════════════════════════════════════════════════╝
echo.

REM ── Step 1: Check Python 3.11+ ─────────────────────────────────────────────

echo [1/6] Checking Python installation...

python --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python is not installed or not in PATH.
    echo Please install Python 3.11+ from https://python.org
    exit /b 1
)

for /f "tokens=2 delims= " %%v in ('python --version 2^>^&1') do set PYVER=%%v
for /f "tokens=1,2 delims=." %%a in ("%PYVER%") do (
    set PYMAJOR=%%a
    set PYMINOR=%%b
)

if %PYMAJOR% LSS 3 (
    echo ERROR: Python 3.11+ required, found %PYVER%
    exit /b 1
)
if %PYMAJOR%==3 if %PYMINOR% LSS 11 (
    echo ERROR: Python 3.11+ required, found %PYVER%
    exit /b 1
)

echo       Python %PYVER% found. OK.

REM ── Step 2: Create .venv if missing ─────────────────────────────────────────

echo [2/6] Checking virtual environment...

if not exist ".venv" (
    echo       Creating virtual environment...
    python -m venv .venv
    if errorlevel 1 (
        echo ERROR: Failed to create virtual environment.
        exit /b 1
    )
    echo       Virtual environment created.
) else (
    echo       Virtual environment already exists. OK.
)

REM Activate venv
call .venv\Scripts\activate.bat

REM ── Step 3: Install Python dependencies ─────────────────────────────────────

echo [3/6] Installing Python dependencies...

pip show task-analyzer >nul 2>&1
if errorlevel 1 (
    echo       Installing task-analyzer with all extras...
    pip install -e ".[all]" --quiet
    if errorlevel 1 (
        echo       Trying without [all] extras...
        pip install -e "." --quiet
        if errorlevel 1 (
            echo ERROR: Failed to install dependencies.
            exit /b 1
        )
    )
    echo       Dependencies installed.
) else (
    echo       Dependencies already installed. OK.
)

REM ── Step 4: Check ANTHROPIC_API_KEY ─────────────────────────────────────────

echo [4/6] Checking API key...

if "%ANTHROPIC_API_KEY%"=="" (
    echo.
    echo  ┌─────────────────────────────────────────────────────────┐
    echo  │  ANTHROPIC_API_KEY is not set.                          │
    echo  │                                                         │
    echo  │  TraceAI needs a Claude API key to run investigations.  │
    echo  │  Get one at: https://console.anthropic.com/             │
    echo  └─────────────────────────────────────────────────────────┘
    echo.
    set /p "API_KEY=Enter your Anthropic API key (or press Enter to skip): "
    if not "!API_KEY!"=="" (
        setx ANTHROPIC_API_KEY "!API_KEY!" >nul 2>&1
        set ANTHROPIC_API_KEY=!API_KEY!
        echo       API key saved to environment variables.
    ) else (
        echo       Skipped. Set ANTHROPIC_API_KEY later before investigating.
    )
) else (
    echo       ANTHROPIC_API_KEY is set. OK.
)

REM ── Step 5: Build VS Code extension ─────────────────────────────────────────

echo [5/6] Building VS Code extension...

if exist "vscode-extension\package.json" (
    pushd vscode-extension

    REM Check if node_modules exists
    if not exist "node_modules" (
        echo       Installing npm dependencies...
        call npm install --quiet 2>nul
        if errorlevel 1 (
            echo       WARNING: npm install failed. VS Code extension may not work.
            echo       Make sure Node.js is installed: https://nodejs.org
        )
    ) else (
        echo       npm dependencies already installed. OK.
    )

    REM Compile TypeScript
    if not exist "out\extension.js" (
        echo       Compiling TypeScript...
        call npm run compile 2>nul
        if errorlevel 1 (
            echo       WARNING: TypeScript compilation failed.
        ) else (
            echo       Extension compiled.
        )
    ) else (
        echo       Extension already compiled. OK.
    )

    popd
) else (
    echo       VS Code extension not found. Skipping.
)

REM ── Step 6: Summary ─────────────────────────────────────────────────────────

echo [6/6] Bootstrap complete!
echo.
echo  ╔══════════════════════════════════════════════════════════════╗
echo  ║                                                              ║
echo  ║   TraceAI is ready!                                         ║
echo  ║                                                              ║
echo  ║   Next steps:                                                ║
echo  ║   1. Open VS Code in this folder                            ║
echo  ║   2. The extension will auto-start the server               ║
echo  ║   3. Run 'TraceAI: Run Setup Wizard' from command palette   ║
echo  ║   4. Configure your ticket source (Azure DevOps/Jira/GitHub)║
echo  ║   5. Click any task to investigate!                         ║
echo  ║                                                              ║
echo  ║   Data directory: %%USERPROFILE%%\.traceai\                    ║
echo  ║   Server port:    7420                                       ║
echo  ║                                                              ║
echo  ╚══════════════════════════════════════════════════════════════╝
echo.

endlocal
