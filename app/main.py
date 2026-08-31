from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
import gspread
import traceback

from app.config import (
    GOOGLE_SHEETS_CREDENTIALS,
    GOOGLE_SHEETS_ID,
    GOOGLE_SHEET_WORKSHEET,
)
from app.orchestrator import Orchestrator
from app.outreach import OutreachRunner
from app.sheets_writer import EXPECTED_HEADERS

app = FastAPI(title="LinkedIn Hiring Intelligence")

# The control hub (index.html) is served by this API in dev but may also be opened
# straight from disk; allow those cross-origin fetches.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health_check():
    return {"status": "ONLINE", "service": "LinkedIn Hiring Intelligence"}


@app.post("/run")
def run_pipeline(limit: int = 10):
    if limit <= 0:
        raise HTTPException(status_code=422, detail="limit must be a positive integer")
    try:
        orchestrator = Orchestrator()
        summary = orchestrator.run_pipeline(limit=limit)
        return {"status": "success", "summary": summary}
    except Exception as e:
        return {"status": "error", "message": str(e), "traceback": traceback.format_exc()}


@app.get("/leads")
def get_leads(limit: int = 200):
    """Recent leads from the `LinkedIn Hiring Leads` tab, newest first, read-only."""
    if limit <= 0:
        raise HTTPException(status_code=422, detail="limit must be a positive integer")
    try:
        gc = gspread.service_account(filename=GOOGLE_SHEETS_CREDENTIALS)
        worksheet = gc.open_by_key(GOOGLE_SHEETS_ID).worksheet(GOOGLE_SHEET_WORKSHEET)
        if worksheet.id == 0 or worksheet.index == 0:
            raise HTTPException(status_code=500, detail="resolved to the protected gid=0 tab")
        values = worksheet.get_all_values()
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Google Sheets read failed: {e}")

    header = values[0] if values else []
    rows = [dict(zip(header, r)) for r in values[1:] if any(c.strip() for c in r)]
    rows.reverse()  # newest first
    with_email = sum(1 for r in rows if "@"
                     in (r.get("Cold Email") or "") and (r.get("Cold Email") or "") != "Not Available")
    new_count = sum(1 for r in rows if (r.get("Status") or "").lower() == "new")
    return {
        "total": len(rows),
        "status_new": new_count,
        "with_email": with_email,
        "headers": EXPECTED_HEADERS,
        "rows": rows[:limit],
    }


@app.post("/outreach")
def run_outreach(max_per_run: int = 5):
    """Cold-email Status=New leads that carry a real Cold Email address.

    Respects DRY_RUN: with the .env default (true) this previews without
    sending or modifying the sheet. Only the Status cell of mailed rows is
    updated, and never on the protected gid=0 tab.
    """
    if max_per_run <= 0:
        raise HTTPException(status_code=422, detail="max_per_run must be a positive integer")
    try:
        runner = OutreachRunner()
        summary = runner.run(max_per_run=max_per_run)
        return {"status": "success", "summary": summary}
    except Exception as e:
        return {"status": "error", "message": str(e), "traceback": traceback.format_exc()}


@app.get("/", include_in_schema=False)
def dashboard():
    """Serve the control hub UI."""
    return FileResponse("index.html")
