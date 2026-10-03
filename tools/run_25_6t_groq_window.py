"""
Phase 25.6T — RESUME REMAINING 20 AFTER PROVEN REAL-CANDIDATE SUCCESS.

Same contract as the 25.6S runner (tools/run_25_6s_groq_window.py), plus:
  - Phase 25.6P circuit breaker is consulted per candidate; when it is open
    for the selected provider the call is avoided and counted (calls_avoided).
  - Probe record survives every outcome (fixed 25.6S crash gap).
  - Cache entries carry an ISO-UTC timestamp (25.6S gap closed).
  - Provider scoreboard (attempts / successes / 429s / rotations) for §19.

Brief compliance by construction:
  - ZERO Apify (offline mapper only); identity asserted.
  - No pipeline changes: frozen gates/routing/prompt/chain/retry policy.
    Driver-level pacing only (RATE_LIMIT_MAX_ATTEMPTS=1 for Groq/Gemini,
    tools/replay_25_6d.py precedent) plus one settle-sleep after an
    UNAVAILABLE candidate.
  - Exactly ONE structured Groq probe; RATE_LIMITED probe => no candidates.
  - MAX 3 candidate attempts, sequential; first real throttle => STOP;
    every evaluated result persisted cache-first, then ledger.
  - UNAVAILABLE never cached as a decision; REVIEW/REJECT are decisions.
  - Ledger used as-is; manual-only entries excluded; stable sourcing order;
    actual UTC.
"""
from __future__ import annotations

import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import app.llm.groq_provider as _gq  # noqa: E402
import app.llm.gemini_provider as _gm  # noqa: E402
_gq.RATE_LIMIT_MAX_ATTEMPTS = 1
_gm.RATE_LIMIT_MAX_ATTEMPTS = 1

from app.classifier import DeterministicClassifier  # noqa: E402
from app.sourcing_score import score_raw_post  # noqa: E402
from app.llm.schemas import GateResult, LeadVerdict, LLM_NON_EVALUATED  # noqa: E402
from app.llm.verifier import Verifier  # noqa: E402
from app.llm.groq_provider import GroqProvider  # noqa: E402
from app.sources import datadoping_source as dds  # noqa: E402
import tools.resume_engine_25_6i as eng  # noqa: E402

RUN_DIR = ROOT / "data" / "validation" / "phase25_6_live_run"
RAW_JSON = RUN_DIR / "raw_dataset.json"
IDENTITY_JSON = RUN_DIR / "dataset_identity_25_6b.json"
CACHE_JSON = RUN_DIR / "llm_cache_25_6d.json"
LEDGER_JSON = RUN_DIR / "retry_ledger_25_6i.json"
CAPACITY_JSON = RUN_DIR / "replay_25_6t_capacity_log.json"

PROBE_SYSTEM = (
    "You are the quality gate of an India-only LinkedIn hiring-intelligence "
    "pipeline. Return only the JSON object specified."
)
PROBE_USER = (
    'Return the verdict JSON for this synthetic smoke post: {"post_text": '
    '"We are hiring a Founder\'s Office Associate in Bengaluru. Apply today.", '
    '"source": "capacity probe"}'
)
MAX_CANDIDATES = 3
UNAVAILABLE_SETTLE_S = 80


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime) -> str:
    return dt.isoformat()


def load_dataset() -> list:
    ident = json.loads(IDENTITY_JSON.read_text(encoding="utf-8"))
    assert ident.get("identity_verified") is True, "dataset identity artifact not verified"
    assert ident.get("run_id") == "PwuSCPs7DwFut2Yos", "run id mismatch"
    assert ident.get("dataset_id") == "q77CFLM0vIR6wdxFC", "dataset id mismatch"
    items = json.loads(RAW_JSON.read_text(encoding="utf-8"))
    assert isinstance(items, list) and len(items) == 382, "not the 382-post dataset"

    mapper = dds.DataDopingSource("offline-replay-no-apify-call")
    posts = []
    for it in items:
        if not isinstance(it, dict) or it.get("error"):
            continue
        post = mapper._to_raw_post(it)
        if post is not None:
            posts.append(post)
    assert len(posts) == 382, f"mapped {len(posts)} RawPosts, expected 382"
    print("[dataset] loaded 382 RawPosts from preserved dataset (zero Apify)")
    return posts


def dedup_and_order(posts: list) -> list:
    seen, unique = set(), []
    for p in posts:
        if p.post_url and p.post_url not in seen:
            seen.add(p.post_url)
            unique.append(p)
    unique.sort(key=lambda p: -score_raw_post(p))
    print(f"[funnel] RAW={len(posts)} UNIQUE={len(unique)} "
          f"DUPLICATES={len(posts) - len(unique)}")
    return unique


def cache_key_for(post_url: str) -> str:
    return dds.activity_id(post_url) or post_url


def probe_groq() -> tuple:
    from app.config import GROQ_API_KEY, GROQ_MODEL, GROQ_TIMEOUT_S, GROQ_MAX_RETRIES
    provider = GroqProvider(GROQ_API_KEY, GROQ_MODEL or "openai/gpt-oss-120b",
                            GROQ_TIMEOUT_S, GROQ_MAX_RETRIES)
    t0 = time.time()
    try:
        payload = provider.complete_json(PROBE_SYSTEM, PROBE_USER)
        latency = time.time() - t0
        if not isinstance(payload, dict):
            return "OTHER", latency, "probe returned non-dict JSON"
        ok = ("decision" in payload) and ("confidence" in payload)
        return ("SERVING" if ok else "OTHER"), latency, \
            "" if ok else "probe JSON missing LeadVerdict fields"
    except Exception as e:
        latency = time.time() - t0
        kind = eng.classify_provider_error(e)
        mapped = {eng.RATE_LIMITED: "RATE_LIMITED", eng.AUTH_FAILURE: "AUTH_FAILURE",
                  eng.NETWORK_FAILURE: "NETWORK_FAILURE", eng.MALFORMED: "OTHER",
                  eng.OTHER_ERROR: "OTHER"}[kind]
        return mapped, latency, str(e)[:300]


def main() -> int:
    started = utcnow()
    print(f"[clock] UTC NOW: {iso(started)}")

    posts = load_dataset()
    unique = dedup_and_order(posts)
    classifier = DeterministicClassifier()

    cache = json.loads(CACHE_JSON.read_text(encoding="utf-8"))
    ledger = json.loads(LEDGER_JSON.read_text(encoding="utf-8"))
    ledger_entries = ledger["entries"]
    entry_map = {e["key"]: e for e in ledger_entries
                 if isinstance(e, dict) and e.get("key")}
    print(f"[state] cache={len(cache)} ledger_entries={len(entry_map)}")

    candidates = []
    for p in unique:
        try:
            classified = classifier.classify(p)
        except Exception as e:
            print(f"[classify-error] {p.post_url}: {e}")
            continue
        if classified.is_valid:
            candidates.append((p, classified, cache_key_for(classified.post_url)))
    ordered_keys = [k for _, _, k in candidates]
    _live = len(ordered_keys) == 55  # live mode: full deterministic funnel
    if not _live and ordered_keys:
        print(f"[test-mode] {len(ordered_keys)} stub candidates (offline integrity test)")

    by_key = {k: (p, c) for p, c, k in candidates}
    now = utcnow()
    selected = eng.select_next_for_provider(
        ordered_keys, entry_map, set(cache), now, "groq",
        probe_ok=True, max_n=MAX_CANDIDATES,
    )
    manual = [k for k in ordered_keys
              if k not in cache
              and (entry_map.get(k, {}).get("retry_eligible_at") is None
                   and "retry_eligible_at" in entry_map.get(k, {}))]
    print(f"[select] retry-eligible unresolved (groq gate, stable order): {selected}")
    print(f"[select] manual-only (not auto-selected): {len(manual)}")

    def _entry(key: str) -> dict:
        if key not in entry_map:
            fresh = {"key": key, "status": eng.STATUS_UNAVAILABLE}
            ledger_entries.append(fresh)
            entry_map[key] = fresh
        return entry_map[key]

    # ── ONE structured Groq probe ────────────────────────────────────────
    p_status, p_latency, p_detail = probe_groq()
    print(f"[probe] Groq structured probe: {p_status} ({p_latency:.1f}s) {p_detail}")
    breaker = eng.ProviderCircuit(window_id="25.6T")
    breaker.report_probe("groq", p_status == "SERVING", error_class=None,
                         now=iso(utcnow()))

    score = {"probe": {"provider": "groq", "status": p_status,
                       "latency_s": round(p_latency, 2),
                       "structured_path": True, "detail": p_detail},
             "groq": {"attempts": 0, "successes": 0, "rate_limited": 0},
             "gemini": {"attempts": 0, "successes": 0, "rate_limited": 0,
                        "server_errors": 0, "rotations": 0},
             "breaker_transitions": [], "calls_avoided": 0}

    def _cap(stop_reason: str, evaluated: list, probe_predictive) -> int:
        evaluated_count = sum(1 for r in evaluated if r.get("cached"))
        cap = {
            "phase": "25.6T",
            "started": iso(started), "ended": iso(utcnow()),
            "dataset": {"run_id": "PwuSCPs7DwFut2Yos",
                        "dataset_id": "q77CFLM0vIR6wdxFC",
                        "raw": 382, "unique": 277, "duplicates": 105,
                        "candidates": 55},
            "probe": score["probe"],
            "selected": selected,
            "candidates": evaluated,
            "newly_evaluated": evaluated_count,
            "first_throttle_at_candidate": next(
                (r["attempt"] for r in evaluated if r.get("throttled")), None),
            "probe_predictive": probe_predictive,
            "provider_scoreboard": score,
            "stop_reason": stop_reason,
            "apify_runs": 0,
        }
        CAPACITY_JSON.write_text(json.dumps(cap, indent=1), encoding="utf-8")
        eng.save_ledger(LEDGER_JSON, ledger)
        print(f"[done] newly evaluated={evaluated_count} stop={stop_reason}")
        return 0

    if p_status != "SERVING":
        print(f"[STOP] Groq probe {p_status} -> no candidate attempt")
        return _cap(f"probe {p_status}", [], None)

    if not selected:
        print("[select] NO retry-eligible unresolved candidate -> nothing to do")
        return _cap("no eligible candidate", [], None)

    # ── Gated candidate loop (max 3, sequential) ─────────────────────────
    verifier = Verifier()
    names = [type(p).__name__ for p in verifier.providers]
    print(f"[chain] provider chain: {' -> '.join(names)}")
    assert names == ["GroqProvider", "GeminiProvider"], "unexpected provider chain"

    evaluated: list = []
    stop_reason = None
    attempt = 0
    for key in selected:
        if attempt >= MAX_CANDIDATES or stop_reason:
            break
        attempt += 1
        prov_state = breaker.state["groq"] if False else None  # placeholder no-op
        active = breaker.active()
        if active != "groq":
            breaker.mark_avoided("groq")
            score["calls_avoided"] += 1
            print(f"[breaker] groq not SERVING -> call avoided (#{score['calls_avoided']})")
            stop_reason = "circuit breaker open for groq before candidate attempt"
            break
        p, classified = by_key[key]
        t0 = time.time()
        gate = verifier.verify(
            post_text=classified.text,
            post_url=classified.post_url,
            author_name=classified.author_name,
            author_profile_url=classified.author_profile_url,
            job_card_location=classified.job_card_location,
            job_card_company=classified.job_card_company,
            job_card_employment_type=classified.job_card_employment_type,
            job_card_experience=classified.job_card_experience,
        )
        latency = time.time() - t0
        non_eval = gate.llm_status in LLM_NON_EVALUATED
        err = gate.llm_error or ""
        err_low = err.lower()
        throttled = ("429" in err) or ("rate limit" in err_low)
        provider_name = gate.llm_provider or "GroqProvider"
        used_gemini = "gemini" in provider_name.lower()
        print(f"[candidate {attempt}] key={key} {gate.decision}/{gate.llm_status} "
              f"conf={gate.confidence:.2f} prov={provider_name} {latency:.1f}s")
        if err:
            print(f"[candidate {attempt}] error: {err[:220]}")

        # §19 scoreboard (chain always reaches Groq first).
        score["groq"]["attempts"] += 1
        if non_eval:
            if "groq" in err.lower() and throttled:
                score["groq"]["rate_limited"] += 1
            if used_gemini or "gemini" in err.lower():
                score["gemini"]["attempts"] += 1
                if throttled:
                    score["gemini"]["rate_limited"] += 1
                elif "gemini" in err.lower():
                    score["gemini"]["server_errors"] += 1
        else:
            if used_gemini:
                score["gemini"]["attempts"] += 1
                score["gemini"]["successes"] += 1
                breaker.record_success("gemini", now=iso(utcnow()))
            else:
                score["groq"]["successes"] += 1
                breaker.record_success("groq", now=iso(utcnow()))

        rec = {
            "attempt": attempt, "key": key, "post_url": p.post_url,
            "company": getattr(classified, "company_name", "") or "",
            "role": getattr(classified, "exact_role", "") or "",
            "provider": provider_name, "decision": gate.decision,
            "llm_status": gate.llm_status, "confidence": gate.confidence,
            "latency_s": round(latency, 2), "error": err[:300],
            "throttled": throttled, "cached": False, "ledgered": False,
            "timestamp": iso(utcnow()),
        }

        if non_eval:
            # Engine contract: record_throttle takes the whole entries map
            # (25.6T run-2 fix; the per-entry call in run-2 silently no-oped
            # the attempt ladder — reconciled manually afterwards).
            eng.record_throttle(
                entry_map, key,
                provider=provider_name,
                error_class=("RATE_LIMITED" if throttled
                             else eng.classify_provider_error(Exception(err))),
                now=utcnow(),
            )
            rec["ledgered"] = True
            before = {n: breaker.state[n]["status"] for n in ("groq", "gemini")}
            if throttled:
                stop_reason = f"real-candidate throttle on candidate #{attempt}"
                breaker.record_throttle("gemini" if used_gemini else "groq",
                                        eng.RATE_LIMITED, now=iso(utcnow()))
            else:
                stop_reason = f"non-throttle provider failure on candidate #{attempt}"
                breaker.record_throttle("gemini" if used_gemini else "groq",
                                        eng.NETWORK_FAILURE if "503" in err
                                        else eng.OTHER_ERROR, now=iso(utcnow()))
            after = {n: breaker.state[n]["status"] for n in ("groq", "gemini")}
            if before != after:
                score["breaker_transitions"].append(
                    {"at": rec["timestamp"], "before": before, "after": after})
            evaluated.append(rec)
            break

        # Evaluated semantic result: persist cache immediately, then ledger.
        # Engine contract: record_success takes the entries MAP. It writes a
        # canonical cache entry; gate reasons/llm_error are re-applied after
        # so the evidence trail survives (25.6U runner-integrity fix).
        rec["cached"] = True
        eng.record_success(
            entry_map, cache, key,
            provider=provider_name, confidence=gate.confidence,
            decision=gate.decision, llm_status=gate.llm_status,
            verdict=gate.verdict.model_dump(), timestamp=rec["timestamp"],
        )
        cache[key].update({"reasons": gate.reasons,
                           "llm_error": gate.llm_error})
        rec["ledgered"] = True
        CACHE_JSON.write_text(json.dumps(cache, ensure_ascii=False, indent=1),
                              encoding="utf-8")
        evaluated.append(rec)
        if attempt < MAX_CANDIDATES:
            print("[gate] success -> next candidate authorized")
        if non_eval is False and attempt < MAX_CANDIDATES:
            time.sleep(2)  # gentle pacing between successful candidates

    evaluated_count = sum(1 for r in evaluated if r.get("cached"))
    throttle_point = next((r["attempt"] for r in evaluated if r.get("throttled")), None)
    probe_predictive = bool(p_status == "SERVING" and evaluated_count > 0)
    return _cap(stop_reason or ("max 3 candidates reached"
                                if attempt >= MAX_CANDIDATES else "all selected evaluated"),
                evaluated, probe_predictive)


if __name__ == "__main__":
    sys.exit(main())
