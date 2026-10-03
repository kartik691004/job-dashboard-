"""
test_phase22_production_safety.py — Phase 22 production-write safety.

All external surfaces mocked/stubbed: no Apify, no Groq, no Google Sheets,
no network. DRY_RUN stays true; no real writes anywhere.
"""
import csv
import os
import sys
import threading
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.llm.base import LLMError
from app.llm.schemas import GateResult, LeadVerdict
from app.models import ClassifiedPost, RawPost
from app.orchestrator import Orchestrator, _PRODUCTION_WRITE_LOCK
from app.sheets_writer import (
    SheetReadError,
    SheetReadResult,
    SheetsWriter,
    WriteResult,
)

ARTIFACT_DIR = Path(__file__).resolve().parent.parent / "data" / "validation" / "phase22_production_safety"


@pytest.fixture(autouse=True)
def fast_retries(monkeypatch):
    monkeypatch.setattr("app.sheets_writer.time.sleep", lambda seconds: None)
    # Phase 25.1: this harness deliberately exercises the legacy gate-off
    # write path with fake live writers. Opt in explicitly so the
    # production-invocation guard (which refuses accidental live+ungated
    # runs) does not trip here. No test assertion is changed by this.
    monkeypatch.setattr("app.config.ALLOW_UNGATED_PRODUCTION_WRITE",
                        True, raising=False)
    # Also patch orchestrator's imported reference just in case
    monkeypatch.setattr("app.orchestrator.ALLOW_UNGATED_PRODUCTION_WRITE",
                        True, raising=False)


# ── Helpers ──────────────────────────────────────────────────────────────────

class FakeWS:
    """In-memory worksheet stand-in. Records appends; never touches network."""

    def __init__(self, urls=None, fail_with=None):
        self._urls = list(urls or [])
        self.appended = []
        self.fail_with = fail_with
        self.append_calls = 0

    def col_values(self, n):
        return ["Source Link"] + list(self._urls)

    def append_rows(self, rows, value_input_option=None):
        self.append_calls += 1
        if self.fail_with is not None:
            raise self.fail_with
        self.appended.extend(rows)


def live_writer(urls=None, fail_with=None):
    w = SheetsWriter.__new__(SheetsWriter)
    w.dry_run = False
    w.worksheet = FakeWS(urls=urls, fail_with=fail_with)
    w.credentials_path = "mock"
    w.sheet_id = "mock"
    w.worksheet_name = "mock"
    return w


def dry_writer():
    w = SheetsWriter.__new__(SheetsWriter)
    w.dry_run = True
    w.worksheet = FakeWS()
    w.credentials_path = "mock"
    w.sheet_id = "mock"
    w.worksheet_name = "mock"
    return w


def make_raw(url):
    return RawPost(post_url=url, post_date="", author_name="",
                   author_profile_url="",
                   text="We are hiring a Data Analyst in Bengaluru. Apply now.")


def make_classified(url, status="New"):
    return ClassifiedPost(
        post_url=url, post_date="", text="t", author_name="", author_profile_url="",
        company_name="Acme", major_category="Data Analyst", exact_role="Data Analyst",
        ctc="Not Disclosed", cold_email="Not Available",
        hiring_manager_name="Unclear", hiring_manager_linkedin="Unclear",
        source_link=url, description="We are hiring a Data Analyst in Bengaluru.",
        confidence=0.95, location="Bengaluru", employment_type="Full-time",
        experience_requirement="Not Specified", market="India", india_relevance="India",
        scraped_at="now", classification_reason="deterministic pass", status=status,
        is_valid=True,
    )


def make_gate(decision, confidence=None):
    conf = confidence if confidence is not None else (0.95 if decision == "ACCEPT" else 0.80)
    verdict = LeadVerdict(
        decision=decision, confidence=conf, is_current_job=True,
        is_genuine_hiring=True, is_target_role=True,
        major_category="Data Analyst", india_relevance="India",
        employment_type="Full-time", company="Acme", exact_role="Data Analyst",
        reason=f"gate says {decision}",
    )
    status = {"ACCEPT": "New", "REVIEW": "Review", "REJECT": ""}[decision]
    return GateResult(verdict=verdict, decision=decision, status=status)


class StubVerifier:
    def __init__(self, gates):
        self.gates = list(gates)
        self.llm_calls = 0

    def verify(self, **kwargs):
        self.llm_calls += 1
        return self.gates.pop(0)


@pytest.fixture
def orch():
    with patch("app.orchestrator.DataDopingSource") as mock_src, \
         patch("app.orchestrator.DeterministicClassifier"), \
         patch("app.orchestrator.SheetsWriter"):
        o = Orchestrator()
        yield o, mock_src


def drive(o, urls, gates, classify=None):
    """Drive one pipeline run with canned inputs; returns summary."""
    o.source.search_all.return_value = [make_raw(u) for u in urls]
    if classify is None:
        o.classifier.classify.side_effect = [make_classified(u) for u in urls]
    else:
        o.classifier.classify.side_effect = classify
    o.verifier = StubVerifier(gates)
    return o.run_pipeline(limit=10)


# ── 22A: fail-closed sheet read ──────────────────────────────────────────────

def test_read_success_with_existing_urls():
    w = live_writer(urls=["https://x/1", "https://x/2"])
    assert w.get_existing_urls() == {"https://x/1", "https://x/2"}
    r = w.try_read_existing_urls()
    assert r.ok is True and r.urls == {"https://x/1", "https://x/2"}


def test_read_success_zero_urls_empty_sheet_valid():
    w = live_writer(urls=[])
    assert w.get_existing_urls() == set()  # SUCCESSFUL_EMPTY_READ, no raise
    assert w.try_read_existing_urls().ok is True


def test_read_exception_raises_fail_closed():
    w = live_writer()
    w.worksheet = MagicMock()
    w.worksheet.col_values.side_effect = RuntimeError("sheets down")
    with pytest.raises(SheetReadError):
        w.get_existing_urls()
    r = w.try_read_existing_urls()
    assert r.ok is False and r.urls == set() and r.error != ""


def test_read_transient_then_success():
    ws = FakeWS(urls=["https://x/9"])
    calls = {"n": 0}
    real = ws.col_values

    def flaky(n):
        calls["n"] += 1
        if calls["n"] == 1:
            raise TimeoutError("read timed out")
        return real(n)

    ws.col_values = flaky
    w = live_writer()
    w.worksheet = ws
    with pytest.raises(SheetReadError):
        w.get_existing_urls()  # first attempt fails closed, no retry-to-empty
    assert w.get_existing_urls() == {"https://x/9"}  # explicit retry succeeds


def test_read_malformed_response_raises():
    w = live_writer()
    w.worksheet = MagicMock()
    w.worksheet.col_values.return_value = {"not": "a list"}
    with pytest.raises(SheetReadError):
        w.get_existing_urls()
    assert w.try_read_existing_urls().ok is False


def test_orchestrator_read_failure_aborts_write(orch):
    o, _ = orch
    o.sheets_writer = live_writer()
    o.sheets_writer.worksheet = MagicMock()
    o.sheets_writer.worksheet.col_values.side_effect = RuntimeError("sheets down")
    summary = drive(o, ["https://x/1"], [make_gate("ACCEPT")])
    assert summary["sheet_read_ok"] is False
    assert summary["write_outcome"] == "READ_FAILED"
    assert summary["new_rows"] == 0 and summary["written_rows"] == 0
    assert summary["errors"] >= 1
    o.sheets_writer.worksheet.append_rows.assert_not_called()


# ── 22B: truthful WriteResult ────────────────────────────────────────────────

def test_write_success_reports_confirmed_rows():
    w = live_writer()
    r = w.write_posts([make_classified("https://x/1"), make_classified("https://x/2")])
    assert isinstance(r, WriteResult)
    assert (r.attempted, r.written, r.failed, r.success, r.outcome) == (2, 2, 0, True, "SUCCESS")
    assert len(w.worksheet.appended) == 2


def test_write_complete_failure_reports_zero_written():
    w = live_writer(fail_with=RuntimeError("quota exceeded"))
    r = w.write_posts([make_classified("https://x/1")])
    assert r.success is False and r.written == 0 and r.failed == 1
    assert r.outcome == "FAILED" and r.error != ""


def test_write_timeout_is_unknown_never_success():
    w = live_writer(fail_with=TimeoutError("request timed out after send"))
    r = w.write_posts([make_classified("https://x/1")])
    assert r.outcome == "UNKNOWN" and r.success is False and r.written == 0


def test_write_ambiguous_network_failure_is_unknown():
    w = live_writer(fail_with=ConnectionError("connection reset by peer"))
    r = w.write_posts([make_classified("https://x/1")])
    assert r.outcome == "UNKNOWN" and r.written == 0


def test_write_definite_api_failure_is_failed():
    class FakeAPIError(Exception):
        pass
    w = live_writer(fail_with=FakeAPIError("APIError 400 invalid values"))
    r = w.write_posts([make_classified("https://x/1")])
    assert r.outcome == "FAILED" and r.written == 0


def test_write_exception_before_request_no_append():
    w = live_writer()
    w.worksheet = None  # definite failure before any request
    r = w.write_posts([make_classified("https://x/1")])
    assert r.outcome == "FAILED" and r.written == 0 and r.success is False


def test_write_dry_run_zero_appends_truthful_result():
    w = dry_writer()
    r = w.write_posts([make_classified("https://x/1")])
    assert r.outcome == "DRY_RUN" and r.written == 0 and r.success is True
    assert w.worksheet.appended == []


def test_write_zero_rows_empty_result_no_api_touch():
    w = live_writer()
    r = w.write_posts([])
    assert (r.attempted, r.written, r.success, r.outcome) == (0, 0, True, "EMPTY")
    assert w.worksheet.append_calls == 0


def test_write_multiple_rows_counts_all():
    w = live_writer()
    urls = [f"https://x/{i}" for i in range(5)]
    r = w.write_posts([make_classified(u) for u in urls])
    assert r.written == 5 and r.attempted == 5 and r.outcome == "SUCCESS"


def test_orchestrator_write_failure_truthful_summary(orch):
    o, _ = orch
    fake = live_writer(fail_with=RuntimeError("quota exceeded"))
    o.sheets_writer = fake
    summary = drive(o, ["https://x/1"], [make_gate("ACCEPT")])
    assert summary["write_outcome"] == "FAILED"
    assert summary["new_rows"] == 0 and summary["written_rows"] == 0
    assert summary["errors"] >= 1
    assert fake.worksheet.append_calls == 1  # single attempt, no blind retry


def test_orchestrator_writer_raises_before_result(orch):
    o, _ = orch
    o.sheets_writer = MagicMock()
    o.sheets_writer.get_existing_urls.return_value = set()
    o.sheets_writer.write_posts.side_effect = RuntimeError("boom pre-result")
    summary = drive(o, ["https://x/1"], [make_gate("ACCEPT")])
    assert summary["write_outcome"] == "FAILED"
    assert summary["new_rows"] == 0 and summary["written_rows"] == 0


def test_orchestrator_ambiguous_write_no_blind_retry(orch):
    o, _ = orch
    fake = live_writer(fail_with=TimeoutError("request timed out"))
    o.sheets_writer = fake
    summary = drive(o, ["https://x/1"], [make_gate("ACCEPT")])
    assert summary["write_outcome"] == "UNKNOWN"
    assert summary["new_rows"] == 0 and summary["written_rows"] == 0
    assert fake.worksheet.append_calls == 1  # exactly one attempt


# ── 22C: single-flight ───────────────────────────────────────────────────────

def test_sequential_runs_both_proceed(orch):
    o, _ = orch
    fake = live_writer()
    o.sheets_writer = fake
    s1 = drive(o, ["https://x/1"], [make_gate("ACCEPT")])
    s2 = drive(o, ["https://x/2"], [make_gate("ACCEPT")])
    assert s1["write_outcome"] == "SUCCESS" and s2["write_outcome"] == "SUCCESS"
    assert s1["run_already_active"] is False and s2["run_already_active"] is False
    assert not _PRODUCTION_WRITE_LOCK.locked()


def test_second_run_while_lock_held_is_rejected(orch):
    o, _ = orch
    o.sheets_writer = live_writer()
    assert _PRODUCTION_WRITE_LOCK.acquire(blocking=False)
    try:
        summary = drive(o, ["https://x/1"], [make_gate("ACCEPT")])
    finally:
        _PRODUCTION_WRITE_LOCK.release()
    assert summary["run_already_active"] is True
    assert summary["write_outcome"] == "RUN_ALREADY_ACTIVE"
    assert summary["new_rows"] == 0 and summary["written_rows"] == 0
    assert o.sheets_writer.worksheet.appended == []
    assert not _PRODUCTION_WRITE_LOCK.locked()


def test_lock_released_after_success(orch):
    o, _ = orch
    o.sheets_writer = live_writer()
    drive(o, ["https://x/1"], [make_gate("ACCEPT")])
    assert not _PRODUCTION_WRITE_LOCK.locked()


def test_lock_released_after_exception(orch):
    o, _ = orch
    o.sheets_writer = MagicMock()
    o.sheets_writer.get_existing_urls.side_effect = RuntimeError("read blew up")
    summary = drive(o, ["https://x/1"], [make_gate("ACCEPT")])
    assert summary["write_outcome"] == "READ_FAILED"
    assert not _PRODUCTION_WRITE_LOCK.locked()
    # Next run proceeds normally once the failure clears.
    o.sheets_writer.get_existing_urls.side_effect = None
    o.sheets_writer.get_existing_urls.return_value = set()
    s2 = drive(o, ["https://x/2"], [make_gate("ACCEPT")])
    assert s2["run_already_active"] is False


def test_overlapping_runs_serialized_no_duplicate_appends(orch):
    o, _ = orch
    entered = threading.Event()
    release = threading.Event()

    class BlockingWS(FakeWS):
        def append_rows(self, rows, value_input_option=None):
            entered.set()
            assert release.wait(timeout=30)
            super().append_rows(rows, value_input_option=value_input_option)

    blocking = live_writer()
    blocking.worksheet = BlockingWS()
    o.sheets_writer = blocking
    o.source.search_all.return_value = [make_raw("https://x/1")]
    o.classifier.classify.side_effect = [make_classified("https://x/1")]
    o.verifier = StubVerifier([make_gate("ACCEPT")])
    t = threading.Thread(target=o.run_pipeline, kwargs={"limit": 10})
    t.start()
    assert entered.wait(timeout=30)

    o2results = {}
    with patch("app.orchestrator.DataDopingSource"), \
         patch("app.orchestrator.DeterministicClassifier"), \
         patch("app.orchestrator.SheetsWriter"):
        o2 = Orchestrator()
    o2.sheets_writer = live_writer()  # separate writer, same process lock
    o2.source.search_all = MagicMock(return_value=[make_raw("https://x/1")])
    o2.classifier.classify = MagicMock(side_effect=[make_classified("https://x/1")])
    o2.verifier = StubVerifier([make_gate("ACCEPT")])
    o2results["summary"] = o2.run_pipeline(limit=10)
    release.set()
    t.join(timeout=30)

    s2 = o2results["summary"]
    assert s2["write_outcome"] == "RUN_ALREADY_ACTIVE"
    assert s2["new_rows"] == 0
    assert len(blocking.worksheet.appended) == 1  # exactly one append total
    assert not _PRODUCTION_WRITE_LOCK.locked()


# ── 22E: ACCEPT boundary preserved ───────────────────────────────────────────

def test_only_accept_and_review_reach_writer(orch):
    o, _ = orch
    fake = live_writer()
    o.sheets_writer = fake
    urls = ["https://x/1", "https://x/2", "https://x/3"]
    gates = [make_gate("ACCEPT"), make_gate("REVIEW"),
             GateResult(verdict=LeadVerdict(decision="REJECT"), decision="REJECT", status="")]
    summary = drive(o, urls, gates)
    assert summary["accepted"] == 1 and summary["review"] == 1 and summary["llm_rejected"] == 1
    assert [r[7] for r in fake.worksheet.appended] == ["https://x/1", "https://x/2"]
    statuses = [r[18] for r in fake.worksheet.appended]
    assert statuses == ["New", "Review"] and "New" in statuses


def test_llm_unavailable_never_promoted_to_new(orch):
    o, _ = orch
    fake = live_writer()
    o.sheets_writer = fake

    class Boom:
        def complete_json(self, system, user):
            raise LLMError("Groq HTTP 429 rate limit exceeded after retries")

    from app.llm.verifier import Verifier
    o.source.search_all.return_value = [make_raw("https://x/1")]
    o.classifier.classify.side_effect = [make_classified("https://x/1")]
    o.verifier = Verifier(provider=Boom())
    summary = o.run_pipeline(limit=10)
    assert summary["accepted"] == 0 and summary["review"] == 1
    assert all(r[18] != "New" for r in fake.worksheet.appended)


def test_null_confidence_never_promoted(orch):
    o, _ = orch
    fake = live_writer()
    o.sheets_writer = fake
    from app.llm.verifier import Verifier
    # Null confidence coerces to 0.0 -> hard REJECT (<0.75) via the real gate.
    gate = Verifier(provider=None)._gate(LeadVerdict(
        decision="ACCEPT", confidence=None, is_current_job=True,
        is_genuine_hiring=True, is_target_role=True,
        major_category="Data Analyst", india_relevance="India",
        employment_type="Full-time"))
    assert gate.decision == "REJECT"
    summary = drive(o, ["https://x/1"], [gate])
    assert summary["accepted"] == 0 and summary["llm_rejected"] == 1
    assert fake.worksheet.appended == []


def test_enrichment_review_does_not_veto_or_promote(orch):
    o, _ = orch
    fake = live_writer()
    o.sheets_writer = fake
    summary = drive(o, ["https://x/1"], [make_gate("ACCEPT")])
    assert summary["write_outcome"] == "SUCCESS"
    assert [r[18] for r in fake.worksheet.appended] == ["New"]


# ── 22D: no blind retry at orchestrator level ────────────────────────────────

def test_failed_write_is_not_retried_by_orchestrator(orch):
    o, _ = orch
    fake = live_writer(fail_with=RuntimeError("quota exceeded"))
    o.sheets_writer = fake
    drive(o, ["https://x/1"], [make_gate("ACCEPT")])
    assert fake.worksheet.append_calls == 1


# ── 22G: no secret output ────────────────────────────────────────────────────

def test_scrubbed_files_contain_no_credential_patterns():
    import re
    pats = [re.compile(r"gsk_\S+"), re.compile(r"AIza\S+"),
            re.compile(r"AQ\.\S+"), re.compile(r"\bsk-\S+")]
    files = [
        "data/validation/phase13b_extraction_rootcause/phase14a_provider_fix_validation.md",
        "data/validation/phase15_production_hardening/phase15e_implementation_validation.md",
        "data/validation/phase15_production_hardening/phase15e_preflight_audit.md",
        "data/validation/phase15_fresh_run/phase15b_groq_new_key_smoke_test.md",
        "AI_CONTEXT.md",
    ]
    repo = Path(__file__).resolve().parent.parent
    for rel in files:
        text = (repo / rel).read_text(encoding="utf-8", errors="replace")
        for p in pats:
            assert not p.search(text), f"credential-like pattern remains in {rel}"


def test_env_and_credentials_still_ignored():
    import subprocess
    out = subprocess.run(["git", "check-ignore", ".env", "credentials.json"],
                         capture_output=True, text=True,
                         cwd=str(Path(__file__).resolve().parent.parent)).stdout
    assert ".env" in out and "credentials.json" in out


# ── 22K: 10-case mocked production simulation → CSV ──────────────────────────

def _sim_run(name, setup):
    with patch("app.orchestrator.DataDopingSource"), \
         patch("app.orchestrator.DeterministicClassifier"), \
         patch("app.orchestrator.SheetsWriter"):
        o = Orchestrator()
    return setup(o)


def _accept_setup(urls, writer, gates):
    def setup(o):
        o.sheets_writer = writer
        o.source.search_all = MagicMock(return_value=[make_raw(u) for u in urls])
        o.classifier.classify = MagicMock(
            side_effect=[make_classified(u) for u in urls])
        o.verifier = StubVerifier(gates)
        s = o.run_pipeline(limit=10)
        return s
    return setup


def test_production_simulation_10_cases():
    rows = [["case", "setup", "expected", "actual", "writes", "result"]]

    def rec(case, setup_desc, expected, summary, writes, ok):
        rows.append([case, setup_desc, expected,
                     f"outcome={summary.get('write_outcome')} new={summary.get('new_rows')} "
                     f"written={summary.get('written_rows')} err={summary.get('errors')}",
                     writes, "PASS" if ok else "FAIL"])
        assert ok, case

    # CASE 1: valid ACCEPT batch -> mocked success.
    w1 = live_writer()
    s1 = _sim_run("c1", _accept_setup(["https://s/1"], w1, [make_gate("ACCEPT")]))
    rec("CASE 1", "ACCEPT batch, healthy sheet", "SUCCESS 1 written",
        s1, len(w1.worksheet.appended),
        s1["write_outcome"] == "SUCCESS" and len(w1.worksheet.appended) == 1)

    # CASE 2: duplicate batch -> zero new rows.
    w2 = live_writer(urls=["https://s/1"])
    s2 = _sim_run("c2", _accept_setup(["https://s/1"], w2, [make_gate("ACCEPT")]))
    rec("CASE 2", "duplicate URL already in sheet", "0 new, EMPTY",
        s2, len(w2.worksheet.appended),
        s2["new_rows"] == 0 and s2["write_outcome"] == "EMPTY"
        and len(w2.worksheet.appended) == 0)

    # CASE 3: sheet read failure -> zero writes.
    w3 = live_writer()
    w3.worksheet = MagicMock()
    w3.worksheet.col_values.side_effect = RuntimeError("down")
    s3 = _sim_run("c3", _accept_setup(["https://s/1"], w3, [make_gate("ACCEPT")]))
    rec("CASE 3", "col_values raises", "READ_FAILED, 0 writes",
        s3, 0,
        s3["write_outcome"] == "READ_FAILED" and s3["new_rows"] == 0)
    w3.worksheet.append_rows.assert_not_called()

    # CASE 4: writer failure -> truthful failed run.
    w4 = live_writer(fail_with=RuntimeError("quota exceeded"))
    s4 = _sim_run("c4", _accept_setup(["https://s/1"], w4, [make_gate("ACCEPT")]))
    rec("CASE 4", "append_rows raises definite error", "FAILED, new=0 written=0",
        s4, 0,
        s4["write_outcome"] == "FAILED" and s4["new_rows"] == 0
        and s4["written_rows"] == 0 and w4.worksheet.append_calls == 1)

    # CASE 5: ambiguous outcome -> UNKNOWN, exactly one attempt.
    w5 = live_writer(fail_with=TimeoutError("request timed out"))
    s5 = _sim_run("c5", _accept_setup(["https://s/1"], w5, [make_gate("ACCEPT")]))
    rec("CASE 5", "append timeout", "UNKNOWN, 1 attempt, no retry",
        s5, 0,
        s5["write_outcome"] == "UNKNOWN" and w5.worksheet.append_calls == 1
        and s5["new_rows"] == 0)

    # CASE 6: overlapping runs -> one rejected explicitly.
    w6 = live_writer()
    assert _PRODUCTION_WRITE_LOCK.acquire(blocking=False)
    try:
        s6 = _sim_run("c6", _accept_setup(["https://s/1"], w6, [make_gate("ACCEPT")]))
    finally:
        _PRODUCTION_WRITE_LOCK.release()
    rec("CASE 6", "lock held by another run", "RUN_ALREADY_ACTIVE, 0 writes",
        s6, len(w6.worksheet.appended),
        s6["write_outcome"] == "RUN_ALREADY_ACTIVE" and len(w6.worksheet.appended) == 0)

    # CASE 7: REJECT-only batch -> zero writes; REVIEW-only keeps Review policy.
    w7 = live_writer()
    s7 = _sim_run("c7", _accept_setup(
        ["https://s/1"],
        w7, [GateResult(verdict=LeadVerdict(decision="REJECT"),
                        decision="REJECT", status="")]))
    rec("CASE 7a", "REJECT-only batch", "EMPTY, 0 writes",
        s7, len(w7.worksheet.appended),
        s7["write_outcome"] == "EMPTY" and len(w7.worksheet.appended) == 0)
    w7b = live_writer()
    s7b = _sim_run("c7b", _accept_setup(["https://s/2"], w7b, [make_gate("REVIEW")]))
    statuses = [r[18] for r in w7b.worksheet.appended]
    rec("CASE 7b", "REVIEW-only batch", "written as Review, never New",
        s7b, len(w7b.worksheet.appended),
        statuses == ["Review"] and s7b["write_outcome"] == "SUCCESS")

    # CASE 8: DRY_RUN=true -> zero writes.
    w8 = dry_writer()
    s8 = _sim_run("c8", _accept_setup(["https://s/1"], w8, [make_gate("ACCEPT")]))
    rec("CASE 8", "dry_run writer", "DRY_RUN, 0 appends",
        s8, len(w8.worksheet.appended),
        s8["write_outcome"] == "DRY_RUN" and len(w8.worksheet.appended) == 0)

    # CASE 9: LLM unavailable -> held as Review, never New.
    w9 = live_writer()

    class Boom:
        def complete_json(self, system, user):
            raise LLMError("Groq HTTP 503 overloaded")

    def setup9(o):
        from app.llm.verifier import Verifier
        o.sheets_writer = w9
        o.source.search_all = MagicMock(return_value=[make_raw("https://s/1")])
        o.classifier.classify = MagicMock(side_effect=[make_classified("https://s/1")])
        o.verifier = Verifier(provider=Boom())
        return o.run_pipeline(limit=10)

    s9 = _sim_run("c9", setup9)
    rec("CASE 9", "LLM transport failure", "0 New rows; held Review",
        s9, len(w9.worksheet.appended),
        all(r[18] != "New" for r in w9.worksheet.appended) and s9["accepted"] == 0)

    # CASE 10: enrichment REVIEW on ACCEPT -> New preserved (policy unchanged).
    w10 = live_writer()
    s10 = _sim_run("c10", _accept_setup(["https://s/1"], w10, [make_gate("ACCEPT")]))
    rec("CASE 10", "ACCEPT + enrichment REVIEW", "New written (no veto/promotion)",
        s10, len(w10.worksheet.appended),
        [r[18] for r in w10.worksheet.appended] == ["New"])

    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    with open(ARTIFACT_DIR / "production_simulation.csv", "w", newline="",
              encoding="utf-8") as f:
        csv.writer(f).writerows(rows)
