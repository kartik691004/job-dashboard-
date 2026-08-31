# Pending work — LinkedIn Hiring Intelligence pipeline

Updated 2026-08-25. Read `AI_CONTEXT.md` first: it is the plan of record.

## Done in the 2026-08-24/25 sessions

1. **Sheet migrated to the 19-column txt-spec schema** (`Company Name ... Status`,
   defined once in `app.sheets_writer.EXPECTED_HEADERS`). All 124 legacy rows were
   mapped losslessly-in-practice; backups git-tracked under `data/analysis/`.
2. **Broken scraper replaced**: orchestrator now uses `datadoping/linkedin-posts-search-scraper`
   (`app/sources/datadoping_source.py`, verified live 2026-08-23). One bulk run covers
   all keywords.
3. **Scheduler live**: `run_daily.py` forces DRY_RUN=false, checks Apify spend, task
   **"JobDashboard LinkedIn Pipeline"** fires Mon/Wed/Fri 01:00 IST.
4. **Control hub connected**: `index.html` renders real `/leads` data and triggers `POST /run`.
5. **Cold-mail outreach step built (2026-08-25)**: `app/outreach.py` + `run_cold_outreach.py`
   (--live) + `POST /outreach`. Mails Status=New rows that carry a real Cold Email,
   flips Status to Contacted. Guarded by name-resolution, gid=0 refusal, header check,
   Status-cell-only writes, per-run cap, DRY_RUN. Suite green at **132 passed / 1 skipped**
   (`tests/test_outreach.py` covers it with fake worksheet/sender).
6. **Cleanup pass done (2026-08-25)**:
   - `src/exporters/google_sheets_exporter.py` guarded (FORBIDDEN_SPREADSHEET_IDS,
     no implicit create, LEGACY_EXPORTER_OVERWRITE opt-in for clear()).
   - `update_gs.py` no longer disables SSL verification.
   - All remaining `verify=False` / `session.verify = False` removed from the legacy
     scrapers (`src/scrapers/apify_client.py`, `apify_service.py`, `utils.py`);
     SSL failures now hard-stop instead of retrying insecurely.
   - `run_full_pipeline.py._write_sheet` refuses the protected "LinkedIn Hiring Leads" tab.
   - Superseded files deleted: `tools/migrate_schema.py`, `tools/import_ftb.py`,
     `tools/inspect_ftb*.py`, `tools/measure_geo_candidates.py`, `app/schema.py`,
     `app/config.py.old-6-categories.bak`, `old-local-edits.patch`.
   - README.md reconciled with the app/ layer.
7. **Sheet filtered to current standards (2026-08-25)**: the 124 tab rows were
   legacy-era accepts (68 x "Product Manager", 14 x "Game Developer", reason
   "Deterministic Match") that predated the strict FO/CoS classifier. Re-judged
   through the live classifier via `tools/filter_sheet_by_standards.py --live`:
   **11 kept, 113 dropped** (96 no role keyword, 8 unclear-India/comp-only,
   6 internship, 1 intl, 1 congratulatory, 1 no hiring intent). Kept rows are
   byte-for-byte unchanged; backup: `data/analysis/filter_backup_20260825T174317Z.json`.
   Post-write verification: header intact, 11 rows, Source Link set exact.

## Known issues, not yet fixed

- **0 leads carry a Cold Email so far** — LinkedIn posts rarely contain addresses;
  extraction cannot conjure ones that were never posted. Until enrichment exists,
  the outreach step has nothing to send (it will report candidates_found=0).
- **Scheduled task logon mode is "Interactive only"** — runs only while someone is
  logged in. Switch to SYSTEM (/RU) or keep the PC signed in at 01:00.

## Operational constraints (unchanged)

- Write ONLY to spreadsheet `15fuzMFlSj2zVaseYlMYdfRaFkmCeUoCyrvKxV6mxLis`
  (JOBDASHBOARD), worksheet exactly `LinkedIn Hiring Leads`. Its first tab (gid=0) is a
  protected Job Board: never read, append, or restructure it.
- Spreadsheet `1f6b_QgkmeOAu0XFpiYyDHMgfkXFKEsrX4gl8IjWbI1k` (FTB internships) is
  READ-ONLY reference material.
- Apify FREE plan: $5.00 per cycle starting the 11th; ~$0.00155/post,
  ~$0.30 per run at limit=10 → three runs/week max. Spend was $2.4853 on 2026-08-23.
- If a scrape ever returns zero posts, check dataset records for an `error` key before
  blaming the pipeline (that trap hid a broken actor for days).
