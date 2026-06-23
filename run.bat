@echo off
title Self-Learning Telegram Bot
echo Starting Self-Learning Telegram Bot...
echo Checking Python version...
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo Python is not installed or not in PATH! Please install Python 3.8+.
    pause
    exit /b
)

echo Checking dependencies...
python -c "import aiogram; import aiohttp" >nul 2>&1
if %errorlevel% neq 0 (
    echo aiogram or aiohttp not found. Installing...
    pip install aiogram aiohttp
)

echo.
echo Starting bot (main.py)...
echo To stop the bot, press Ctrl+C or close this window.
echo --------------------------------------------------
python main.py
echo.
echo Bot stopped or crashed.
pause
