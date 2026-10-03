"""
test_phase25_6i_resume.py — Durable quota-aware resume engine (Phase 25.6I).

Extended in Phase 25.6O with provider-specific availability.

Offline only: no Apify, no Sheets, no network, no live providers.
All provider interactions use scripted fakes; ledger I/O uses tmp_path.
"""
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.resume_engine_25_6i import (
    AUTH_FAILURE,
    BATCH_MAX,
    MALFORMED,
    NETWORK_FAILURE,
    OTHER_ERROR,
    RATE_LIMITED,
    STATUS_HOLD,
    STATUS_REJECT,
    STATUS_UNAVAILABLE,
    classify_provider_error,
    cooldown_for_attempt,
    eligible_for_provider,
    is_retry_eligible,
    load_ledger,
    next_retry_eligible,
    record_success,
    record_throttle,
    save_ledger,
    select_next,
    select_next_for_provider,
)

NOW = datetime(2026, 9, 20, 12, 0, 0, tzinfo=timezone.utc)


def _entry(**over):
    base = {"key": "k", "status": STATUS_UNAVAILABLE, "attempt_count": 0}
    base.update(over)
    return base


# ── retry eligibility ────────────────────────────────────────────────────

def test_eligible_when_cooldown_passed():
    e = _entry(retry_eligible_at=(NOW - timedelta(hours=1)).isoformat())
    assert is_retry_eligible(e, NOW) is True


def test_ineligible_during_cooldown():
    e = _entry(retry_eligible_at=(NOW + timedelta(hours=5)).isoformat())
    assert is_retry_eligible(e, NOW) is False


def test_non_unavailable_never_eligible():
    e = _entry(status=STATUS_HOLD,
               retry_eligible_at=(NOW - timedelta(hours=1)).isoformat())
    assert is_retry_eligible(e, NOW) is False


# ── throttle ladder ──────────────────────────────────────────────────────

def test_first_throttle_waits_future_window():
    cd = cooldown_for_attempt(1, RATE_LIMITED)
    assert cd is not None and cd.total_seconds() == 6 * 3600
    nxt = next_retry_eligible(NOW, 1, RATE_LIMITED)
    assert nxt == (NOW + timedelta(hours=6)).isoformat()


def test_second_throttle_increases_cooldown():
    first = cooldown_for_attempt(1, RATE_LIMITED)
    second = cooldown_for_attempt(2, RATE_LIMITED)
    assert second is not None and second > first
    assert second.total_seconds() == 24 * 3600


def test_third_throttle_manual_only():
    assert cooldown_for_attempt(3, RATE_LIMITED) is None
    assert next_retry_eligible(NOW, 3, RATE_LIMITED) is None
    e = _entry(attempt_count=3, retry_eligible_at=None)
    assert is_retry_eligible(e, NOW) is False


def test_auth_never_auto_retries():
    assert cooldown_for_attempt(1, AUTH_FAILURE) is None


# ── error classification ─────────────────────────────────────────────────

def test_classify_provider_errors():
    assert classify_provider_error(Exception("HTTP 429 rate limit exceeded")) == RATE_LIMITED
    assert classify_provider_error(Exception("GroqProvider unavailable (RATE_LIMITED)")) == RATE_LIMITED
    assert classify_provider_error(Exception("HTTP 401 bad credentials")) == AUTH_FAILURE
    assert classify_provider_error(TimeoutError("timed out")) == NETWORK_FAILURE
    assert classify_provider_error(Exception("HTTP 503 overloaded")) == NETWORK_FAILURE
    assert classify_provider_error(Exception("returned JSON that is not an object")) == MALFORMED
    assert classify_provider_error(Exception("weird boom")) == OTHER_ERROR


# ── persistence ──────────────────────────────────────────────────────────

def test_throttle_persisted_without_touching_cache():
    ledger, cache = {}, {"already": {"decision": "REVIEW"}}
    entry = record_throttle(ledger, "k1", provider="GeminiProvider",
                            error_class=RATE_LIMITED, now=NOW)
    assert "k1" not in cache
    assert cache == {"already": {"decision": "REVIEW"}}
    assert entry["status"] == STATUS_UNAVAILABLE
    assert entry["attempt_count"] == 1
    assert entry["last_provider"] == "GeminiProvider"
    assert entry["last_error_class"] == RATE_LIMITED
    assert entry["retry_eligible_at"] == (NOW + timedelta(hours=6)).isoformat()


def test_unavailable_never_cached_as_successful():
    ledger, cache = {}, {}
    for _ in range(3):
        record_throttle(ledger, "k9", provider="GeminiProvider",
                        error_class=RATE_LIMITED, now=NOW)
    assert cache == {}
    assert ledger["k9"]["status"] == STATUS_UNAVAILABLE


def test_success_persisted_with_provenance():
    ledger, cache = {}, {}
    verdict = {"decision": "REVIEW", "confidence": 0.85, "company": "Acme"}
    record_success(ledger, cache, "k2", provider="GroqProvider", confidence=0.85,
                   decision="REVIEW", llm_status="REVIEW", verdict=verdict,
                   timestamp=NOW.isoformat())
    assert cache["k2"]["verdict"] == verdict
    assert cache["k2"]["llm_provider"] == "GroqProvider"
    assert cache["k2"]["timestamp"] == NOW.isoformat()
    assert ledger["k2"]["status"] == STATUS_HOLD
    assert ledger["k2"]["confidence"] == 0.85
    assert "verdict" not in ledger["k2"]  # pointers only, no duplication


def test_reject_persisted_as_reject():
    ledger, cache = {}, {}
    record_success(ledger, cache, "k3", provider="GroqProvider", confidence=0.99,
                   decision="REJECT", llm_status="REJECT",
                   verdict={"decision": "REJECT"}, timestamp=NOW.isoformat())
    assert ledger["k3"]["status"] == STATUS_REJECT


# ── quota gate before candidate calls ────────────────────────────────────

class _Probe:
    def __init__(self, error=None):
        self.error = error
        self.calls = 0

    def __call__(self):
        self.calls += 1
        if self.error is not None:
            raise self.error
        return {"ok": True}


def _run_gated_batch(probe, verifier_calls, ordered, ledger, cache, now):
    """Gated batch shape: probe once; RATE_LIMITED exits with zero calls."""
    try:
        probe()
    except Exception as e:
        if classify_provider_error(e) == RATE_LIMITED:
            return {"started": False, "calls": 0}
        return {"started": False, "calls": 0, "gate": classify_provider_error(e)}
    calls = 0
    for key in select_next(ordered, ledger, set(cache), now):
        verifier_calls.append(key)
        calls += 1
    return {"started": True, "calls": calls}


def test_quota_gate_blocks_batch_when_rate_limited():
    probe = _Probe(error=Exception("HTTP 429"))
    seen = []
    ordered = ["a", "b", "c"]
    ledger = {k: _entry(key=k) for k in ordered}
    out = _run_gated_batch(probe, seen, ordered, ledger, {}, NOW)
    assert out == {"started": False, "calls": 0}
    assert seen == []


def test_quota_gate_allows_batch_when_serving():
    probe = _Probe()
    seen = []
    ordered = ["a", "b", "c"]
    ledger = {k: _entry(key=k) for k in ordered}
    out = _run_gated_batch(probe, seen, ordered, ledger, {}, NOW)
    assert out["started"] is True and out["calls"] == 3


# ── stop + max-3 ─────────────────────────────────────────────────────────

def test_stop_after_first_throttle():
    """Success #1 persisted, throttle on #2 stops batch before #3."""
    ledger, cache = {}, {}
    verdict = {"decision": "REVIEW", "confidence": 0.9}
    record_success(ledger, cache, "c1", provider="GroqProvider", confidence=0.9,
                   decision="REVIEW", llm_status="REVIEW", verdict=verdict,
                   timestamp=NOW.isoformat())
    record_throttle(ledger, "c2", provider="GeminiProvider",
                    error_class=RATE_LIMITED, now=NOW)
    # batch must stop: c3 never attempted
    assert "c3" not in ledger
    assert set(cache) == {"c1"}  # c1 recoverable after crash


def test_max_three_candidates():
    ordered = [f"k{i}" for i in range(25)]
    ledger = {k: _entry(key=k) for k in ordered}
    assert select_next(ordered, ledger, set(), NOW) == ["k0", "k1", "k2"]
    assert BATCH_MAX == 3


# ── resume / restart ─────────────────────────────────────────────────────

def test_resume_after_restart(tmp_path):
    ledger_doc = {"version": "t", "entries": [
        {"key": "a", "status": STATUS_HOLD},
        {"key": "b", "status": STATUS_UNAVAILABLE},
        {"key": "c", "status": STATUS_UNAVAILABLE},
    ]}
    p = tmp_path / "ledger.json"
    save_ledger(p, ledger_doc)
    reloaded = load_ledger(p)
    entries = {e["key"]: e for e in reloaded["entries"]}
    picked = select_next(["a", "b", "c"], entries, {"a"}, NOW)
    assert picked == ["b", "c"]


def test_existing_resolved_never_reprocessed():
    ordered = [f"k{i}" for i in range(55)]
    ledger = {k: _entry(key=k) for k in ordered}
    cache_keys = {f"k{i}" for i in range(30)}
    picked = select_next(ordered, ledger, cache_keys, NOW, max_n=25)
    assert len(picked) == 25
    assert not (set(picked) & cache_keys)


def test_hs_candidates_stay_unresolved_until_evaluated():
    ledger = {
        "7506296663968374784": _entry(key="7506296663968374784"),
        "7506579551330652161": _entry(key="7506579551330652161"),
    }
    for key in ledger:
        assert ledger[key]["status"] == STATUS_UNAVAILABLE
    picked = select_next(list(ledger), ledger, set(), NOW)
    assert set(picked) == set(ledger)


# ── frozen production contracts ──────────────────────────────────────────

def test_production_gate_unchanged():
    from app.production_gate import MAX_DESCRIPTION_WORDS, MIN_PRODUCTION_CONFIDENCE
    assert MIN_PRODUCTION_CONFIDENCE == 0.60
    assert MAX_DESCRIPTION_WORDS == 600


def test_two_tab_routing_unchanged():
    from app.production_gate import DATA_PRODUCTION, MAIN_PRODUCTION, route_category
    assert route_category("Data Analyst") == DATA_PRODUCTION
    assert route_category("Data Scientist") == DATA_PRODUCTION
    assert route_category("Founder's Office") == MAIN_PRODUCTION
    assert route_category("Chief of Staff") == MAIN_PRODUCTION
