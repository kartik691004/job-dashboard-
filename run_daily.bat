@echo off
REM Scheduled pipeline runner for Windows Task Scheduler
REM Task "JobDashboard LinkedIn Pipeline" fires Mon/Wed/Fri 01:00 IST.
REM Cadence is 3x/week, not daily: one run costs ~$0.30 of the $5.00/month
REM Apify FREE cap, and daily runs would exhaust it mid-cycle.

cd /d "%~dp0"
if not exist logs mkdir logs

echo ===== %DATE% %TIME% ===== >> logs\scheduled_run.log
python run_daily.py --limit 10 >> logs\scheduled_run.log 2>&1
echo. >> logs\scheduled_run.log
