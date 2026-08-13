"""
test_api.py — LinkedIn Hiring Intelligence Phase 1 API contract tests.

Covers only the two endpoints that exist:
  GET  /health
  POST /run?limit=N

Stale tests for /founders, /dashboard, /schedule/status, and
ContactScraper have been removed — those features are not in scope.
"""
import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch, MagicMock
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.main import app

client = TestClient(app)

# ── /health ────────────────────────────────────────────────────────────────────

def test_health_returns_online():
    resp = client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ONLINE"
    assert data["service"] == "LinkedIn Hiring Intelligence"


# ── /run ───────────────────────────────────────────────────────────────────────

MOCK_SUMMARY = {
    "scraped": 5,
    "unique": 5,
    "valid": 2,
    "excluded": 3,
    "current_run_duplicates": 0,
    "sheet_duplicates": 0,
    "new_rows": 2,
    "errors": 0,
}


def test_run_returns_success_summary():
    """POST /run with a mocked orchestrator must return a valid summary dict."""
    with patch("app.main.Orchestrator") as MockOrch:
        MockOrch.return_value.run_pipeline.return_value = MOCK_SUMMARY
        resp = client.post("/run")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "success"
    summary = data["summary"]
    for key in ("scraped", "unique", "valid", "excluded",
                 "current_run_duplicates", "sheet_duplicates", "new_rows", "errors"):
        assert key in summary, f"Missing summary key: {key}"


def test_run_accepts_limit_param():
    """POST /run?limit=5 must pass limit=5 to the orchestrator."""
    with patch("app.main.Orchestrator") as MockOrch:
        instance = MockOrch.return_value
        instance.run_pipeline.return_value = MOCK_SUMMARY
        resp = client.post("/run", params={"limit": 5})
    assert resp.status_code == 200
    instance.run_pipeline.assert_called_once_with(limit=5)


def test_run_rejects_zero_limit():
    """POST /run?limit=0 must return 422."""
    resp = client.post("/run", params={"limit": 0})
    assert resp.status_code == 422


def test_run_rejects_negative_limit():
    """POST /run?limit=-1 must return 422."""
    resp = client.post("/run", params={"limit": -1})
    assert resp.status_code == 422


def test_run_open_when_no_api_key_env(monkeypatch):
    """When API_KEY env var is not set, /run succeeds without any header."""
    monkeypatch.delenv("API_KEY", raising=False)
    with patch("app.main.Orchestrator") as MockOrch:
        MockOrch.return_value.run_pipeline.return_value = MOCK_SUMMARY
        resp = client.post("/run")
    assert resp.status_code == 200

