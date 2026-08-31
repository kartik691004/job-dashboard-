# LinkedIn Hiring Intelligence

A Python/FastAPI backend that discovers LinkedIn posts where an **Indian company is actively hiring for a Founder's Office or Chief of Staff role**, classifies them through a strict quality gate, and writes rich leads to a Google Sheet. Quality over quantity: only posts that pass all seven classification stages become leads.

## How it works

```text
1. Scrape        one bulk Apify actor run over all SEARCH_QUERIES
                 (datadoping/linkedin-posts-search-scraper)
        |
2. Deduplicate   drop posts already seen in this batch
        |
3. Classify      7-stage pipeline in app/classifier.py:
                 exclusions -> role keywords -> hiring-intent proximity
                 -> India-only market gate -> field extraction
                 -> confidence scoring -> acceptance gate
        |
4. Sheet dedupe  skip rows whose Source Link already exists in the tab
        |
5. Write         append accepted leads to Google Sheets
```

Extracted fields include exact role, company, CTC, cold email (when posted), hiring manager name/LinkedIn, location, experience requirement, employment type and a confidence score.

## The 19-column schema

Defined once in `app/sheets_writer.EXPECTED_HEADERS`:

Company Name, Major Category, Exact Role, CTC, Cold Email, Hiring Manager Name, Hiring Manager LinkedIn, Source Link, Description, Confidence Score, Location, Type, Experience Requirement, Market, India Relevance, Post Date, Scraped At, Classification Reason, Status

## API

Run the server with `python app/main.py` (default `http://localhost:8000`).

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/health` | Service status |
| `POST` | `/run?limit=10` | Trigger scrape + classify + write (`limit` = max posts per keyword batch) |
| `GET` | `/leads?limit=200` | Read recent leads from the sheet, newest first, with KPIs |
| `POST` | `/outreach?max_per_run=5` | Cold-email Status=New leads that carry a real Cold Email; flips them to Contacted (respects DRY_RUN) |
| `GET` | `/` | Serves `index.html`, the control-hub dashboard |

## Cold outreach

The pipeline writes leads with `Status=New`. When a lead carries a real `Cold Email`
address (rare — posts seldom include one), the outreach step mails it and flips
Status to `Contacted`:

```bash
python run_cold_outreach.py            # dry-run preview from .env's DRY_RUN
python run_cold_outreach.py --live     # actually send + update Status cells
python run_cold_outreach.py --live --max 5
```

Configuration lives in `.env` (`SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`,
`SMTP_PASSWORD`, `OUTREACH_FROM_EMAIL`, `OUTREACH_SENDER_NAME`,
`OUTREACH_MAX_PER_RUN`) — see `.env.example`. Safety: worksheet resolved by name,
gid=0 refused, header schema verified before any write, only the Status cell of
mailed rows is modified, sends capped per invocation, and nothing sends while
`DRY_RUN=true`.

## Scheduling

Windows scheduled task **"JobDashboard LinkedIn Pipeline"** runs `run_daily.bat` -> `run_daily.py` Mon/Wed/Fri at 01:00 IST, logging to `logs\scheduled_run.log`. `run_daily.py` forces `DRY_RUN=false` for scheduled runs and checks remaining Apify budget before scraping, aborting if the projected cost exceeds the cycle remainder.

## Google Sheets safety boundaries

- Writes go ONLY to spreadsheet `15fuzMFlSj2zVaseYlMYdfRaFkmCeUoCyrvKxV6mxLis` (JOBDASHBOARD), worksheet exactly **"LinkedIn Hiring Leads"**.
- Its first tab (gid=0) is an existing Job Board: never read, append, or restructure it.
- Spreadsheet `1f6b_QgkmeOAu0XFpiYyDHMgfkXFKEsrX4gl8IjWbI1k` (FTB internships) is READ-ONLY reference material.
- With `DRY_RUN=true` in `.env`, the pipeline scrapes and classifies but never modifies the sheet.

Before touching sheets manually, run `python verify_sheets.py` — a read-only preflight that checks auth, worksheet-by-name resolution and header compatibility.

## Apify budget discipline

Free plan: $5.00 per cycle starting on the 11th; ~$0.00155/post, ~$0.30 per run at `limit=10` — so at most three runs per week. Check spend before raising limits. If a scrape returns zero posts, check dataset records for an in-band `error` key before blaming the pipeline code.

## Project layout

```text
app/
  main.py           FastAPI server (/health, /run, /leads, /outreach, /)
  orchestrator.py   scrape -> dedupe -> classify -> sheet-dedupe -> write
  classifier.py     strict 7-stage classification pipeline
  outreach.py       cold-mail step: Status=New + real email -> send -> Contacted
  config.py         env vars, search queries, keyword lists, thresholds
  models.py         RawPost / ClassifiedPost Pydantic models
  sheets_writer.py  guarded Google Sheets writes, EXPECTED_HEADERS schema
  geo.py            India-market geography helpers
  sources/          Apify integrations (datadoping_source.py is live)
tests/              pytest suite (132 passed, 1 skipped)
tools/              one-off migration utilities
data/               run artifacts, backups, analysis dumps
docker/             Dockerfile + docker-compose.yml
n8n/                legacy workflow export
index.html          control hub wired to /health, /leads, POST /run
run_daily.py/.bat   scheduler entry point
run_cold_outreach.py manual cold-mail entry point (--live to send)
verify_sheets.py    read-only sheets preflight
```

The older `src/` layer (startup-discovery scrapers, Excel/GSheets exporters) is legacy and inert in the live pipeline; its Google exporter is guarded against touching production spreadsheets.

## Development

```bash
pip install -r requirements.txt
copy .env.example .env        # add service-account credentials + SHEET_ID
python -m pytest              # test suite
python verify_sheets.py       # read-only preflight
python app/main.py            # start API
```
