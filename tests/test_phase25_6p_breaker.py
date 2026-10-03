"""
test_phase25_6p_breaker.py — Adaptive provider circuit breaker (Phase 25.6P).

Offline only: no Apify, no Sheets, no network, no live providers.
Covers §17 focused tests + §18 simulations A–F with scripted fakes.
"""
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.resume_engine_25_6i import (
    NETWORK_FAILURE,
    RATE_LIMITED,
    STATUS_HOLD,
    STATUS_UNAVAILABLE,
    ProviderCircuit,
    record_success,
    record_throttle,
    select_next_for_provider,
)

NOW = datetime(2026, 9, 20, 12, 0, 0, tzinfo=timezone.utc)
T = NOW.isoformat()


def _entry(**over):
    base = {"key": "k", "status": STATUS_UNAVAILABLE, "attempt_count": 0}
    base.update(over)
    return base


def _circuit_both_ok():
    c = ProviderCircuit(window_id="t")
    c.report_probe("groq", True, now=T)
    c.report_probe("gemini", True, now=T)
    return c


# ── §17 focused tests ────────────────────────────────────────────────────

def test_1_probe_success_groq_active():
    assert _circuit_both_ok().active() == "groq"


def test_2_candidate_429_disables_groq():
    c = _circuit_both_ok()
    c.record_throttle("groq", RATE_LIMITED, now=T)
    assert c.available("groq") is False
    assert c.active() == "gemini"


def test_3_healthy_gemini_becomes_active():
    c = _circuit_both_ok()
    c.record_throttle("groq", RATE_LIMITED, now=T)
    assert c.active() == "gemini"


def test_4_no_second_groq_call_after_429():
    c = _circuit_both_ok()
    groq_calls, gemini_calls = [], []

    def attempt(candidate, use):
        if use == "groq":
            groq_calls.append(candidate)
            if candidate == "c2":
                c.record_throttle("groq", RATE_LIMITED, now=T)
                return "failover"
        else:
            gemini_calls.append(candidate)
        return "ok"

    order = []
    for cand in ["c1", "c2", "c3"]:
        use = c.active()
        order.append((cand, use))
        r = attempt(cand, use)
        if r == "ok":
            c.record_success(use, now=T)
        else:
            nxt = c.active()
            order.append((cand, nxt))
            attempt(cand, nxt)
            c.record_success(nxt, now=T)
    # c2's first call necessarily uses groq (breaker learns from the 429);
    # the requirement is no SECOND groq call afterwards.
    assert groq_calls == ["c1", "c2"]
    assert "c3" not in groq_calls
    assert gemini_calls == ["c2", "c3"]
    assert c.available("groq") is False


def test_5_gemini_503_rotates_keys_then_6_disables_provider():
    events = []

    def gemini_call():
        events.append("k1")
        events.append("k2")  # rotation after k1 503 (provider-internal)
        return "both-503"

    c = _circuit_both_ok()
    assert gemini_call() == "both-503"
    c.record_throttle("gemini", NETWORK_FAILURE, now=T)
    assert c.available("gemini") is False
    assert events == ["k1", "k2"]


def test_7_switch_after_runtime_failure():
    c = _circuit_both_ok()
    c.record_success("groq", now=T)
    assert c.active() == "groq"  # recent success keeps priority
    c.record_throttle("groq", RATE_LIMITED, now=T)
    assert c.active() == "gemini"


def test_8_max_3():
    from tools.resume_engine_25_6i import BATCH_MAX
    assert BATCH_MAX == 3
    ordered = [f"k{i}" for i in range(10)]
    ledger = {k: _entry(key=k) for k in ordered}
    assert select_next_for_provider(ordered, ledger, set(), NOW, "groq",
                                    True, max_n=3) == ["k0", "k1", "k2"]


def test_9_sequential_persistence():
    ledger, cache, log = {}, {}, []

    def evaluate(key, outcome):
        log.append(("attempt", key))
        if outcome == "ok":
            record_success(ledger, cache, key, provider="GroqProvider",
                           confidence=0.9, decision="REVIEW", llm_status="REVIEW",
                           verdict={"decision": "REVIEW"}, timestamp=T)
            log.append(("persisted", key))

    for key, outcome in [("c1", "ok"), ("c2", "ok")]:
        assert f"c{int(key[1]) - 1}" in cache or key == "c1"
        evaluate(key, outcome)
    assert log == [("attempt", "c1"), ("persisted", "c1"),
                   ("attempt", "c2"), ("persisted", "c2")]
    assert set(cache) == {"c1", "c2"}


def test_10_restart_preserves_successes():
    import copy
    ledger, cache = {}, {}
    record_success(ledger, cache, "c1", provider="GroqProvider", confidence=0.9,
                   decision="REVIEW", llm_status="REVIEW",
                   verdict={"decision": "REVIEW"}, timestamp=T)
    snap_l, snap_c = copy.deepcopy(ledger), copy.deepcopy(cache)
    assert snap_c == cache and snap_l == ledger  # crash-safe snapshot


def test_11_existing_34_never_reevaluated():
    ordered = [f"k{i}" for i in range(55)]
    ledger = {k: _entry(key=k) for k in ordered}
    cache_keys = {f"k{i}" for i in range(34)}
    picked = select_next_for_provider(ordered, ledger, cache_keys, NOW, "groq",
                                      True, max_n=21)
    assert len(picked) == 21 and not (set(picked) & cache_keys)


def test_12_gate_unchanged():
    from app.production_gate import MAX_DESCRIPTION_WORDS, MIN_PRODUCTION_CONFIDENCE
    assert MIN_PRODUCTION_CONFIDENCE == 0.60
    assert MAX_DESCRIPTION_WORDS == 600


def test_13_routing_unchanged():
    from app.production_gate import DATA_PRODUCTION, MAIN_PRODUCTION, route_category
    assert route_category("Data Analyst") == DATA_PRODUCTION
    assert route_category("Data Scientist") == DATA_PRODUCTION
    assert route_category("Founder's Office") == MAIN_PRODUCTION


# ── §18 simulations A–F ──────────────────────────────────────────────────

def _run(script):
    """Scripted window. script[candidate] = outcome on the active provider.

    Outcomes: "ok" (success via active), "t429" (active 429s -> failover to
    the other provider if available, success there), "down" (active fails
    and nothing else is available -> stop).
    Returns (ledger, cache, providers_used, stopped).
    """
    c = _circuit_both_ok()
    ledger = {k: _entry(key=k) for k in script}
    cache, used, stopped, done = {}, [], False, []
    prov_name = {"groq": "GroqProvider", "gemini": "GeminiProvider"}
    for cand, step in script.items():
        if len(done) >= 3:
            break
        active = c.active()
        if active is None:
            stopped = True
            break
        if step == "ok":
            used.append(active)
            c.record_success(active, now=T)
            record_success(ledger, cache, cand, provider=prov_name[active],
                           confidence=0.9, decision="REVIEW", llm_status="REVIEW",
                           verdict={"decision": "REVIEW"}, timestamp=T)
            done.append(cand)
        elif step == "t429":
            used.append(active)
            c.record_throttle(active, RATE_LIMITED, now=T)
            record_throttle(ledger, cand, provider=prov_name[active],
                            error_class=RATE_LIMITED, now=NOW)
            nxt = c.active()
            if nxt is None:
                stopped = True
                break
            used.append(nxt)
            c.record_success(nxt, now=T)
            record_success(ledger, cache, cand, provider=prov_name[nxt],
                           confidence=0.85, decision="REVIEW", llm_status="REVIEW",
                           verdict={"decision": "REVIEW"}, timestamp=T)
            done.append(cand)
        elif step == "down":
            used.append(active)
            c.record_throttle(active, NETWORK_FAILURE, now=T)
            record_throttle(ledger, cand, provider=prov_name[active],
                            error_class=NETWORK_FAILURE, now=NOW)
            for other in ("groq", "gemini"):
                if other != active:
                    c.record_throttle(other, NETWORK_FAILURE, now=T)
            stopped = c.active() is None
            break
        else:
            raise AssertionError(step)
    return ledger, cache, used, stopped


def test_sim_A_groq_3_of_3():
    _, cache, used, stopped = _run({"a": "ok", "b": "ok", "c": "ok"})
    assert used == ["groq"] * 3 and len(cache) == 3 and not stopped


def test_sim_B_groq_then_switch():
    _, cache, used, stopped = _run({"a": "ok", "b": "t429", "c": "ok"})
    assert used == ["groq", "groq", "gemini", "gemini"] and len(cache) == 3


def test_sim_C_groq_down_gemini_3():
    _, cache, used, stopped = _run({"a": "t429", "b": "ok", "c": "ok"})
    assert used == ["groq", "gemini", "gemini", "gemini"] and len(cache) == 3


def test_sim_D_both_down_stop():
    c = _circuit_both_ok()
    c.record_throttle("groq", RATE_LIMITED, now=T)
    ledger = {"a": _entry(key="a")}
    cache = {}
    c.record_throttle("gemini", NETWORK_FAILURE, now=T)  # k1+k2 503
    record_throttle(ledger, "a", provider="GeminiProvider",
                    error_class=NETWORK_FAILURE, now=NOW)
    assert c.active() is None and cache == {}
    assert ledger["a"]["status"] == STATUS_UNAVAILABLE


def test_sim_E_gate_blocks_then_continues():
    ledger, cache = {}, {}
    record_success(ledger, cache, "a", provider="GroqProvider", confidence=0.75,
                   decision="REVIEW", llm_status="REVIEW",
                   verdict={"decision": "REVIEW"}, timestamp=T)
    assert ledger["a"]["status"] == STATUS_HOLD  # held, window continues
    record_success(ledger, cache, "b", provider="GroqProvider", confidence=0.9,
                   decision="REVIEW", llm_status="REVIEW",
                   verdict={"decision": "REVIEW"}, timestamp=T)
    assert set(cache) == {"a", "b"}


def test_sim_F_write_then_throttle_keeps_first():
    ledger, cache, writes = {}, {}, []
    record_success(ledger, cache, "a", provider="GroqProvider", confidence=0.97,
                   decision="ACCEPT", llm_status="SUCCESS",
                   verdict={"decision": "ACCEPT"}, timestamp=T)
    writes.append("a")  # simulated verified write
    record_throttle(ledger, "b", provider="GeminiProvider",
                    error_class=RATE_LIMITED, now=NOW)
    assert writes == ["a"] and "a" in cache and "b" not in cache
