# LinkedIn Hiring Intelligence - AI Context & Plan

*This document serves as the single source of truth for OpenCode (or any other AI agent) working on this repository.*

## 1. Project Overview
This project is an automated Python backend (FastAPI) that discovers LinkedIn posts indicating a company is actively hiring (e.g., for `"Chief of Staff"` or `"Founder's Office"`). It scrapes posts using Apify, classifies them using a deterministic proximity-based algorithm, deduplicates the results, and writes new leads to a Google Sheet.

## 2. CRITICAL Safety Rules (Google Sheets)
- **Target Sheet:** Uses the spreadsheet defined by `SHEET_ID` in `.env`. Current target: **JOBDASHBOARD** (`15fuzMFlSj2zVaseYlMYdfRaFkmCeUoCyrvKxV6mxLis`). The previous target `FTB internships` (`1f6b_QgkmeOAu0XFpiYyDHMgfkXFKEsrX4gl8IjWbI1k`) is READ-ONLY for the service account — do not write there.
- **NEVER TOUCH gid=0:** The first tab of the spreadsheet is an existing Job Board. It is strictly **READ/WRITE PROTECTED**. Do not modify headers, append rows, or read from it.
- **Target Worksheet:** All reads and writes MUST go to the worksheet named `LinkedIn Hiring Leads` (configured via `GOOGLE_SHEET_WORKSHEET`).
- **Dry Runs:** If `DRY_RUN=true` is set in `.env`, the pipeline must scrape and classify but MUST NOT modify the Google Sheet.

## 3. Architecture & Current State
- **Entry point:** `app/main.py` (FastAPI server) and `/run` endpoint.
- **Orchestrator:** `app/orchestrator.py` — scrape -> deduplicate -> classify -> sheet-deduplicate -> write.
- **Scraper:** `app/sources/apify_source.py` — fetches LinkedIn posts via Apify actor.
- **Classifier:** `app/classifier.py` — deterministic proximity-based algorithm. Validates that hiring signals appear near role keywords within the post text.
- **Config:** `app/config.py` — all environment variables and `ROLE_CATEGORIES` dict (categories -> keywords list).
- **Data Export:** `app/sheets_writer.py` — safe Google Sheets interaction with two-level deduplication.
- **Models:** `app/models.py` — RawPost and ClassifiedPost Pydantic models.
- **Tests:** `tests/test_classifier.py`, `tests/test_dedup.py`, `tests/test_api.py` — all passing.
- **Verification:** `verify_sheets.py` / `verify_sheets2.py` — one-off scripts to verify Sheet connectivity.
- **Status:** All tests pass. Dry-run verified against Google Sheet successfully.

## 4. GitHub Repository
- **Active Repo:** https://github.com/kartik691004/job-dashboard-.git  (pushed: 2026-08-13)
- **Old Repo:** https://github.com/kartik691004/startup-outreach-.git  (initial commit only, superseded)

## 5. Next Steps (Where We Left Off)
1. **First Production Write:** Change DRY_RUN=false in .env to perform the first live write to LinkedIn Hiring Leads. Requires user approval.
2. **Phase 2 Expansion:** Add new role categories (e.g., Product Manager, Game Developer) to ROLE_CATEGORIES in `app/config.py`.
3. **Scheduler / Daily Run:** Wire up `run_daily.py` / `run_daily.bat` to run the pipeline at 1:00 AM IST automatically.
4. **Index.html Dashboard:** `index.html` contains a Control Hub UI — connect it to the FastAPI /run, /startups etc. endpoints for a live control panel.

---
**Agent Instruction:** Please read this entire document before executing any commands or editing code. Respect the Google Sheets safety boundaries above all else.
