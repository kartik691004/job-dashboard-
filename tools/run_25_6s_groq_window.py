"""
Phase 25.6S — GROQ-FIRST CAPACITY WINDOW RESUME (runner).

Brief compliance by construction:
  - ZERO Apify: no scraper is constructed; the dataset is loaded from the
    preserved raw_dataset.json and mapped through the same RawPost fields
    the actor mapping produces (deterministic reclassification is
    byte-stable, so the in-DB fields are reconstructed exactly).
  - No pipeline changes: gates, routing, prompt, chain, retry policy are
    untouched. Driver-level pacing only (RATE_LIMIT_MAX_ATTEMPTS=1 for
    Groq/Gemini, mirroring tools/replay_25_6d.py precedent) and one
    settle-sleep after an UNAVAILABLE candidate — policy, not semantics.
  - Exactly ONE structured Groq probe via GroqProvider.complete_json on the
    verifier's real structured-output path (no tiny ping).
  - MAX 3 candidate attempts; first real throttle => STOP; every evaluated
    candidate is persisted to the cache immediately and ledgered.
  - Ledger used as-is (no wipe/rebuild); provider-specific 25.6O blocks and
    manual-only entries honored; stable sourcing-score order; actual UTC.
  - UNAVAILABLE is never cached as a decision; REVIEW/REJECT are decisions.

Outputs (all under RUN_DIR, new 25.6S names; nothing overwritten):
  - probe/candidate log to stdout (captured to logs/replay_25_6s.log by the
    invoking shell)
  - data/validation/phase25_6_live_run/replay_25_6s_capacity_log.json
  - updates llm_cache_25_6d.json + retry_ledger_25_6i.json only on evaluated
    results / throttles
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
# Driver-level pacing (precedent: tools/replay_25_6d.py). A 429 aborts a
# provider immediately instead of burning ~90s of in-process backoff; the
# stop-on-throttle semantics and bounded attempts are unchanged.
_gq.RATE_LIMIT_MAX_ATTEMPTS = 1
_gm.RATE_LIMIT_MAX_ATTEMPTS = 1

from app.config import GROQ_MODEL  # noqa: E402  (model name for the probe log only)
from app.models import RawPost  # noqa: E402
from app.classifier import DeterministicClassifier  # noqa: E402
from app.sourcing_score import score_raw_post  # noqa: E402
from app.llm.schemas import GateResult, LeadVerdict, LLM_NON_EVALUATED  # noqa: E402
from app.llm.verifier import Verifier  # noqa: E402
from app.llm.groq_provider import GroqProvider  # noqa: E402
import tools.resume_engine_25_6i as eng  # noqa: E402

RUN_DIR = ROOT / "data" / "validation" / "phase25_6_live_run"
RAW_JSON = RUN_DIR / "raw_dataset.json"
IDENTITY_JSON = RUN_DIR / "dataset_identity_25_6b.json"
CACHE_JSON = RUN_DIR / "llm_cache_25_6d.json"
LEDGER_JSON = RUN_DIR / "retry_ledger_25_6i.json"
CAPACITY_JSON = RUN_DIR / "replay_25_6s_capacity_log.json"

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
PROBE_SYSTEM = (
    "You are the quality gate of an India-only LinkedIn hiring-intelligence "
    "pipeline. Return only the JSON object specified."
)
PROBE_USER = (
    'Return the verdict JSON for this synthetic smoke post: {"post_text": '
    '"We are hiring a Founder\'s Office Associate in Bengaluru. Apply today.", '
    '"source": "capacity probe"}'
)
PROBE_MAX_S = 45
MAX_CANDIDATES = 3
UNAVAILABLE_SETTLE_S = 80


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime) -> str:
    return dt.isoformat()


def load_dataset() -> list[RawPost]:
    ident = json.loads(IDENTITY_JSON.read_text(encoding="utf-8"))
    assert ident.get("identity_verified") is True, "dataset identity artifact not verified"
    assert ident.get("run_id") == "PwuSCPs7DwFut2Yos", "run id mismatch"
    assert ident.get("dataset_id") == "q77CFLM0vIR6wdxFC", "dataset id mismatch"
    items = json.loads(RAW_JSON.read_text(encoding="utf-8"))
    assert isinstance(items, list) and len(items) == 382, "not the 382-post dataset"

    # Production's exact RawPost mapping (precedent: tools/replay_25_6d.py).
    # Constructing DataDopingSource performs NO network I/O; only the actor
    # client handle is created and then never used in this offline replay.
    from app.sources import datadoping_source as dds  # noqa: E402
    mapper = dds.DataDopingSource("offline-replay-no-apify-call")
    posts: list[RawPost] = []
    for it in items:
        if not isinstance(it, dict) or it.get("error"):
            continue
        post = mapper._to_raw_post(it)
        if post is not None:
            posts.append(post)
    assert len(posts) == 382, f"mapped {len(posts)} RawPosts, expected 382"
    print(f"[dataset] loaded 382 RawPosts from preserved dataset (zero Apify)")
    return posts


def dedup_and_order(posts: list[RawPost]) -> list[RawPost]:
    seen: set[str] = set()
    unique: list[RawPost] = []
    for p in posts:
        if p.post_url and p.post_url not in seen:
            seen.add(p.post_url)
            unique.append(p)
    unique.sort(key=lambda p: -score_raw_post(p))
    print(f"[funnel] RAW={len(posts)} UNIQUE={len(unique)} "
          f"DUPLICATES={len(posts) - len(unique)}")
    return unique


def cache_key_for(post_url: str) -> str:
    """Production cache key (precedent: tools/replay_25_6d.py:168)."""
    from app.sources import datadoping_source as dds  # noqa: E402
    return dds.activity_id(post_url) or post_url


def probe_groq() -> tuple[str, float, str]:
    """One structured Groq probe on the real structured-output path."""
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
        status = "SERVING" if ok else "OTHER"
        return status, latency, "" if ok else "probe JSON missing LeadVerdict fields"
    except Exception as e:
        latency = time.time() - t0
        kind = eng.classify_provider_error(e)
        mapped = {
            eng.RATE_LIMITED: "RATE_LIMITED", eng.AUTH_FAILURE: "AUTH_FAILURE",
            eng.NETWORK_FAILURE: "NETWORK_FAILURE", eng.MALFORMED: "OTHER",
            eng.OTHER_ERROR: "OTHER",
        }[kind]
        return mapped, latency, str(e)[:300]


def candidate_gate_result(classified, verifier: Verifier) -> GateResult:
    return verifier.verify(
        post_text=classified.text,
        post_url=classified.post_url,
        author_name=classified.author_name,
        author_profile_url=classified.author_profile_url,
        job_card_location=classified.job_card_location,
        job_card_company=classified.job_card_company,
        job_card_employment_type=classified.job_card_employment_type,
        job_card_experience=classified.job_card_experience,
    )


def gate_from_cache(d: dict) -> GateResult:
    return GateResult(
        verdict=LeadVerdict(**d["verdict"]),
        decision=d["decision"], status=d["status"], reasons=d["reasons"],
        llm_called=True, llm_status=d["llm_status"],
        llm_error=d.get("llm_error", ""), llm_provider=d.get("llm_provider", ""),
    )


def main() -> int:
    started = utcnow()
    print(f"[clock] UTC NOW: {iso(started)}")

    posts = load_dataset()
    unique = dedup_and_order(posts)
    classifier = DeterministicClassifier()

    cache = json.loads(CACHE_JSON.read_text(encoding="utf-8"))
    ledger = json.loads(LEDGER_JSON.read_text(encoding="utf-8"))
    ledger_entries = ledger["entries"]
    print(f"[state] cache={len(cache)} ledger_entries={len(ledger_entries)}")

    # Deterministic classification of the whole unique set (byte-stable with
    # the live run: identical RawPost fields => identical candidates).
    candidates: list[tuple[RawPost, object, str]] = []
    for p in unique:
        try:
            classified = classifier.classify(p)
        except Exception as e:
            print(f"[classify-error] {p.post_url}: {e}")
            continue
        if classified.is_valid:
            key = cache_key_for(classified.post_url)
            candidates.append((p, classified, key))
    print(f"[funnel] deterministic candidates: {len(candidates)}")

    by_key = {}
    for p, classified, key in candidates:
        by_key.setdefault(key, (p, classified))
    ordered_keys = [k for _, _, k in candidates]
    cache_keys = set(cache.keys())
    now = utcnow()
    entry_map = {e["key"]: e for e in ledger_entries if isinstance(e, dict) and e.get("key")}

    # Ledger-driven selection: all three gates, exactly as production.
    selected = eng.select_next_for_provider(
        ordered_keys, entry_map, cache_keys, now, "groq",
        probe_ok=True,  # probe_ok is gated AGAIN below, after the real probe
        max_n=MAX_CANDIDATES,
    )
    # Manual-only visibility (never auto-selected).
    manual = [k for k in ordered_keys
              if k not in cache_keys
              and (entry_map.get(k, {}).get("retry_eligible_at") is None
                   and "retry_eligible_at" in entry_map.get(k, {}))]
    print(f"[select] retry-eligible unresolved (groq gate, stable order): {selected}")
    print(f"[select] manual-only (not auto-selected): {len(manual)}")

    if not selected:
        print("[select] NO retry-eligible unresolved candidate -> nothing to do")
        cap = {
            "phase": "25.6S", "started": iso(started), "ended": iso(utcnow()),
            "probe": None, "selected": [], "candidates": [], "notes": ["no eligible candidate"],
        }
        CAPACITY_JSON.write_text(json.dumps(cap, indent=1), encoding="utf-8")
        return 0

    # ── ONE structured Groq probe ────────────────────────────────────────
    p_status, p_latency, p_detail = probe_groq()
    print(f"[probe] Groq structured probe: {p_status} ({p_latency:.1f}s) {p_detail}")
    breaker = eng.ProviderCircuit(window_id="25.6S")
    breaker.report_probe("groq", p_status == "SERVING",
                         error_class=None, now=iso(utcnow()))
    if p_status != "SERVING":
        print(f"[STOP] Groq probe {p_status} -> no candidate attempt (brief §3-§5)")
        cap = {
            "phase": "25.6S", "started": iso(started), "ended": iso(utcnow()),
            "probe": {"provider": "groq", "status": p_status,
                      "latency_s": round(p_latency, 2), "detail": p_detail,
                      "structured_path": True},
            "selected": selected, "candidates": [],
            "probe_predictive": None, "notes": ["stopped at probe"],
        }
        CAPACITY_JSON.write_text(json.dumps(cap, indent=1), encoding="utf-8")
        return 0

    # ── Gated candidate loop (max 3) ─────────────────────────────────────
    verifier = Verifier()
    names = [type(p).__name__ for p in verifier.providers]
    print(f"[chain] provider chain: {' -> '.join(names)}")
    assert names == ["GroqProvider", "GeminiProvider"], "unexpected provider chain"

    evaluated: list[dict] = []
    stop_reason = None
    attempt = 0
    for key in selected:
        if attempt >= MAX_CANDIDATES:
            break
        if stop_reason:
            break
        attempt += 1
        p, classified = by_key[key]
        t0 = time.time()
        gate = candidate_gate_result(classified, verifier)
        latency = time.time() - t0
        non_eval = gate.llm_status in LLM_NON_EVALUATED
        err = gate.llm_error or ""
        err_low = err.lower()
        throttled = ("429" in err) or ("rate limit" in err_low)
        provider_name = gate.llm_provider or "GroqProvider"
        print(f"[candidate {attempt}] key={key} {gate.decision}/{gate.llm_status} "
              f"conf={gate.confidence:.2f} prov={provider_name} {latency:.1f}s")
        if err:
            print(f"[candidate {attempt}] error: {err[:220]}")

        rec = {
            "attempt": attempt, "key": key, "post_url": p.post_url,
            "company": getattr(classified, "company_name", "") or "",
            "role": getattr(classified, "exact_role", "") or "",
            "provider": provider_name, "decision": gate.decision,
            "llm_status": gate.llm_status, "confidence": gate.confidence,
            "latency_s": round(latency, 2), "error": err[:300],
            "throttled": throttled, "cached": False, "ledgered": False,
        }

        def _entry(key: str) -> dict:
            for e in ledger_entries:
                if isinstance(e, dict) and e.get("key") == key:
                    return e
            fresh = {"key": key, "status": eng.STATUS_UNAVAILABLE}
            ledger_entries.append(fresh)
            entry_map[key] = fresh
            return fresh

        if non_eval:
            rec["throttled"] = throttled
            eng.record_throttle(
                _entry(key), key,
                provider=provider_name,
                error_class=("RATE_LIMITED" if throttled
                             else eng.classify_provider_error(Exception(err))),
                now=utcnow(),
            )
            rec["ledgered"] = True
            if throttled:
                stop_reason = f"real-candidate throttle on candidate #{attempt}"
                breaker.record_throttle("groq" if "groq" in provider_name.lower() else "gemini",
                                        eng.RATE_LIMITED, now=iso(utcnow()))
                evaluated.append(rec)
                break
            else:
                stop_reason = f"non-throttle provider failure on candidate #{attempt}"
                evaluated.append(rec)
                break

        # Evaluated semantic result: persist cache immediately.
        cache[key] = {
            "decision": gate.decision, "status": gate.status,
            "reasons": gate.reasons, "llm_status": gate.llm_status,
            "llm_error": gate.llm_error, "llm_provider": gate.llm_provider,
            "verdict": gate.verdict.model_dump(),
        }
        CACHE_JSON.write_text(json.dumps(cache, ensure_ascii=False, indent=1),
                              encoding="utf-8")
        rec["cached"] = True
        rec["throttled"] = throttled
        eng.record_success(
            _entry(key), cache, key,
            provider=provider_name, confidence=gate.confidence,
            decision=gate.decision, llm_status=gate.llm_status,
            verdict=gate.verdict.model_dump(), timestamp=iso(utcnow()),
        )
        rec["ledgered"] = True
        evaluated.append(rec)
        if attempt < MAX_CANDIDATES:
            print("[gate] success -> next candidate authorized")

    evaluated_count = sum(1 for r in evaluated if r["cached"])
    throttle_point = next((r["attempt"] for r in evaluated if r["throttled"]), None)
    probe_predictive = (p_status == "SERVING" and evaluated_count == 0
                        and throttle_point is not None)
    cap = {
        "phase": "25.6S",
        "started": iso(started), "ended": iso(utcnow()),
        "dataset": {"run_id": "PwuSCPs7DwFut2Yos", "dataset_id": "q77CFLM0vIR6wdxFC",
                    "raw": 382, "unique": 277, "duplicates": 105, "candidates": 55},
        "probe": {"provider": "groq", "status": p_status,
                  "latency_s": round(p_latency, 2), "structured_path": True,
                  "detail": p_detail},
        "selected": selected,
        "candidates": evaluated,
        "newly_evaluated": evaluated_count,
        "first_throttle_at_candidate": throttle_point,
        "probe_predictive": probe_predictive,
        "stop_reason": stop_reason or ("max 3 candidates reached"
                                        if attempt >= MAX_CANDIDATES else "all selected evaluated"),
        "apify_runs": 0,
    }
    CAPACITY_JSON.write_text(json.dumps(cap, indent=1), encoding="utf-8")
    eng.save_ledger(LEDGER_JSON, ledger)
    print(f"[done] newly evaluated={evaluated_count} stop={cap['stop_reason']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
