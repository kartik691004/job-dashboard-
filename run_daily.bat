@echo off
REM Daily pipeline runner for Windows Task Scheduler
REM This script runs the startup intelligence pipeline daily at 1:00 AM IST

cd /d "%~dp0"
python run_daily.py --limit 100 >> logs\scheduled_run.log 2>&1
