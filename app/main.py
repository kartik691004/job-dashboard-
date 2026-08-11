"""
FastAPI Application — AI Indian Startup Intelligence & Outreach Automation
Serves REST API + control hub UI + weekly Sunday 1 AM scheduler on app startup.
Production-grade: CORS restricted, API key auth, rate limiting, input validation.
"""
import os
import re
import time
import logging
import sys
import asyncio
from collections import defaultdict
from contextlib import asynccontextmanager
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

from fastapi import FastAPI, Query, HTTPException, Request, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.security import APIKeyHeader

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.pipeline.automation_runner import IndianStartupIntelligenceWorkflow
from src.pipeline.scheduler         import start_scheduler
from src.config                     import EXCEL_OUTPUT_PATH, validate_config, BASE_DIR

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("FTB-API")

workflow  = IndianStartupIntelligenceWorkflow()
_scheduler = None
INDEX_HTML = BASE_DIR / "index.html"
_executor  = ThreadPoolExecutor(max_workers=2)

def _get_api_key() -> str:
    return os.getenv("API_KEY", "")


async def _verify_api_key(request: Request):
    api_key = _get_api_key()
    if not api_key:
        return True
    key = request.headers.get("X-API-Key", "")
    if key != api_key:
        raise HTTPException(status_code=401, detail="Invalid or missing API key")
    return True


_RATE_LIMIT_REQUESTS = int(os.getenv("RATE_LIMIT_REQUESTS", "60"))
_RATE_LIMIT_WINDOW  = int(os.getenv("RATE_LIMIT_WINDOW", "60"))
_rate_limit_store: dict[str, list[float]] = defaultdict(list)


def _is_rate_limited(client_ip: str) -> bool:
    now = time.time()
    timestamps = _rate_limit_store[client_ip]
    _rate_limit_store[client_ip] = [t for t in timestamps if now - t < _RATE_LIMIT_WINDOW]
    return len(_rate_limit_store[client_ip]) >= _RATE_LIMIT_REQUESTS


def _record_request(client_ip: str) -> None:
    _rate_limit_store[client_ip].append(time.time())


_COMPANY_NAME_RE = re.compile(r"^[a-zA-Z0-9\s\-\.&']{1,60}$")


async def _rate_limit_middleware(request: Request, call_next):
    client_ip = request.client.host if request.client else "unknown"
    if _is_rate_limited(client_ip):
        raise HTTPException(status_code=429, detail="Too many requests")
    response = await call_next(request)
    _record_request(client_ip)
    return response


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _scheduler
    _scheduler = start_scheduler(workflow.run_daily_workflow)
    schedule_label = "Daily" if os.getenv("SCHEDULER_DAY_OF_WEEK", "*") == "*" else "Weekly"
    logger.info(f"{schedule_label} pipeline scheduler registered")
    for issue in validate_config():
        logger.warning(f"Config issue: {issue}")
    yield
    if _scheduler and _scheduler.running:
        _scheduler.shutdown(wait=False)
        logger.info("Scheduler stopped.")


app = FastAPI(
    title       ="AI Indian Startup Intelligence & Outreach API",
    description ="Discover, rank and outreach recently funded Indian startups — powered by real scraping + Gemini AI.",
    version     ="3.2.0",
    lifespan    =lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        origin.strip()
        for origin in os.getenv("ALLOWED_ORIGINS", "http://localhost:8000,http://127.0.0.1:8000").split(",")
        if origin.strip()
    ] or ["*"],
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
    allow_credentials=False,
)

app.middleware("http")(_rate_limit_middleware)


def _validate_company_name(company: str) -> str:
    cleaned = company.strip()
    if not cleaned:
        raise HTTPException(status_code=400, detail="Company name is required")
    if not _COMPANY_NAME_RE.match(cleaned):
        raise HTTPException(status_code=400, detail="Invalid company name format")
    if len(cleaned) > 60:
        raise HTTPException(status_code=400, detail="Company name too long")
    return cleaned


def _guess_website(company: str) -> str:
    cleaned = company.lower().replace(" ", "").replace("-", "").replace("&", "")
    return f"https://{cleaned}.com"


@app.get("/health", tags=["Health"])
async def health():
    return {
        "status":   "ONLINE",
        "service":  "AI Indian Startup Funding & Founder Intelligence API v4.0",
        "schedule": "Auto-pipeline: Daily at 1:00 AM IST",
        "docs":     "/docs",
        "hub":      "/hub",
    }


@app.get("/", tags=["Health"])
async def root():
    if INDEX_HTML.exists():
        return FileResponse(INDEX_HTML, media_type="text/html")
    return await health()


@app.get("/hub", tags=["UI"])
async def control_hub():
    if not INDEX_HTML.exists():
        raise HTTPException(status_code=404, detail="index.html not found")
    return FileResponse(INDEX_HTML, media_type="text/html")


@app.get("/dashboard", tags=["UI"])
async def dashboard_data(_: bool = Depends(_verify_api_key)):
    data = workflow.get_dashboard_data()
    summary = workflow.last_summary or {}
    return {
        "status": data.get("status", "EMPTY"),
        "sheets": data.get("sheets", {}),
        "summary": summary,
    }


@app.get("/startups", tags=["Startups"])
async def get_startups(
    limit: int = Query(50, ge=1, le=200),
    _: bool = Depends(_verify_api_key),
):
    def _run():
        data = workflow.get_dashboard_data()
        sheets = data.get("sheets", {})
        startups_sheet = sheets.get("Startups", {})
        columns = startups_sheet.get("columns", [])
        rows = startups_sheet.get("rows", [])
        result = []
        for row in rows[:limit]:
            item = {}
            for idx, col in enumerate(columns):
                item[col] = row[idx] if idx < len(row) else None
            result.append(item)
        return {"status": "SUCCESS", "count": len(result), "data": result}

    loop = asyncio.get_running_loop()
    result = await loop.run_in_executor(_executor, _run)
    return result


@app.get("/founders", tags=["Founders & Leadership"])
async def get_founders(
    company: str = Query(..., description="Startup company name"),
    _: bool = Depends(_verify_api_key),
):
    company = _validate_company_name(company)
    website = _guess_website(company)
    def _run():
        founders = workflow.contact_scraper.extract_leadership_data(company, website)
        return {"status": "SUCCESS", "company": company, "founders": founders}
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(_executor, _run)


@app.get("/contacts", tags=["Key Contacts"])
async def get_contacts(
    company: str = Query(..., description="Startup company name"),
    _: bool = Depends(_verify_api_key),
):
    company = _validate_company_name(company)
    website = _guess_website(company)
    def _run():
        contacts = workflow.contact_scraper.extract_hiring_contacts(company, website)
        return {"status": "SUCCESS", "company": company, "contacts": contacts}
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(_executor, _run)


@app.get("/export", tags=["Excel Export"])
async def download_excel(_: bool = Depends(_verify_api_key)):
    path = Path(EXCEL_OUTPUT_PATH)
    if not path.exists():
        def _run():
            logger.info("Excel file not found — running pipeline to generate it...")
            return workflow.run_daily_workflow()
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(_executor, _run)

    if not path.exists():
        raise HTTPException(status_code=503, detail="Excel file could not be generated. Check logs.")

    return FileResponse(
        path       =EXCEL_OUTPUT_PATH,
        filename   ="Indian_Startup_Outreach.xlsx",
        media_type ="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


@app.post("/run", tags=["Pipeline"])
async def run_pipeline(
    limit: int = Query(50, ge=1, le=200),
    _: bool = Depends(_verify_api_key),
):
    logger.info(f"Manual pipeline trigger via POST /run (limit={limit})")
    def _run():
        return workflow.run_daily_workflow(limit=limit)
    loop = asyncio.get_running_loop()
    result = await loop.run_in_executor(_executor, _run)
    return JSONResponse(content=result)


@app.get("/schedule/status", tags=["Scheduler"])
async def scheduler_status(_: bool = Depends(_verify_api_key)):
    if not _scheduler or not _scheduler.running:
        return {"scheduler": "not running"}
    job = _scheduler.get_job("startup_pipeline")
    day_of_week = os.getenv("SCHEDULER_DAY_OF_WEEK", "*")
    schedule_label = "Daily" if day_of_week.strip() == "*" else f"Weekly ({day_of_week.upper()})"
    return {
        "scheduler": "running",
        "next_run":  str(job.next_run_time) if job else "unknown",
        "schedule":  schedule_label,
    }


@app.post("/outreach/initial", tags=["Outreach"])
async def run_outreach_initial(_: bool = Depends(_verify_api_key)):
    """Trigger Workflow 2 — Generate & send/draft initial outreach emails for new startups."""
    logger.info("n8n triggered: POST /outreach/initial")
    from src.pipeline.outreach_workflow import OutreachWorkflow
    def _run():
        wf = OutreachWorkflow()
        return wf.run_initial_outreach()
    loop = asyncio.get_running_loop()
    result = await loop.run_in_executor(_executor, _run)
    return JSONResponse(content=result)


@app.post("/outreach/followup", tags=["Outreach"])
async def run_outreach_followup(_: bool = Depends(_verify_api_key)):
    """Trigger Workflow 3 — Send follow-up emails to non-replying contacts."""
    logger.info("n8n triggered: POST /outreach/followup")
    from src.pipeline.outreach_workflow import OutreachWorkflow
    def _run():
        wf = OutreachWorkflow()
        return wf.run_followup_outreach()
    loop = asyncio.get_running_loop()
    result = await loop.run_in_executor(_executor, _run)
    return JSONResponse(content=result)


@app.get("/outreach/status", tags=["Outreach"])
async def outreach_status(_: bool = Depends(_verify_api_key)):
    """Return a summary of the current outreach campaign state."""
    from src.exporters.excel_exporter import ExcelOutreachExporter
    def _run():
        exporter = ExcelOutreachExporter()
        rows = exporter.read_outreach_state()
        total      = len(rows)
        sent       = sum(1 for r in rows if r.get("Initial Email Sent") == "Yes")
        drafts     = sum(1 for r in rows if r.get("Initial Email Sent") == "Draft")
        replied    = sum(1 for r in rows if str(r.get("Reply Status","")).lower() == "replied")
        no_reply   = sum(1 for r in rows if str(r.get("Reply Status","")).lower() == "no reply")
        followups  = sum(int(r.get("Follow-up Sent") or 0) for r in rows)
        return {
            "total_in_pipeline": total,
            "initial_sent":      sent,
            "drafts_ready":      drafts,
            "replied":           replied,
            "no_reply":          no_reply,
            "followups_sent":    followups,
        }
    loop = asyncio.get_running_loop()
    result = await loop.run_in_executor(_executor, _run)
    return JSONResponse(content=result)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=False)
