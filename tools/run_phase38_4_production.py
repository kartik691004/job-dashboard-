"""run_phase38_4_production.py — Phase 38.4: run the REAL production pipeline
over the 213 Phase 38.2 live posts (Apify run DozWlUGaazYJwIDCw).

The scrape step is the ONLY thing stubbed (rule 18: no second Apify scrape).
Everything downstream is the untouched production pipeline:
  dedup -> sourcing-score order -> DeterministicClassifier -> LLM verifier
  -> production_gate.route_production_post -> enrichment -> SheetsWriter
  .write_production (fail-closed re-validation + idempotent Source-Link dedup).

DRY_RUN is forced false BEFORE app.config loads (same contract as run_daily.py).
Review/Held is never written here: gate-held rows stay internal review artifacts.

── Transport notes (Phase 38.4) — operational ONLY, no gate is relaxed ──
* GROQ_API_KEY is revoked (HTTP 403 -> AUTH_FAILURE, deliberately NOT
  failover-eligible), so it can only ever dead-end the chain. It is omitted
  from this run's chain; if every Gemini model were unavailable the result is
  identical (UNAVAILABLE -> REVIEW -> excluded), so nothing is weakened.
* The .env model `gemini-3.6-flash` is hard quota-exhausted on BOTH Gemini
  keys (HTTP 429, free tier "limit: 20" generate_content_free_tier_requests,
  "retry in ~15h"). Google scopes the free-tier DAILY budget PER MODEL, so
  sibling models still have their own untouched budgets. Verified live today:
      gemini-3.5-flash       key0=OK      key1=503 high-demand
      gemini-3.1-flash-lite  key0=OK      key1=OK
      gemini-3-flash-preview key0=OK      key1=OK
  Those three form a chain of independent budgets, assembled below from the
  SAME production GeminiProvider transport used by build_provider_chain().
* `_verify` retries ONLY when llm_status == "UNAVAILABLE", which the verifier
  returns only when transport/schema produced NO verdict at all. Any genuine
  ACCEPT/REVIEW/REJECT is final and never retried, so throttling and retry
  cannot launder a REVIEW or a REJECT into an ACCEPT.
"""
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
# reconfigure() in place — never create a second TextIOWrapper over the
# shared buffer (its GC closes the buffer mid-run; observed live in Phase 32).
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
    sys.stderr.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
except Exception:
    pass

os.environ["DRY_RUN"] = "false"   # must precede app.config import
os.environ["LLM_PROVIDER"] = "gemini"

# Free-tier back-pressure: space calls out so a live model is not pushed into
# 429/503 by our own burst. Touches pacing only, never a decision.
THROTTLE_S = float(os.environ.get("PHASE38_4_THROTTLE_S", "4"))
UNAVAILABLE_RETRIES = int(os.environ.get("PHASE38_4_UNAVAILABLE_RETRIES", "3"))

# Live free-tier models, best first. Each has its own daily request budget.
GEMINI_MODELS = [
    m for m in (os.environ.get("PHASE38_4_GEMINI_MODELS")
                or "gemini-3.5-flash,gemini-3.1-flash-lite,gemini-3-flash-preview").split(",")
    if m.strip()
]

from app.models import RawPost                                    # noqa: E402
from app.orchestrator import Orchestrator                         # noqa: E402
import app.sheets_writer as sw                                     # noqa: E402
from app.production_gate import route_production_post, MAIN_PRODUCTION, DATA_PRODUCTION  # noqa: E402
from app.llm.gemini_provider import GeminiProvider, collect_gemini_keys  # noqa: E402
from app import config as cfg                                      # noqa: E402

RUN_ID = "DozWlUGaazYJwIDCw"
SRC = Path("data/validation/phase38_2_live_hm_recovery/raw_posts.json")
OUT = Path("data/validation/phase38_4_production_run")
OUT.mkdir(parents=True, exist_ok=True)

posts = [RawPost(**d) for d in json.loads(SRC.read_text(encoding="utf-8"))]
print(f"source records: {len(posts)} (run {RUN_ID})")

orch = Orchestrator(production_gate=True)
print(f"DRY_RUN={orch.sheets_writer.dry_run} | production_gate={orch.production_gate_enabled}")
assert orch.sheets_writer.dry_run is False, "fail closed: writer is not live"
assert orch.production_gate_enabled is True, "fail closed: production gate disabled"

# ── Build the live Gemini chain from the production transport ──
_keys = collect_gemini_keys(cfg.GEMINI_API_KEY, cfg.GEMINI_API_KEY_2, cfg.GEMINI_API_KEYS)
assert _keys, "fail closed: no Gemini API key configured"
_timeout = getattr(cfg, "GROQ_TIMEOUT_S", 30.0)
_retries = getattr(cfg, "GROQ_MAX_RETRIES", 1)
chain = [GeminiProvider(_keys, m.strip(), _timeout, _retries) for m in GEMINI_MODELS]
orch.verifier.providers = chain
orch.verifier.provider = chain[0]
print(f"LLM chain: {' -> '.join(GEMINI_MODELS)} | keys={len(_keys)} "
      f"| throttle={THROTTLE_S}s | unavailable_retries={UNAVAILABLE_RETRIES}")

# Rule 18: no Apify call — the scrape returns the already-fetched 213 posts.
orch.source.search_all = lambda *a, **k: list(posts)

# ── LLM decision tally (ACCEPT / REVIEW / REJECT / UNAVAILABLE) ──
tally = {"ACCEPT": 0, "REVIEW": 0, "REJECT": 0, "UNAVAILABLE": 0}
stats = {"sent_to_llm": 0, "transport_retries": 0}
_orig_verify = orch.verifier.verify


def _verify(**kw):
    """Tally once per post. Retries ONLY a no-verdict transport failure."""
    stats["sent_to_llm"] += 1
    result = None
    for attempt in range(1, UNAVAILABLE_RETRIES + 1):
        time.sleep(THROTTLE_S)
        result = _orig_verify(**kw)
        if str(getattr(result, "llm_status", "") or "") != "UNAVAILABLE":
            break  # a real verdict was produced -> final, never retried
        if attempt < UNAVAILABLE_RETRIES:
            stats["transport_retries"] += 1
            backoff = 8.0 * (2 ** (attempt - 1))
            print(f"   [llm] no verdict (transport) attempt {attempt}/"
                  f"{UNAVAILABLE_RETRIES}; backing off {backoff:.0f}s", flush=True)
            time.sleep(backoff)

    decision = str(getattr(result, "decision", "") or "")
    if decision == "ACCEPT":
        tally["ACCEPT"] += 1
    elif decision == "REJECT":
        tally["REJECT"] += 1
    else:
        tally["REVIEW"] += 1
    if str(getattr(result, "llm_status", "") or "") == "UNAVAILABLE":
        tally["UNAVAILABLE"] += 1
        print("   [llm] UNAVAILABLE after retries -> excluded (fail closed)", flush=True)
    return result


orch.verifier.verify = _verify

# ── pre-write printout + real write (hooked, not replaced) ──
_real_write = sw.SheetsWriter.write_production
pre = {}


def _hooked(self, records):
    eligible_total = len(getattr(orch, "_accepted_details", []) or [])
    main = sum(1 for p, g in records if route_production_post(p, g).destination == MAIN_PRODUCTION)
    data = sum(1 for p, g in records if route_production_post(p, g).destination == DATA_PRODUCTION)
    pre.update({"eligible_total": eligible_total, "post_dedup_records": len(records),
                "main": main, "data": data,
                "duplicates_skipped": eligible_total - len(records),
                "llm_decisions": dict(tally), **stats})
    print("=" * 72)
    print("PRE-WRITE SUMMARY (nothing written yet)")
    print(f"  source records            : {len(posts)}")
    print(f"  deterministic candidates  : {stats['sent_to_llm']}")
    print(f"  sent to LLM               : {stats['sent_to_llm']}")
    print(f"  ACCEPT / REVIEW / REJECT  : {tally['ACCEPT']} / {tally['REVIEW']} / {tally['REJECT']}")
    print(f"  UNAVAILABLE (LLM)         : {tally['UNAVAILABLE']}")
    print(f"  transport retries         : {stats['transport_retries']}")
    print(f"  production-eligible (gate): {eligible_total}")
    print(f"  duplicates vs MAIN+DATA   : {eligible_total - len(records)}")
    print(f"  intended MAIN             : {main}")
    print(f"  intended DATA             : {data}")
    print(f"  LLM chain                 : {' -> '.join(GEMINI_MODELS)}")
    print("=" * 72, flush=True)
    return _real_write(self, records)


sw.SheetsWriter.write_production = _hooked

summary = orch.run_pipeline(limit=10)
pre["summary"] = summary
print("\nPIPELINE SUMMARY")
print(json.dumps(summary, indent=2, default=str))
(OUT / "production_run_summary.json").write_text(json.dumps(
    {"run_id": RUN_ID, "llm_chain": GEMINI_MODELS, "tally": tally, "stats": stats,
     "pre_write": {k: v for k, v in pre.items() if k != "summary"},
     "summary": summary}, indent=2, default=str), encoding="utf-8")
print("\nsaved:", OUT / "production_run_summary.json")