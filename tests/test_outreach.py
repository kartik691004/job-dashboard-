"""
test_outreach.py — cold-mail step (app/outreach.py).

Covers candidate filtering, dry-run isolation, Status-cell updates, per-run
caps, failure handling and the gid=0 guard. SMTP itself is never exercised;
a fake sender stands in for it.
"""
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.outreach import (
    COL_EMAIL,
    COL_STATUS,
    OutreachRunner,
    compose_email,
    is_real_email,
)
from app.sheets_writer import EXPECTED_HEADERS


# ── Fixtures ──────────────────────────────────────────────────────────────────

class FakeWorksheet:
    def __init__(self, rows, sheet_id=12345):
        self.rows = [list(r) for r in rows]
        self.id = sheet_id
        self.updates = []

    def get_all_values(self):
        return [list(r) for r in self.rows]

    def row_values(self, row):
        return list(self.rows[row - 1]) if 0 < row <= len(self.rows) else []

    def update_cell(self, row, col, value):
        self.updates.append((row, col, value))
        while len(self.rows) < row:
            self.rows.append([])
        target = self.rows[row - 1]
        while len(target) < col:
            target.append("")
        target[col - 1] = value


def make_sheet(rows_by_status_email):
    """Build a FakeWorksheet from (status, email) tuples; data starts at row 2."""
    rows = [list(EXPECTED_HEADERS)]
    for status, email in rows_by_status_email:
        row = [""] * len(EXPECTED_HEADERS)
        row[EXPECTED_HEADERS.index("Company Name")] = "Acme"
        row[EXPECTED_HEADERS.index("Exact Role")] = "Chief of Staff"
        row[EXPECTED_HEADERS.index("Hiring Manager Name")] = ""
        row[EXPECTED_HEADERS.index(COL_EMAIL)] = email
        row[EXPECTED_HEADERS.index(COL_STATUS)] = status
        rows.append(row)
    return FakeWorksheet(rows)


def make_runner(ws, dry_run=False):
    return OutreachRunner(
        credentials_path="unused.json",
        sheet_id="unused",
        worksheet_name="LinkedIn Hiring Leads",
        dry_run=dry_run,
        worksheet=ws,
    )


@pytest.fixture(autouse=True)
def no_retry_delays(monkeypatch):
    """Keep unit tests fast: _with_retry retries with real sleeps otherwise."""
    import app.outreach as outreach_mod
    monkeypatch.setattr(outreach_mod, "_with_retry", lambda fn, **kw: fn())


# ── is_real_email ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("value", [
    "founder@acme.in", "hr.team+jobs@company.co.in", "A.B@sub.domain.org",
])
def test_real_emails_accepted(value):
    assert is_real_email(value)

@pytest.mark.parametrize("value", [
    "", "Not Available", "not available", "N/A", "-", "TBD",
    "no-at-sign", "@missing-local.org", "spaces in@addr.com",
])
def test_placeholder_or_malformed_rejected(value):
    assert not is_real_email(value)


# ── Candidate selection ───────────────────────────────────────────────────────

def test_load_candidates_only_new_with_real_email():
    ws = make_sheet([
        ("New", "jobs@acme.in"),          # candidate
        ("New", "Not Available"),          # no address posted
        ("Contacted", "done@acme.in"),     # already worked
        ("new", "second@acme.in"),         # case-insensitive New -> candidate
        ("New", "bad-address"),            # malformed -> skipped
    ])
    leads = make_runner(ws).load_candidates()
    assert [l.email for l in leads] == ["jobs@acme.in", "second@acme.in"]
    assert leads[0].row_number == 2 and leads[1].row_number == 5
    assert leads[0].company_name == "Acme" and leads[0].exact_role == "Chief of Staff"


# ── Dry run ───────────────────────────────────────────────────────────────────

def test_dry_run_neither_sends_nor_writes():
    ws = make_sheet([("New", "jobs@acme.in")])

    def boom(*args):
        raise AssertionError("send_fn must not be called during dry run")

    summary = make_runner(ws, dry_run=True).run(max_per_run=10, send_fn=boom)
    assert summary["dry_run"] is True
    assert summary["candidates_found"] == 1 and summary["attempted"] == 1
    assert summary["sent"] == 0 and summary["failed"] == 0
    assert ws.updates == []


# ── Live run ──────────────────────────────────────────────────────────────────

def test_live_run_sends_and_marks_contacted():
    ws = make_sheet([
        ("New", "one@acme.in"),
        ("New", "two@acme.in"),
        ("Contacted", "old@acme.in"),
    ])
    sent = []
    runner = make_runner(ws, dry_run=False)
    summary = runner.run(max_per_run=10,
                         send_fn=lambda lead, s, b: sent.append(lead.email))

    assert sorted(sent) == ["one@acme.in", "two@acme.in"]
    assert summary["sent"] == 2 and summary["status_updated"] == 2 and summary["failed"] == 0
    status_col = EXPECTED_HEADERS.index(COL_STATUS) + 1
    assert ws.rows[1][status_col - 1] == "Contacted"
    assert ws.rows[2][status_col - 1] == "Contacted"
    assert ws.rows[3][status_col - 1] == "Contacted"  # untouched row stays as-is


def test_max_per_run_cap_leaves_rest_new():
    ws = make_sheet([
        ("New", "a@acme.in"),
        ("New", "b@acme.in"),
        ("New", "c@acme.in"),
    ])
    summary = make_runner(ws, dry_run=False).run(
        max_per_run=2, send_fn=lambda lead, s, b: None)

    assert summary["attempted"] == 2 and summary["sent"] == 2
    status_col = EXPECTED_HEADERS.index(COL_STATUS) + 1
    email_col = EXPECTED_HEADERS.index(COL_EMAIL) + 1
    assert set(ws.updates) == {(2, status_col, "Contacted"), (3, status_col, "Contacted")}
    assert ws.rows[3][email_col - 1] == "c@acme.in"   # third candidate still New


def test_send_failure_does_not_mark_contacted():
    ws = make_sheet([("New", "flaky@acme.in")])

    def failing_send(lead, subject, body):
        raise RuntimeError("SMTP down")

    summary = make_runner(ws, dry_run=False).run(max_per_run=5, send_fn=failing_send)
    assert summary["sent"] == 0 and summary["failed"] == 1
    assert summary["status_updated"] == 0
    assert ws.updates == []


def test_status_update_failure_counts_but_does_not_resend():
    class BrokenWriter(FakeWorksheet):
        def update_cell(self, row, col, value):
            raise RuntimeError("sheets quota")

    ws = BrokenWriter(make_sheet([("New", "x@acme.in")]).rows)
    sent = []
    summary = make_runner(ws, dry_run=False).run(
        max_per_run=5, send_fn=lambda lead, s, b: sent.append(lead.email))

    assert summary["sent"] == 1           # email went out...
    assert summary["failed"] == 1         # ...but the Status flip failed
    assert summary["status_updated"] == 0


# ── gid=0 guard ───────────────────────────────────────────────────────────────

def test_refuses_protected_gid_zero_worksheet():
    ws = make_sheet([("New", "jobs@acme.in")])
    ws.id = 0
    with pytest.raises(RuntimeError, match="gid=0"):
        make_runner(ws, dry_run=True).load_candidates()


def test_header_mismatch_aborts_before_any_write():
    ws = make_sheet([("New", "jobs@acme.in")])
    ws.rows[0][4] = "Wrong Header"        # Cold Email column position
    with pytest.raises(RuntimeError, match="EXPECTED_HEADERS"):
        make_runner(ws, dry_run=False)._ensure_worksheet()


# ── Email composition ─────────────────────────────────────────────────────────

def test_compose_email_personalizes_subject_and_greeting():
    from app.outreach import Lead
    lead = Lead(row_number=2, company_name="Acme", exact_role="Chief of Staff",
                hiring_manager_name="Aditi", email="aditi@acme.in",
                source_link="https://linkedin.com/p/1")
    subject, body = compose_email(lead, sender_name="K")
    assert "Chief of Staff" in subject and "Acme" in subject
    assert body.startswith("Hi Aditi,")
    assert "K" in body.splitlines()[-1]


def test_compose_email_falls_back_without_manager_name():
    from app.outreach import Lead
    lead = Lead(row_number=2, company_name="", exact_role="",
                hiring_manager_name="", email="x@y.in", source_link="")
    subject, body = compose_email(lead)
    assert "Founder's Office / Chief of Staff" in subject
    assert body.startswith("Hi,")


# ── API endpoint ──────────────────────────────────────────────────────────────

from app.main import app as fastapi_app

client = TestClient(fastapi_app)

MOCK_OUTREACH_SUMMARY = {
    "candidates_found": 3, "attempted": 2, "sent": 2,
    "failed": 0, "status_updated": 2, "dry_run": False,
}


def test_outreach_endpoint_returns_summary():
    with patch("app.main.OutreachRunner") as MockRunner:
        MockRunner.return_value.run.return_value = dict(MOCK_OUTREACH_SUMMARY)
        resp = client.post("/outreach", params={"max_per_run": 2})
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "success"
    MockRunner.return_value.run.assert_called_once_with(max_per_run=2)
    assert data["summary"]["sent"] == 2


def test_outreach_endpoint_rejects_bad_cap():
    resp = client.post("/outreach", params={"max_per_run": 0})
    assert resp.status_code == 422


def test_outreach_endpoint_wraps_errors():
    with patch("app.main.OutreachRunner") as MockRunner:
        MockRunner.return_value.run.side_effect = RuntimeError("sheets down")
        resp = client.post("/outreach")
    assert resp.status_code == 200
    assert resp.json()["status"] == "error"
