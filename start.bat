@echo off
setlocal

cd /d "%~dp0"

where uv >nul 2>nul
if not errorlevel 1 (
    uv run python scripts\dev.py
    exit /b %errorlevel%
)

if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" scripts\dev.py
    exit /b %errorlevel%
)

echo ERROR: uv or the project virtual environment was not found.
echo Install uv and run: uv sync --extra dev
exit /b 1
