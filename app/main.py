from fastapi import FastAPI, HTTPException
from app.orchestrator import Orchestrator
import traceback

app = FastAPI(title="LinkedIn Hiring Intelligence")


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
