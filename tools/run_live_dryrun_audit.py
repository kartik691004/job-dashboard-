"""
tools/run_live_dryrun_audit.py — Phase 9 CONTROLLED LIVE validation (DRY_RUN=true).

Invokes the production Orchestrator with a SMALL Apify limit, captures every
ACCEPT / REVIEW lead with its full field set + enrichment, and writes a quality
audit JSON under data/validation/. Respects DRY_RUN=true from .env (no Sheet
writes), CONTACT_PROVIDER=null (offline enrichment only). Read-only: it does not
modify any acceptance/rejection logic.
"""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.orchestrator import Orchestrator, _enrichment_row


def dumps_clean(obj):
    return json.dumps(obj, default=str, ensure_ascii=False)


def main():
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else 1
    print(f"RUNNING production pipeline with limit={limit} (DRY_RUN enforced by .env)")
    orch = Orchestrator()
    summary = orch.run_pipeline(limit=limit)

    accepted = orch._accepted_details
    review = orch._review_details
    rejected = orch._rejected_samples
    enrichments = orch._enrichment_results

    report = {
        "run_time": datetime.now(timezone.utc).isoformat(),
        "limit": limit,
        "summary": summary,
        "accepted": accepted,
        "review": review,
        "rejected_samples": rejected,
        "enrichments": {k: _enrichment_row(v) for k, v in enrichments.items()},
    }
    out_dir = Path("data/validation")
    out_dir.mkdir(exist_ok=True)
    out_path = out_dir / f"phase9_live_dryrun_{datetime.now().strftime('%Y%m%dT%H%M%SZ')}.json"
    out_path.write_text(dumps_clean(report), encoding="utf-8")
    print(f"\nWROTE AUDIT -> {out_path}")
    print("SUMMARY:", dumps_clean(summary))
    return report


if __name__ == "__main__":
    main()
