"""
Phase 32.1 gate/write pass — route ALREADY-EVALUATED ACCEPTs through the
unchanged production gate and guarded writer. ZERO LLM calls: only ACCEPT
verdicts carrying a stored gate_json are processed (GateResult rehydrated
offline); nothing is evaluated, re-verified, or promoted.

Steps (mirrors tools/run_phase32_fresh_recovery.py post-loop byte-for-byte):
  1. Sheets preflight (read-only) + pre snapshot.
  2. Rebuild deterministic funnel from dataset TslrrxacueKrImAFF (classifier
     only — no LLM) and index classified posts by URL.
  3. For each ACCEPT-with-gate verdict: enriched_post + route_production_post
     (unchanged gate). Gate-held ACCEPTs recorded, never written.
  4. Eligible pairs -> Enricher -> guarded SheetsWriter.write_production.
  5. Independent post-write verification (verify_post_write).

No gate/taxonomy/verifier/retry/TLS/.env/query changes. No commits.

Usage: python tools/phase32_gate_write_pass.py
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

# In-process production override (run_daily.py pattern) — before app.config.
os.environ["DRY_RUN"] = "false"

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace",
                           line_buffering=True)
    sys.stderr.reconfigure(encoding="utf-8", errors="replace",
                           line_buffering=True)
except Exception:
    pass

from app.classifier import DeterministicClassifier  # noqa: E402
from app.config import (  # noqa: E402
    GOOGLE_SHEET_DATA_WORKSHEET, GOOGLE_SHEET_WORKSHEET,
    GOOGLE_SHEETS_CREDENTIALS, GOOGLE_SHEETS_ID,
)
from app.enrichment import Enricher  # noqa: E402
from app.llm.schemas import GateResult  # noqa: E402
from app.llm.verifier import enriched_post  # noqa: E402
from app.production_gate import route_production_post  # noqa: E402
from app.sheets_writer import SheetsWriter  # noqa: E402
from app.sources import datadoping_source as dds  # noqa: E402
from tools.run_phase29_tranche import sheets_preflight  # noqa: E402
from tools.run_phase32_production import (  # noqa: E402
    snapshot_tabs, verify_post_write,
)

DATASET_ID = "TslrrxacueKrImAFF"
OUT_DIR = ROOT / "data" / "validation" / "phase32_final_production_run"
VERDICTS = OUT_DIR / "verdicts.jsonl"


def _log(msg: str) -> None:
    print(msg, flush=True)


def main() -> int:
    utc = datetime.now(timezone.utc).isoformat()
    _log(f"=== PHASE 32.1 GATE/WRITE PASS @ {utc} (0 LLM calls) ===")

    w = SheetsWriter(GOOGLE_SHEETS_CREDENTIALS, GOOGLE_SHEETS_ID,
                     GOOGLE_SHEET_WORKSHEET, dry_run=False,
                     data_worksheet_name=GOOGLE_SHEET_DATA_WORKSHEET)
    if w.client is None:
        _log("ABORT: Sheets auth failed")
        return 1
    sheet = w.client.open_by_key(GOOGLE_SHEETS_ID)
    try:
        preflight = sheets_preflight(w, sheet)
    except Exception as e:
        _log(f"ABORT: Sheets preflight failed: {e}")
        return 1
    _log(f"preflight OK: {json.dumps(preflight)}")
    pre = snapshot_tabs(w, sheet)

    # Deterministic rebuild (classifier only) -> classified by URL.
    from apify_client import ApifyClient
    from app.config import APIFY_API_TOKEN
    dataset = ApifyClient(APIFY_API_TOKEN).dataset(DATASET_ID)
    items = list(dataset.iterate_items())
    mapper = dds.DataDopingSource("offline-replay-no-apify-call")
    raw_posts = []
    for item in items:
        if item.get("error"):
            continue
        post = mapper._to_raw_post(item)
        if post is not None:
            raw_posts.append(post)
    seen: set = set()
    unique_posts = []
    for p in raw_posts:
        if p.post_url not in seen:
            seen.add(p.post_url)
            unique_posts.append(p)
    classifier = DeterministicClassifier()
    by_url = {}
    for p in unique_posts:
        try:
            c = classifier.classify(p)
        except Exception:
            continue
        if c.is_valid:
            by_url[p.post_url] = c
    _log(f"classified index: {len(by_url)} candidates")

    verdicts = [json.loads(l) for l in
                VERDICTS.read_text(encoding="utf-8").splitlines() if l.strip()]
    gated = [r for r in verdicts
             if r.get("decision") == "ACCEPT" and "gate_json" in r]
    _log(f"ACCEPT-with-gate verdicts: {len(gated)}")

    accepted_triples, gate_blocked = [], []
    for rec in gated:
        c = by_url.get(rec["post_url"])
        if c is None:
            _log(f"SKIP (not a deterministic candidate): {rec['post_url'][:70]}")
            continue
        gate = GateResult.model_validate(rec["gate_json"])
        route = route_production_post(enriched_post(c, gate), gate)
        if route.destination in ("MAIN_PRODUCTION", "DATA_PRODUCTION"):
            accepted_triples.append((enriched_post(c, gate), gate, c))
            _log(f"ELIGIBLE -> {route.destination}: {rec['post_url'][:70]}")
        else:
            gate_blocked.append({"source_url": rec["post_url"],
                                 "destination": route.destination,
                                 "reasons": list(route.reasons)})
            _log(f"GATE-HELD ({route.destination}): {rec['post_url'][:70]} :: "
                 f"{'; '.join(route.reasons)[:200]}")
    accepted_pairs = [(m, g) for m, g, _c in accepted_triples]

    enricher = Enricher(contact_provider=None)
    enrichment = {}
    for merged, gate, c in accepted_triples:
        try:
            e = enricher.enrich_from_classified(
                merged, gate.verdict,
                job_card_company=c.job_card_company,
                job_card_employment_type=c.job_card_employment_type,
                job_card_experience=c.job_card_experience)
            enrichment[merged.source_link] = e
        except Exception as ex:
            _log(f"enrichment error {merged.source_link}: {ex}")

    write_summary = {"outcome": "SKIPPED", "main": 0, "data": 0, "error": ""}
    if accepted_pairs:
        try:
            result = w.write_production(accepted_pairs)
            write_summary = {
                "outcome": getattr(result, "outcome", "UNKNOWN"),
                "main": int((getattr(result, "destinations", None) or
                             {}).get(GOOGLE_SHEET_WORKSHEET, 0)),
                "data": int((getattr(result, "destinations", None) or
                             {}).get(GOOGLE_SHEET_DATA_WORKSHEET, 0)),
                "error": getattr(result, "error", "") or "",
            }
        except Exception as e:
            write_summary = {"outcome": "FAILED", "main": 0, "data": 0,
                             "error": str(e)[:300]}
        _log(f"write: {json.dumps(write_summary)}")
    else:
        _log("no eligible pairs; write skipped (0 writes is correct)")

    post = snapshot_tabs(w, sheet)
    verification = verify_post_write(pre, post, write_summary["main"],
                                     write_summary["data"])
    if verification["failures"]:
        _log("VERIFICATION FAILED:\n  " +
             "\n  ".join(verification["failures"][:10]))
        return 2
    _log("verification: ALL CHECKS PASS "
         f"(MAIN {verification['final_main']}, DATA {verification['final_data']})")
    _log(json.dumps({
        "eligible": len(accepted_pairs), "gate_blocked": len(gate_blocked),
        "write": write_summary,
        "final_main": verification["final_main"],
        "final_data": verification["final_data"],
        "final_review": verification["final_review"],
        "cross_tab_duplicates": verification["cross_tab_duplicates"],
        "enriched": len(enrichment),
    }, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
