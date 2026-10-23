@echo off
chcp 65001 >nul
cd /d "%~dp0.."
if not exist ".env" (
  echo [KnowLens] .env not found. Copy .env.example to .env and fill in the model settings.
  pause
  exit /b 1
)
echo [KnowLens] starting ... http://127.0.0.1:8633
python run.py