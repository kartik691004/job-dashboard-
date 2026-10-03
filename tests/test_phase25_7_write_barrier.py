"""
test_phase25_7_write_barrier.py — Phase 25.7 final write barrier regression tests.

Proves that the Phase 25.7 write barrier (_production_write_barrier + write_production
loop) independently blocks every category of invalid record BEFORE it can reach any
production tab — regardless of what the upstream orchestrator or route result says.

All tests are offline (no Apify, no Groq, no Google Sheets, no network).
DRY_RUN stays True; no real writes anywhere.

Tests:
  1.  Review cannot reach production writer
  2.  HOLD cannot reach production writer
  3.  REJECT cannot reach production writer
  4.  UNAVAILABLE cannot reach production writer
  5.  confidence=0 cannot reach production writer
  6.  confidence=None cannot reach production writer
  7.  Company=Unclear cannot reach production writer
  8.  HM=Unclear cannot reach production writer
  9.  Gate OFF cannot create live production writes (orchestrator guard)
 10.  Incorrect category cannot route across tabs (DATA post blocked from MAIN)
 11.  write_posts() with live writer blocked without ALLOW_UNGATED_PRODUCTION_WRITE
 12.  Scheduler (run_daily.py) always enables gate — cannot bypass
 13.  Historical GIRI exception — snapshot comparison helper
 14.  Valid rows pass the barrier unchanged
 15.  Stable-ID cleanup: identity mismatch causes abort, not wrong-row deletion
"""
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.llm.schemas import GateResult, LeadVerdict, LLM_NON_EVALUATED
from app.models import ClassifiedPost
from app.production_gate import (
    DATA_PRODUCTION, MAIN_PRODUCTION, HOLD, REJECT, ELIGIBLE,
    DATA_CATEGORIES, MAIN_PRODUCTION_CATEGORIES,
)
from app.sheets_writer import (
    SheetsWriter, WriteResult,
    _production_write_barrier,
    _BARRIER_BLOCKED_STATUSES,
    _BARRIER_MIN_CONFIDENCE,
)


# ── Test fixtures / builders ──────────────────────────────────────────────────

def make_post(
    company="Acme Corp",
    hm="Rohan Mehta",
    role="Chief of Staff",
    category="Founder's Office",
    status="New",
    confidence=0.95,
    source_link="https://www.linkedin.com/posts/acme-activity-111",
    india="India",
    description="We are hiring a Chief of Staff in Bangalore. Full-time role.",
    exact_role=None,
) -> ClassifiedPost:
    return ClassifiedPost(
        post_url=source_link,
        post_date="2026-09-14",
        text="We are hiring a Chief of Staff in Bangalore.",
        author_name="Acme Corp",
        author_profile_url="https://www.linkedin.com/company/acme",
        company_name=company,
        major_category=category,
        exact_role=exact_role if exact_role is not None else role,
        hiring_manager_name=hm,
        hiring_manager_evidence="named in post",
        hiring_manager_linkedin="https://www.linkedin.com/in/rohan",
        location="Bangalore",
        employment_type="Full-time",
        experience_requirement="2-4 Years",
        india_relevance=india,
        market="India",
        source_link=source_link,
        description=description,
        key_points="",
        confidence=confidence,
        scraped_at="2026-09-14T00:00:00Z",
        classification_reason="ACCEPT: test",
        status=status,
        job_card_company="Acme Corp",
        is_valid=True,
    )


def make_gate(confidence=0.95, decision="ACCEPT", llm_status="SUCCESS") -> GateResult:
    verdict = LeadVerdict(
        decision=decision, confidence=confidence,
        is_current_job=True, is_genuine_hiring=True, is_target_role=True,
        major_category="Founder's Office", india_relevance="India",
        employment_type="Full-time", company="Acme Corp",
        exact_role="Chief of Staff", reason="test",
    )
    return GateResult(
        verdict=verdict, decision=decision,
        status={"ACCEPT": "New", "REVIEW": "Review"}.get(decision, ""),
        llm_status=llm_status,
    )


def make_live_writer_with_fake_ws():
    """Create a live SheetsWriter backed by in-memory FakeWS (no network)."""
    class FakeWS:
        def __init__(self):
            self._urls = []
            self.appended = []

        def col_values(self, n):
            return ["Source Link"] + self._urls

        def append_rows(self, rows, value_input_option=None):
            self.appended.extend(rows)

    w = SheetsWriter.__new__(SheetsWriter)
    w.dry_run = False
    ws = FakeWS()
    w.worksheet = ws
    w.data_worksheet = FakeWS()
    w.credentials_path = "mock"
    w.sheet_id = "mock"
    w.worksheet_name = "LinkedIn Hiring Leads"
    w.data_worksheet_name = "Data Analyst & Data Scientist"
    return w, ws


# ── 1. Review cannot reach production writer ──────────────────────────────────

def test_review_blocked_by_barrier():
    """Status=Review is blocked by the final write barrier."""
    post = make_post(status="Review")
    gate = make_gate()
    ok, reason = _production_write_barrier(post, gate, MAIN_PRODUCTION, "Founder's Office")
    assert not ok
    assert "review" in reason.lower()


def test_review_dropped_in_write_production():
    """A Review post passed to write_production() is dropped, never appended."""
    post = make_post(status="Review")
    gate = make_gate()
    w, ws = make_live_writer_with_fake_ws()
    with patch("app.sheets_writer._production_write_barrier", wraps=_production_write_barrier):
        result = w.write_production([(post, gate)])
    assert result.dropped >= 1
    assert ws.appended == []


# ── 2. HOLD cannot reach production writer ────────────────────────────────────

def test_hold_blocked_by_barrier():
    post = make_post(status="Hold")
    gate = make_gate()
    ok, reason = _production_write_barrier(post, gate, MAIN_PRODUCTION, "Founder's Office")
    assert not ok
    assert "hold" in reason.lower() or "blocked" in reason.lower()


# ── 3. REJECT cannot reach production writer ──────────────────────────────────

def test_reject_blocked_by_barrier():
    """Status=Reject is blocked by the final write barrier."""
    post = make_post(status="Reject")
    gate = make_gate()
    ok, reason = _production_write_barrier(post, gate, MAIN_PRODUCTION, "Founder's Office")
    assert not ok
    assert "reject" in reason.lower() or "blocked" in reason.lower()


def test_reject_gate_decision_blocked_by_route():
    """gate.decision=REJECT never passes route_production_post."""
    from app.production_gate import route_production_post
    post = make_post()
    gate = make_gate(decision="REJECT")
    route = route_production_post(post, gate)
    assert route.destination == REJECT


# ── 4. UNAVAILABLE cannot reach production writer ─────────────────────────────

def test_unavailable_blocked_by_barrier():
    post = make_post(status="Unavailable")
    gate = make_gate(llm_status="UNAVAILABLE")
    ok, reason = _production_write_barrier(post, gate, MAIN_PRODUCTION, "Founder's Office")
    assert not ok


def test_unavailable_llm_status_blocked():
    """LLM_NON_EVALUATED llm_status is blocked by the barrier."""
    for bad_status in ("UNAVAILABLE", "NOT_RUN", "ERROR"):
        post = make_post(status="New")
        gate = make_gate(llm_status=bad_status)
        ok, reason = _production_write_barrier(post, gate, MAIN_PRODUCTION, "Founder's Office")
        assert not ok, f"Expected barrier to block llm_status={bad_status!r}"
        assert "llm" in reason.lower() or "evaluated" in reason.lower(), reason


# ── 5 & 6. confidence=0 / None cannot reach production writer ─────────────────

def test_zero_confidence_blocked():
    post = make_post(confidence=0.0)
    gate = make_gate(confidence=0.0)
    ok, reason = _production_write_barrier(post, gate, MAIN_PRODUCTION, "Founder's Office")
    assert not ok
    assert "0.000" in reason or "confidence" in reason.lower()


def test_none_confidence_blocked():
    post = make_post()
    gate = MagicMock()
    gate.confidence = None
    gate.llm_status = "SUCCESS"
    ok, reason = _production_write_barrier(post, gate, MAIN_PRODUCTION, "Founder's Office")
    assert not ok


def test_below_threshold_confidence_blocked():
    """confidence=0.59 is below the 0.60 floor."""
    post = make_post(confidence=0.59)
    gate = make_gate(confidence=0.59)
    ok, reason = _production_write_barrier(post, gate, MAIN_PRODUCTION, "Founder's Office")
    assert not ok


# ── 7. Company=Unclear cannot reach production writer ─────────────────────────

def test_unclear_company_blocked():
    for bad_co in ("Unclear", "unclear", "N/A", "", "Unknown", "None"):
        post = make_post(company=bad_co)
        gate = make_gate()
        ok, reason = _production_write_barrier(post, gate, MAIN_PRODUCTION, "Founder's Office")
        assert not ok, f"Expected barrier to block company={bad_co!r}"
        assert "company" in reason.lower(), reason


def test_unclear_company_dropped_in_write_production():
    post = make_post(company="Unclear")
    gate = make_gate()
    w, ws = make_live_writer_with_fake_ws()
    result = w.write_production([(post, gate)])
    assert result.dropped >= 1
    assert ws.appended == []


# ── 8. HM=Unclear cannot reach production writer ──────────────────────────────

def test_unclear_hm_blocked():
    for bad_hm in ("Unclear", "unclear", "N/A", "", "Unknown"):
        post = make_post(hm=bad_hm)
        gate = make_gate()
        ok, reason = _production_write_barrier(post, gate, MAIN_PRODUCTION, "Founder's Office")
        assert not ok, f"Expected barrier to block HM={bad_hm!r}"
        assert "hiring_manager" in reason.lower() or "hm" in reason.lower(), reason


def test_unclear_hm_dropped_in_write_production():
    post = make_post(hm="Unclear")
    gate = make_gate()
    w, ws = make_live_writer_with_fake_ws()
    result = w.write_production([(post, gate)])
    assert result.dropped >= 1
    assert ws.appended == []


# ── 9. Gate OFF cannot create live production writes ──────────────────────────

def test_gate_off_live_write_refused_by_orchestrator():
    """
    Orchestrator with gate=False refuses a live non-dry-run write
    (Phase 25.1 invocation guard). ALLOW_UNGATED_PRODUCTION_WRITE must be
    False for this test.
    """
    from app.orchestrator import Orchestrator
    with patch("app.orchestrator.ALLOW_UNGATED_PRODUCTION_WRITE", False):
        orch = Orchestrator(production_gate=False)
        # Force the writer to look live
        orch.sheets_writer.dry_run = False
        result = orch.run_pipeline.__wrapped__ if hasattr(orch.run_pipeline, "__wrapped__") else None
        # Use _PRODUCTION_WRITE_LOCK bypass by mocking the scrape to return 0 posts
        with patch.object(orch.source, "search_all", return_value=[]):
            summary = orch.run_pipeline(limit=1)
        # The orchestrator should have refused due to GATE_REQUIRED
        assert summary.get("write_outcome") == "GATE_REQUIRED", (
            f"Expected GATE_REQUIRED, got {summary.get('write_outcome')!r}"
        )


# ── 10. Incorrect category cannot route across tabs ───────────────────────────

def test_data_post_blocked_from_main_tab():
    """A Data Analyst post is blocked from the MAIN tab by the barrier."""
    post = make_post(category="Data Analyst", role="Data Analyst")
    gate = make_gate()
    # Simulate the barrier being asked to route a DA post to MAIN
    ok, reason = _production_write_barrier(post, gate, MAIN_PRODUCTION, "Data Analyst")
    assert not ok
    assert "main_production" in reason.lower() or "category" in reason.lower()


def test_fo_post_blocked_from_data_tab():
    """A Founder's Office post is blocked from the DATA tab by the barrier."""
    post = make_post(category="Founder's Office", role="Chief of Staff")
    gate = make_gate()
    ok, reason = _production_write_barrier(post, gate, DATA_PRODUCTION, "Founder's Office")
    assert not ok
    assert "data_production" in reason.lower() or "category" in reason.lower()


def test_write_production_data_post_only_in_data_tab():
    """A DA post goes only to data tab in write_production, never main."""
    post = make_post(
        category="Data Analyst", role="Data Analyst",
        company="TestCo", hm="Jane Smith",
        source_link="https://www.linkedin.com/posts/da-activity-999",
    )
    gate = make_gate()
    gate.verdict.major_category = "Data Analyst"
    w, ws = make_live_writer_with_fake_ws()
    result = w.write_production([(post, gate)])
    # DA post should NOT land in main tab ws.appended
    assert ws.appended == [], "DA post must not reach MAIN tab"


# ── 11. write_posts() with live writer blocked without ungated override ────────

def test_write_posts_live_blocked_without_ungated_override():
    """
    write_posts() with a live writer and ALLOW_UNGATED_PRODUCTION_WRITE=False
    raises RuntimeError (Phase 25.7 secondary guard).
    """
    post = make_post()
    w, ws = make_live_writer_with_fake_ws()
    with patch("app.config.ALLOW_UNGATED_PRODUCTION_WRITE", False):
        with pytest.raises(RuntimeError, match="write_posts\\(\\) called with a live writer"):
            w.write_posts([post])


def test_write_posts_dry_run_always_safe():
    """write_posts() with dry_run=True never raises and never calls append."""
    post = make_post()
    w = SheetsWriter.__new__(SheetsWriter)
    w.dry_run = True
    w.worksheet = MagicMock()
    w.data_worksheet = None
    w.worksheet_name = "mock"
    w.data_worksheet_name = None
    result = w.write_posts([post])
    assert result.outcome == "DRY_RUN"
    w.worksheet.append_rows.assert_not_called()


# ── 12. Scheduler always enables gate — cannot bypass ────────────────────────

def test_run_daily_always_enables_production_gate():
    """
    run_daily.py calls Orchestrator(production_gate=True).
    Simulate parsing the source to confirm.
    """
    import ast
    run_daily = Path(__file__).resolve().parent.parent / "run_daily.py"
    source = run_daily.read_text(encoding="utf-8")
    # Must contain production_gate=True
    assert "production_gate=True" in source, (
        "run_daily.py must always pass production_gate=True to Orchestrator"
    )
    # Must NOT force DRY_RUN=true (it forces DRY_RUN=false)
    assert 'os.environ["DRY_RUN"] = "false"' in source, (
        "run_daily.py must set DRY_RUN=false for scheduled live writes"
    )


# ── 13. Historical GIRI exception snapshot comparison ─────────────────────────

def test_giri_exception_preserved_in_snapshot():
    """
    The pre-cleanup snapshot must contain the GIRI CONSULTANCY row.
    This proves the historical exception was captured before any cleanup.
    """
    snapshot_path = (
        Path(__file__).resolve().parent.parent
        / "data" / "validation" / "phase25_7_sheet_audit"
        / "pre_cleanup_snapshot" / "LinkedIn Hiring Leads.json"
    )
    if not snapshot_path.exists():
        pytest.skip("Pre-cleanup snapshot not yet taken; run --audit-only first")
    with open(snapshot_path, encoding="utf-8") as f:
        snap = json.load(f)
    rows = snap.get("values", [])
    # Look for GIRI CONSULTANCY in company column (col 0)
    companies = [r[0] if r else "" for r in rows[1:]]  # skip header
    assert any("GIRI" in c.upper() for c in companies), (
        "GIRI CONSULTANCY historical exception not found in pre-cleanup snapshot"
    )


# ── 14. Valid rows pass the barrier unchanged ─────────────────────────────────

def test_valid_fo_row_passes_barrier():
    """A fully valid Founder's Office ACCEPT row passes the barrier."""
    post = make_post(
        company="Acme Corp", hm="Rohan Mehta", role="Chief of Staff",
        category="Founder's Office", status="New", confidence=0.95,
    )
    gate = make_gate(confidence=0.95, decision="ACCEPT", llm_status="SUCCESS")
    ok, reason = _production_write_barrier(post, gate, MAIN_PRODUCTION, "Founder's Office")
    assert ok, f"Valid row should pass barrier: {reason}"
    assert reason == "barrier passed"


def test_valid_da_row_passes_barrier():
    """A fully valid Data Analyst ACCEPT row passes the barrier for DATA tab."""
    post = make_post(
        company="TestCo", hm="Jane Smith", role="Data Analyst",
        category="Data Analyst", status="New", confidence=0.92,
    )
    gate = make_gate(confidence=0.92, decision="ACCEPT", llm_status="SUCCESS")
    ok, reason = _production_write_barrier(post, gate, DATA_PRODUCTION, "Data Analyst")
    assert ok, f"Valid DA row should pass barrier: {reason}"


# ── 15. Stable-ID cleanup abort on identity mismatch ─────────────────────────

def test_cleanup_aborts_on_identity_mismatch():
    """
    The cleanup logic must abort and not delete a row if the company
    found at the expected activity ID does not match the expected company.
    This simulates the Sheet having been modified between audit and cleanup.
    """
    # Import the helper directly from the cleanup script
    import importlib.util
    script_path = (
        Path(__file__).resolve().parent.parent
        / "data" / "validation" / "phase25_7_sheet_audit"
        / "phase25_7_audit_and_cleanup.py"
    )
    spec = importlib.util.spec_from_file_location("cleanup_script", script_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    # Build a fake "current sheet" where the row at index 0 (sheet_row=2)
    # has a DIFFERENT company than expected
    real_aid = "7501963747826188288"  # Sagepilot AI in the audit
    wrong_row = ["WrongCompany"] + [""] * 22
    # The source link for real_aid
    real_source = f"https://www.linkedin.com/posts/someone-activity-{real_aid}"
    wrong_row[7] = real_source  # COL_SOURCE_LINK = 7

    fake_spreadsheet = MagicMock()
    # Tab read returns header + the wrong row
    fake_spreadsheet.worksheet.return_value.get_all_values.return_value = [
        mod.EXPECTED_HEADERS,
        wrong_row,
    ]

    rows_to_move = [{
        "activity_id": real_aid,
        "company": "Sagepilot AI",   # Expected company from audit
        "sheet_row": 2,
        "status": "Review",
        "reasons": ["Status=Review"],
        "exempt": False,
    }]

    # Dry-run off so it attempts to locate the row; but identity mismatch aborts
    # We patch read_tab to return the fake data
    with patch.object(mod, "read_tab", return_value=[mod.EXPECTED_HEADERS, wrong_row]):
        # Also patch the worksheet open
        fake_ws = MagicMock()
        fake_ws.get_all_values.return_value = [mod.EXPECTED_HEADERS, wrong_row]
        fake_spreadsheet.worksheet.return_value = fake_ws

        # The move_rows_from_tab inner function is not directly importable,
        # so we test via run_cleanup with dry_run=False but a fake spreadsheet.
        # We construct the audit_results that would trigger the move.
        audit_results = {
            "main_invalid": rows_to_move,
            "data_invalid": [],
            "main_valid": [],
            "data_valid": [],
        }

        # We expect it to fail with an error (identity mismatch)
        result = mod.run_cleanup(fake_spreadsheet, audit_results, dry_run=False)

    # The cleanup should have aborted — zero rows moved, errors reported
    assert result.get("moved_main", 0) == 0, (
        "Cleanup must not move any rows when identity mismatch detected"
    )
    assert result.get("aborted", False) or result.get("errors"), (
        "Cleanup must abort on identity mismatch"
    )
