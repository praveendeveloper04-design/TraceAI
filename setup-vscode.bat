@echo off
REM ============================================================================
REM  TraceAI — VS Code Extension Setup
REM  Run this ONCE to install and build the VS Code extension.
REM  Then press F5 inside VS Code to launch it.
REM ============================================================================

title TraceAI - VS Code Extension Setup
color 0A
cd /d "%~dp0"

echo.
echo  ============================================
echo    TraceAI VS Code Extension Setup
echo  ============================================
echo.

REM --- Check Node.js ---
echo [1/4] Checking Node.js...
node --version >nul 2>&1
if %ERRORLEVEL% neq 0 (
    color 0C
    echo  ERROR: Node.js is not installed.
    echo  Download from: https://nodejs.org/
    pause
    exit /b 1
)
for /f "tokens=1" %%i in ('node --version 2^>^&1') do set NODEVER=%%i
echo  Found Node.js %NODEVER%
echo.

REM --- Check npm ---
echo [2/4] Checking npm...
npm --version >nul 2>&1
if %ERRORLEVEL% neq 0 (
    color 0C
    echo  ERROR: npm is not installed.
    pause
    exit /b 1
)
for /f "tokens=1" %%i in ('npm --version 2^>^&1') do set NPMVER=%%i
echo  Found npm %NPMVER%
echo.

REM --- Install dependencies ---
echo [3/4] Installing extension dependencies...
cd vscode-extension
npm install
if %ERRORLEVEL% neq 0 (
    color 0C
    echo  ERROR: npm install failed.
    pause
    exit /b 1
)
echo  Dependencies installed.
echo.

REM --- Compile TypeScript ---
echo [4/4] Compiling TypeScript...
npm run compile
if %ERRORLEVEL% neq 0 (
    color 0C
    echo  ERROR: TypeScript compilation failed.
    pause
    exit /b 1
)
echo  Extension compiled successfully.
echo.

REM --- Done ---
color 0A
echo  ============================================
echo    VS Code Extension Ready!
echo  ============================================
echo.
echo  To use the extension:
echo.
echo    1. Open the vscode-extension folder in VS Code:
echo       code "%~dp0vscode-extension"
echo.
echo    2. Press F5 to launch Extension Development Host
echo.
echo    3. In the new VS Code window:
echo       - Click the TraceAI icon in the sidebar
echo       - Use Ctrl+Shift+P ^> "TraceAI: Fetch My Tasks"
echo.
echo  NOTE: Make sure the API server is running first:
echo        Run "run.bat" and select option 7 (Start API Server)
echo.
echo  ============================================
echo.
pause
