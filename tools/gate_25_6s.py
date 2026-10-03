"""
Phase 25.6S/T — gate + routing evaluation of the newly evaluated candidates
(offline; NO provider calls, NO Sheet writes in this step).

Usage: python tools/gate_25_6s.py [25.6s | 25.6t]

Runs the UNCHANGED production gate + two-tab routing on each newly evaluated
cache entry of the selected window and produces replay_25_6x_eligible_pairs.json
in the exact shape cmd_write() of tools/replay_25_6d.py consumes.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from app.classifier import DeterministicClassifier  # noqa: E402
from app.enrichment import Enricher  # noqa: E402
from app.llm.schemas import GateResult, LeadVerdict, LLM_NON_EVALUATED  # noqa: E402
from app.llm.verifier import enriched_post  # noqa: E402
from app.production_gate import (  # noqa: E402
    DATA_PRODUCTION, MAIN_PRODUCTION, route_production_post,
)
from app.sources import datadoping_source as dds  # noqa: E402

RUN_DIR = ROOT / "data" / "validation" / "phase25_6_live_run"
RAW_JSON = RUN_DIR / "raw_dataset.json"
CACHE_JSON = RUN_DIR / "llm_cache_25_6d.json"

# Newly evaluated cache keys per phase window.
NEW_KEYS_BY_PHASE = {
    "25.6s": ["7506318496969089024"],
    "25.6t": ["7506257052546281472"],
    "25.6v": ["https://www.linkedin.com/feed/update/urn:li:groupPost:9354683-7506326785429516289"],
}
PHASE = sys.argv[1] if len(sys.argv) > 1 and sys.argv[1] in NEW_KEYS_BY_PHASE else "25.6t"
NEW_KEYS = NEW_KEYS_BY_PHASE[PHASE]
PAIRS_JSON = RUN_DIR / f"replay_{PHASE.replace('.', '_')}_eligible_pairs.json"


def main() -> int:
    cache = json.loads(CACHE_JSON.read_text(encoding="utf-8"))
    items = json.loads(RAW_JSON.read_text(encoding="utf-8"))
    mapper = dds.DataDopingSource("offline-replay-no-apify-call")
    clf = DeterministicClassifier()

    raw_by_key = {}
    for it in items:
        if not isinstance(it, dict) or it.get("error"):
            continue
        p = mapper._to_raw_post(it)
        if p is not None:
            raw_by_key[dds.activity_id(p.post_url) or p.post_url] = p

    pairs = []
    for key in NEW_KEYS:
        entry = cache.get(key)
        assert entry, f"newly evaluated candidate {key} missing from cache"
        if entry.get("llm_status") in LLM_NON_EVALUATED:
            print(f"[{key}] not evaluated ({entry.get('llm_status')}) — nothing to gate")
            continue
        raw = raw_by_key.get(key)
        assert raw is not None, f"raw post for {key} not found"
        classified = clf.classify(raw)
        assert classified.is_valid, "deterministic classification no longer passes"

        d = entry
        gate = GateResult(
            verdict=LeadVerdict(**d["verdict"]),
            decision=d["decision"], status=d["status"], reasons=d["reasons"],
            llm_called=True, llm_status=d["llm_status"],
            llm_error=d.get("llm_error", ""), llm_provider=d.get("llm_provider", ""),
        )
        merged = enriched_post(classified, gate)

        enricher = Enricher(contact_provider=None)  # offline, fail-closed
        enriched = False
        try:
            enricher.enrich_from_classified(
                merged, gate.verdict,
                job_card_company=classified.job_card_company,
                job_card_employment_type=classified.job_card_employment_type,
                job_card_experience=classified.job_card_experience,
            )
            enriched = True
        except Exception as e:
            print(f"[enrich-error] {merged.source_link}: {e}")

        route = route_production_post(merged, gate)
        _disp = getattr(route.gate_result, "disposition", None) if route.gate_result else None
        print(f"[{key}] destination={route.destination} disposition={_disp}")
        print(f"[{key}] reasons={list(route.reasons or [])}")
        print(f"[{key}] §15 SOURCE-QUALITY: company={merged.company_name!r} "
              f"hm={merged.hiring_manager_name!r} "
              f"hm_evidence={bool(merged.hiring_manager_evidence)} "
              f"apply_link={bool(merged.apply_link)}")

        if route.destination not in (MAIN_PRODUCTION, DATA_PRODUCTION):
            continue

        bucket = "MAIN" if route.destination == MAIN_PRODUCTION else "DATA"
        pairs.append({
            "bucket": bucket,
            "post": {
                "post_url": classified.post_url, "post_date": classified.post_date,
                "text": classified.text, "author_name": classified.author_name,
                "author_profile_url": classified.author_profile_url,
                "company": classified.company,
                "job_card_location": classified.job_card_location,
                "job_card_company": classified.job_card_company,
                "job_card_employment_type": classified.job_card_employment_type,
                "job_card_experience": classified.job_card_experience,
            },
            "gate": {
                "decision": gate.decision, "status": gate.status,
                "reasons": gate.reasons, "llm_status": gate.llm_status,
                "llm_error": gate.llm_error, "llm_provider": gate.llm_provider,
                "verdict": gate.verdict.model_dump(),
            },
            "query": "",
        })

    PAIRS_JSON.write_text(json.dumps(pairs, ensure_ascii=False, indent=1),
                          encoding="utf-8")
    if pairs:
        print(f"[pairs] {len(pairs)} eligible pair(s) persisted -> {PAIRS_JSON.name}")
    else:
        print(f"[pairs] 0 production-eligible — empty pairs file persisted "
              f"({PAIRS_JSON.name}); NO write authorized")
    return 0


if __name__ == "__main__":
    sys.exit(main())
