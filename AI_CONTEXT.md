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
- **Outreach:** `app/outreach.py` - cold-mail step. Finds Status=New rows with a real Cold Email, sends a plain-text application note over SMTP (env: SMTP_HOST/PORT/USER/PASSWORD, OUTREACH_FROM_EMAIL, OUTREACH_SENDER_NAME), flips Status to "Contacted". Guards: worksheet by name only, refuses gid=0, verifies headers before writing, touches ONLY the Status cell of mailed rows, caps sends via OUTREACH_MAX_PER_RUN, and never sends while DRY_RUN=true. Entry points: `run_cold_outreach.py` (--live to send) and POST /outreach on the API.
- **Models:** `app/models.py` - RawPost (incl. job_card_location from LinkedIn job cards) and ClassifiedPost Pydantic models.
- **Tests:** `tests/test_classifier.py`, `tests/test_dedup.py`, `tests/test_api.py`, `tests/test_core.py`, `tests/test_geo.py`, `tests/test_outreach.py`, `tests/test_llm_gate.py`, `tests/test_orchestrator_gate.py`, `tests/test_extraction_fixes.py`, `tests/test_llm_providers.py` - **198 passed, 1 skipped**.
- **Verification:** `verify_sheets.py` - read-only preflight (auth, worksheet-by-name safety, header compatibility, URL column).
- **Scheduler:** Windows task "JobDashboard LinkedIn Pipeline" runs `run_daily.bat` Mon/Wed/Fri 01:00 IST -> `run_daily.py` (forces DRY_RUN=false, checks Apify budget first).
- **Dashboard:** `index.html` control hub wired to /health, /leads and POST /run.
- **Status (2026-08-25):** Live tab migrated to the 19-column layout, then FILTERED to current standards: 124 legacy rows (mostly pre-V3 "Product Manager"/"Game Developer" era) re-judged through the live classifier -> 11 genuine India FO/CoS leads kept, 113 dropped. Backup: data/analysis/filter_backup_20260825T174317Z.json. Tool: tools/filter_sheet_by_standards.py (dry-run default, --live + DRY_RUN=false to write). Suite green at 139 passed / 1 skipped.

## 4. GitHub Repository
- **Active Repo:** https://github.com/kartik691004/job-dashboard-.git
- Local main project is this directory (`C:\Users\kartik\job dashboard`). The sibling `job-dashboard-new` clone was a mistake and may be deleted; its unique state was consolidated here on 2026-08-24.

## 5. Where We Left Off / Next Steps
Completed (2026-08-24/25): V3 strict classifier; sheet migrated to the 19-column txt-spec schema; scraper swapped to a working actor; scheduler registered; dashboard connected; backups git-tracked; **cold-mail outreach step built** (`app/outreach.py` + `run_cold_outreach.py` + POST /outreach); legacy `src/exporters/google_sheets_exporter.py` guarded behind FORBIDDEN_SPREADSHEET_IDS + LEGACY_EXPORTER_OVERWRITE; `update_gs.py` and all legacy scrapers cleaned of TLS verify=False; superseded files deleted (`tools/migrate_schema.py`, `tools/import_ftb.py`, `tools/inspect_ftb*.py`, `tools/measure_geo_candidates.py`, `app/schema.py`, config .bak, old-local-edits.patch); README reconciled with the app/ layer; actor in-band `error` records collected in `DataDopingSource.last_errors` and surfaced by the orchestrator; **sheet filtered to current standards** (124 -> 11 via tools/filter_sheet_by_standards.py, backup data/analysis/filter_backup_20260825T174317Z.json); production run 2026-08-25 added 15 fresh leads (26 rows total); classifier Stage-5 extraction weaknesses identified (curly-apostrophe role regex, brittle company regex -> many "Unclear" cells).

## 6. ACTIVE DIRECTIVE: Groq LLM Quality Gate — IMPLEMENTED + PHASE 4 DRY_RUN VALIDATED

User brief: HIGH-PRECISION LLM QUALITY GATE + STRUCTURED EXTRACTION. Precision > quantity; false positives worse than missed borderline leads. Standing user rule honored: context file updated FIRST when the prompt arrived. Plan APPROVED with clarifications: (1) Review leads are never confirmed leads — Status exactly "Review"; ACCEPT = "New". (2) Contract/Unclear employment is conservative -> REVIEW, never auto-accept (full-time is the product). (3) Stage-5 regex fixes only narrow/display-level.

Architecture (live):
```
APIFY -> STAGE 1 deterministic filter (unchanged) -> CANDIDATES
       -> STAGE 2 app/llm Verifier (GroqProvider|GeminiProvider, JSON mode) -> ACCEPT/REVIEW/REJECT
       -> Python hard gates (_gate) -> enriched_post() -> sheet dedup -> Sheets
```

Implemented:
- `app/llm/base.py` (LLMProvider protocol + LLMError), `schemas.py` (LeadVerdict: 20-field spec, hostile-input coercion to safe defaults; GateResult), `groq_provider.py` + `gemini_provider.py` (httpx, JSON mode, key never logged), `verifier.py` (SYSTEM_PROMPT encoding all product rules + anti-injection delimiters, retry-once->REVIEW failure safety, hard gates: wrong-role/negation/aggregator/advice/update/internship/no-India are Python-enforced REJECTs the LLM cannot override; caps: confidence<0.90, employment Contract/Unclear, LLM REVIEW -> Status=Review).
- Provider auto-selection: `LLM_PROVIDER=auto` picks Groq if GROQ_API_KEY set, else Gemini if GEMINI_API_KEY+GEMINI_MODEL set, else None (disabled -> all Review). Explicit `groq`/`gemini` forces one. `Verifier(None)` is explicit DISABLED (sentinel `_AUTO` distinguishes "auto" from "off").
- **429 rate-limit backoff** added to BOTH providers (separate budget, exponential backoff honoring Retry-After) so transient throttling rides out instead of degrading to REVIEW.
- **Unicode-safe logging** in `sheets_writer._safe_print` (re-encodes to the real stdout encoding with errors=replace) so posts with non-cp1252 chars (e.g. non-breaking hyphen) never crash the run.
- Orchestrator REJECT sample bug fixed: now uses `gate.reasons` verbatim (previously appended the LLM's positive `verdict.reason` onto hard-gate rejects, producing contradictory/duplicated text).
- `enriched_post()` folds verified extraction into ClassifiedPost only where better than deterministic defaults.
- Orchestrator: gate between classify and dedup; per-run detail captured (`_accepted_details`, `_review_details`, `_rejected_samples`); metrics (deterministic_candidates, llm_calls, accepted, review, llm_rejected); write skipped when nothing to write.
- Classifier Stage-5 narrow fixes: curly-apostrophe role regex; "<Company> is hiring" company fallback with pronoun guard. Display-only; scope unchanged.
- Config/env: GROQ_API_KEY, GROQ_MODEL (live = `openai/gpt-oss-120b`; `llama-3.3-70b-versatile` 404s on Groq now), GEMINI_API_KEY/MODEL, LLM_PROVIDER, GROQ_TIMEOUT_S, GROQ_MAX_RETRIES; .env.example placeholders + LLM_PROVIDER docs.
- Tests: test_llm_gate.py, test_orchestrator_gate.py, test_extraction_fixes.py, test_llm_providers.py (provider selection + transports + 429 backoff). **Suite: 198 passed / 1 skipped.**

PHASE 4 DRY_RUN — COMPLETED 2026-08-26 (exactly one clean run, DRY_RUN=true, limit=10, NEVER run_daily.py):
- Apify token switched to a fresh account (`exalted_delight`) after the original hit its monthly hard limit.
- Result: scraped 128, unique 76, deterministic candidates 15, Groq calls 15, **ACCEPT 3 / REVIEW 3 / REJECT 9**, current-run dups 52, sheet dups 0, new_rows 6, errors 0. No production write.
- 3 ACCEPTs (all Status=New, Full-time, India): Founder's Office Associate (Jaipur, 0.97, email Hr.buddinggroup@gmail.com); Founder's Office Executive (Zestify_365, Ahmedabad, 0–2 yrs, 0.98); Founder's Office Associate (Protip, New Delhi, 1–2 yrs, 0.99).
- REJECTs correctly caught: Executive Assistant (not target role), job-aggregator roundups, success narratives, "not a current job opening". REVIEWs = confidence<0.90 or employment_type Unclear (held, never auto-accepted). Gate logic confirmed correct and conservative.
- Known: GEMINI_API_KEY in .env is INVALID (24 chars, not an `[redacted] key) — Gemini path is built but unusable; Groq is the active path.

Next: user reviews lead quality. Only after approval, flip DRY_RUN=false for a production write. No production write has occurred.

## 6b. PHASE 5 — DATA-QUALITY HARDENING (2026-08-26, user-supplied real-dataset correction)

User reviewed the live dataset and found the gate still too permissive. Mandate: HIGH-PRECISION over quantity; a false positive is worse than a missed borderline lead. Implemented:
- **Schema** (`app/llm/schemas.py`): added `is_aggregator`, `has_negation`, `is_experience_requirement_only`, `actual_role`; extended `EMPLOYMENT_TYPES` with Part-time/Freelance/Temporary; added `NON_ACCEPT_EMPLOYMENT`.
- **SYSTEM_PROMPT** (`app/llm/verifier.py`): rewritten around the ACTUAL-VACANCY test (post mentions Founder's Office ≠ the vacancy), explicit aggregator/negation/experience-only/hiring-manager/company/extraction rules, full-time-only, strict ACCEPT criteria.
- **`_gate` final validation**: new hard REJECTs for `is_aggregator`, `has_negation`, `is_experience_requirement_only`; non-full-time employment held at REVIEW (never auto-accepted); LLM ACCEPT is overridden whenever a hard rule fails (§13).
- **Regression suite** `tests/test_data_quality_regression.py`: covers Razorpay (exp-only), Deployment Inc (negation), Find Me The Job / CareerBee (aggregators), Founders Find (advice), Smytten (update), TalentoIndia (wrong actual role), CRM Executive / Growth Officer (functional-role false positives), §13 override cases, plus genuine NXP/Luar Beauty/Medulance/Amama Partners FO/CoS examples (§15 do-not-overcorrect). Offline (scripted provider).
- Full suite: **218 passed / 1 skipped**.

Next: run ONE live DRY_RUN (limit=10, DRY_RUN=true) to confirm the real LLM obeys the new prompt on live data; then STOP for user review. No production write.

### Phase 5 live DRY_RUN result (2026-08-26, ONE run, limit=10, DRY_RUN=true)
- Scraped 126, unique 73, deterministic candidates 13, Groq calls 14, **ACCEPT 2 / REVIEW 1 / REJECT 10**, errors 0. No write.
- **Works:** aggregators (SolarSquare/rohan-surana, CareerBee, Find Me The Job), Executive Assistant, experience-only, career advice, success narratives now correctly REJECT. 2 genuine ACCEPTs (Zavity Aerospace FO Bengaluru; Protip FO Associate New Delhi).
- **Over-correction found (violates §15):** `gpt-oss-120b` inconsistently set `is_current_job=false` for 4 GENUINE current openings ("We're #hiring a new Founder's Office – Growth & Partnerships in Jaipur"; "Associate – Founder's Office in Mumbai"; "Zestify_365 Founder's Office Executive Ahmedabad"; "Macro Rides Founder's Office Associate"), hard-rejecting them as "not a current job opening". The TalentoIndia REVIEW was actually a Groq HTTP 400 fallback, not a semantic review.
- **Fix applied (prompt only, no live re-run yet):** added a CURRENT-OPENING SIGNAL block to SYSTEM_PROMPT anchoring `is_current_job=true` on explicit hiring language ("We're hiring", "#hiring", "Apply now", etc.) with examples, and limiting `false` to advice/updates/job-seeker/historical. Offline suite green (218 passed).
- **Recommendation:** a second DRY_RUN to confirm the over-rejection is gone before any production write. Awaiting user approval (step 11 = STOP after first live run).

Remaining (older items):
1. **Email enrichment:** outreach step built but 0 leads carry emails yet.
2. **Scheduled task logon mode is "Interactive only".**
3. **Key hygiene:** GROQ_API_KEY was pasted in chat/screenshot — recommend rotating at console.groq.com.
3. **Apify budget discipline:** ~$4.51 of $5.00 spent on 2026-08-25 — cycle exhausted until Sept 11 reset (one limit=10 run still fits if desired for Phase 4).

---
**Agent Instruction:** Please read this entire document before executing any commands or editing code. Respect the Google Sheets safety boundaries above all else. Current pending-work detail lives in PENDING.md.
