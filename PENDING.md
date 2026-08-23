# Pending work — LinkedIn Hiring Intelligence pipeline

Updated 2026-08-24 after completing the tasks this file previously tracked. Read
`AI_CONTEXT.md` first: it is the plan of record, and the FTB-mirror plan below was
superseded by it.

## Done in the 2026-08-24 session

1. **Sheet migrated to the 19-column txt-spec schema** (`Company Name ... Status`,
   defined once in `app.sheets_writer.EXPECTED_HEADERS`). All 124 legacy rows were
   mapped losslessly-in-practice: every Post URL survives as Source Link, 91/124 got a
   Location recovered from their India evidence, Author Name/URL moved into Hiring
   Manager fields. Backup: `data/analysis/migration19_backup_20260823T202644Z.json`
   (git-tracked) plus `jobdashboard_linkedin_leads_backup_pre_migration.json`.
2. **Irreplaceable artifacts pulled out of gitignore** with `git add -f`: pre-migration
   snapshot, FTB reference raw, geo projection corpus, prod run log.
3. **Formula-mangling scan clean**: zero cells starting with `= + - @`, none over the
   50k-char limit, across both snapshots. Migration wrote with `RAW` anyway.
4. **Broken scraper replaced**: orchestrator now uses `datadoping/linkedin-posts-search-scraper`
   (`app/sources/datadoping_source.py`, verified live 2026-08-23) instead of
   `supreme_coder/linkedin-post`, whose content search has returned
   `{"error": "No posts found"}` since 2026-08-23. One bulk run covers all keywords.
5. **Scheduler live**: `run_daily.py` forces DRY_RUN=false for scheduled runs, checks
   Apify spend before scraping, and aborts when the projected cost exceeds the cycle
   remainder. Task **"JobDashboard LinkedIn Pipeline"** fires Mon/Wed/Fri 01:00 IST via
   `run_daily.bat` -> `logs\scheduled_run.log`.
6. **Control hub connected**: `index.html` now renders real `/leads` data (newest
   first, KPIs for total/new/with-email), triggers `POST /run`, and is served at `/`.
7. `verify_sheets.py` reads its expected header from `app.sheets_writer`; suite green
   at **107 passed / 1 skipped**.

## Superseded: the FTB-mirror layout

The earlier plan mirrored FTB's 20 columns + 5 audit columns (`app/schema.py`,
`tools/migrate_schema.py`, `tools/import_ftb.py`). It lost to the txt-spec layout the
moment the classifier was narrowed to Founder's Office / Chief of Staff. Those files
are kept for reference but are **inert**: `import_ftb.py` imports `ROLE_CATEGORIES`,
which no longer exists in `app/config.py`, and both scripts would refuse to run against
the migrated header anyway. Delete them whenever you are comfortable; do NOT run them.

## Known issues, not yet fixed

- **`src/exporters/google_sheets_exporter.py`** has an unguarded `worksheet.clear()`
  (~line 229) and `client.create()` (~line 97). Inert only because `.env` has no
  `GOOGLE_SHEETS_ID` for the old `src/` layer. Guard or delete.
- **`update_gs.py` disables SSL verification** globally (`verify=False`). Cleanup
  candidate; not on any critical path.
- **Consolidation leftovers at repo root**: `app/config.py.old-6-categories.bak` and
  `old-local-edits.patch` preserve the losing side of the config conflict. Safe to
  delete now that the txt-spec stack is committed.
- **README.md** still documents only the old `src/` startup-discovery layer.
- **Scheduled task logon mode is "Interactive only"** — runs only while someone is
  logged in. Switch to SYSTEM (/RU) or keep the PC signed in at 01:00.
- **0 leads carry a Cold Email so far** — LinkedIn posts rarely contain addresses;
  extraction cannot conjure ones that were never posted.

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
