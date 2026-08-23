# LinkedIn Hiring Intelligence - AI Context & Plan

*This document serves as the single source of truth for OpenCode (or any other AI agent) working on this repository.*

## 1. Project Overview
This project is an automated Python backend (FastAPI) that discovers LinkedIn posts indicating an Indian company is actively hiring for **Founder's Office / Chief of Staff** roles. It scrapes posts via Apify, runs them through a strict 7-stage classification pipeline (exclusions -> role keywords -> hiring-intent proximity -> India-only market gate -> field extraction -> confidence scoring -> acceptance gate), deduplicates the results, and writes rich 19-column leads to a Google Sheet. Quality > Quantity.

## 2. CRITICAL Safety Rules (Google Sheets)
- **Target Sheet:** Uses the spreadsheet defined by SHEET_ID in .env. Current target: **JOBDASHBOARD** (15fuzMFlSj2zVaseYlMYdfRaFkmCeUoCyrvKxV6mxLis). The previous target FTB internships (1f6b_QgkmeOAu0XFpiYyDHMgfkXFKEsrX4gl8IjWbI1k) is READ-ONLY for the service account - do not write there.
- **NEVER TOUCH gid=0:** The first tab of the spreadsheet is an existing Job Board. It is strictly **READ/WRITE PROTECTED**. Do not modify headers, append rows, or read from it.
- **Target Worksheet:** All reads and writes MUST go to the worksheet named LinkedIn Hiring Leads (configured via GOOGLE_SHEET_WORKSHEET).
- **Dry Runs:** If DRY_RUN=true is set in .env, the pipeline must scrape and classify but MUST NOT modify the Google Sheet.

## 3. Architecture & Current State
- **Entry point:** `app/main.py` (FastAPI server: GET /health, POST /run, GET /leads, GET / serves index.html).
- **Orchestrator:** `app/orchestrator.py` - scrape (one bulk actor run over all SEARCH_QUERIES) -> deduplicate -> classify -> sheet-deduplicate -> write.
- **Scraper:** `app/sources/datadoping_source.py` - `datadoping/linkedin-posts-search-scraper`, ~$0.00155/post, all keywords in ONE run. Replaced `supreme_coder/linkedin-post`, whose content search returns "No posts found" since 2026-08-23 (`app/sources/apify_source.py` is kept but unused).
- **Classifier:** `app/classifier.py` - strict 7-stage pipeline. FO/CoS only; internships, job-seeker, congratulatory and informational posts excluded; India-only gate; extracts exact role, company, CTC, email, location, experience, employment type; scores confidence against CONFIDENCE_THRESHOLD.
- **Config:** `app/config.py` - environment variables, targeted SEARCH_QUERIES, strict keyword lists, exclusions, India signals, PROXIMITY_CHAR_LIMIT.
- **Data Export:** `app/sheets_writer.py` - safe Google Sheets interaction with two-level deduplication. EXPECTED_HEADERS defines the **19-column schema**: Company Name, Major Category, Exact Role, CTC, Cold Email, Hiring Manager Name, Hiring Manager LinkedIn, Source Link, Description, Confidence Score, Location, Type, Experience Requirement, Market, India Relevance, Post Date, Scraped At, Classification Reason, Status.
- **Models:** `app/models.py` - RawPost (incl. job_card_location from LinkedIn job cards) and ClassifiedPost Pydantic models.
- **Tests:** `tests/test_classifier.py`, `tests/test_dedup.py`, `tests/test_api.py`, `tests/test_core.py`, `tests/test_geo.py` - 107 passed, 1 skipped.
- **Verification:** `verify_sheets.py` - read-only preflight (auth, worksheet-by-name safety, header compatibility, URL column).
- **Scheduler:** Windows task "JobDashboard LinkedIn Pipeline" runs `run_daily.bat` Mon/Wed/Fri 01:00 IST -> `run_daily.py` (forces DRY_RUN=false, checks Apify budget first).
- **Dashboard:** `index.html` control hub wired to /health, /leads and POST /run.
- **Status (2026-08-24):** Live tab migrated to the 19-column layout with all 124 rows preserved (backup in data/analysis/, git-tracked). Suite green.

## 4. GitHub Repository
- **Active Repo:** https://github.com/kartik691004/job-dashboard-.git
- Local main project is this directory (`C:\Users\kartik\job dashboard`). The sibling `job-dashboard-new` clone was a mistake and may be deleted; its unique state was consolidated here on 2026-08-24.

## 5. Where We Left Off / Next Steps
Completed: V3 strict classifier; first production write (107 rows, 2026-08-23); sheet migrated to the 19-column txt-spec schema; scraper swapped to a working actor; scheduler registered; dashboard connected; backups git-tracked.

Remaining:
1. **Outreach workflow:** leads carry Status=New; build the cold-mail step once emails exist (posts rarely contain addresses - consider enriching via hiring-manager profiles).
2. **Guard or delete** `src/exporters/google_sheets_exporter.py` (unguarded clear()/create()) and clean up `update_gs.py` (SSL verification disabled).
3. **Reconcile README.md** with the app/ layer; delete superseded files (`tools/migrate_schema.py`, `tools/import_ftb.py`, `app/schema.py`, consolidation .bak/.patch leftovers).
4. **Actor health monitoring:** if a scrape ever returns zero posts, check dataset records for an in-band `error` key before blaming pipeline code.
5. **Apify budget discipline:** $5/cycle from the 11th; ~$0.30/run at limit=10; three runs/week max. Check spend before raising limits.

---
**Agent Instruction:** Please read this entire document before executing any commands or editing code. Respect the Google Sheets safety boundaries above all else. Current pending-work detail lives in PENDING.md.
