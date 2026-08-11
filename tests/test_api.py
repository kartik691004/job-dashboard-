import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch, MagicMock
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.main import app

client = TestClient(app)


def test_health_endpoint():
    resp = client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ONLINE"
    assert "Funding & Founder Intelligence" in data["service"]


def test_company_validation_rejects_bad_input():
    resp = client.get("/founders", params={"company": "'; DROP TABLE--"})
    assert resp.status_code == 400


def test_company_validation_accepts_good_input():
    with patch("app.main.workflow") as mock_workflow:
        mock_workflow.contact_scraper.extract_leadership_data.return_value = []
        resp = client.get("/founders", params={"company": "Krutrim AI"})
        assert resp.status_code == 200


def test_dashboard_endpoint():
    resp = client.get("/dashboard")
    assert resp.status_code == 200
    data = resp.json()
    assert "status" in data
    assert "sheets" in data


def test_schedule_status():
    resp = client.get("/schedule/status")
    assert resp.status_code == 200
    data = resp.json()
    assert "scheduler" in data


def test_run_endpoint_requires_auth_when_key_set(monkeypatch):
    monkeypatch.setenv("API_KEY", "secret123")
    with patch("app.main.workflow") as mock_workflow:
        mock_workflow.run_daily_workflow.return_value = {"status": "SUCCESS"}
        resp = client.post("/run", headers={"X-API-Key": "secret123"}, params={"limit": 1})
        assert resp.status_code == 200


def test_run_endpoint_rejects_invalid_auth(monkeypatch):
    monkeypatch.setenv("API_KEY", "secret123")
    resp = client.post("/run", headers={"X-API-Key": "wrong"}, params={"limit": 1})
    assert resp.status_code == 401
