"""
tools/capture_deterministic_audit.py — Phase 9 read-only capture for the quality
audit. Reuses the PRODUCTION DataDopingSource + DeterministicClassifier exactly
(no logic changes), but retains every unique raw post and its deterministic
verdict + reason, so the audit can evaluate A-J across ALL leads (not just the
handful that reach the (currently Groq-blocked) LLM gate).

DRY_RUN side effect free: it never touches Sheets, never enables web-search, and
does not modify any acceptance/rejection logic. One controlled Apify run.
"""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.classifier import DeterministicClassifier
from app.config import APIFY_API_TOKEN, SEARCH_QUERIES
from app.sources.datadoping_source import DataDopingSource


def main():
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else 1
    src = DataDopingSource(APIFY_API_TOKEN)
    raw = src.search_all(SEARCH_QUERIES, max_posts=limit)
    cl = DeterministicClassifier()

    unique = []
    seen = set()
    for p in raw:
        if p.post_url not in seen:
            seen.add(p.post_url)
            unique.append(p)

    records = []
    for p in unique:
        try:
            c = cl.classify(p)
            rec = {
                "post_url": p.post_url,
                "post_date": p.post_date,
                "author_name": p.author_name,
                "author_profile_url": p.author_profile_url,
                "job_card_company": p.company,
                "job_card_location": p.job_card_location,
                "text": p.text,
                "is_valid": c.is_valid,
                "company": c.company_name,
                "major_category": c.major_category,
                "exact_role": c.exact_role,
                "ctc": c.ctc,
                "location": c.location,
                "experience": c.experience_requirement,
                "employment_type": c.employment_type,
                "india_relevance": c.india_relevance,
                "cold_email": c.cold_email,
                "hiring_manager_name": c.hiring_manager_name,
                "hiring_manager_linkedin": c.hiring_manager_linkedin,
                "confidence": c.confidence,
                "reason": c.classification_reason,
                "matched_role_keywords": c.matched_role_keywords,
                "hiring_intent_signals": c.hiring_intent_signals,
            }
        except Exception as e:
            rec = {"post_url": p.post_url, "text": p.text, "error": str(e)}
        records.append(rec)

    out_dir = Path("data/validation")
    out_dir.mkdir(exist_ok=True)
    out_path = out_dir / f"phase9_deterministic_{datetime.now().strftime('%Y%m%dT%H%M%SZ')}.json"
    out_path.write_text(json.dumps({
        "run_time": datetime.now(timezone.utc).isoformat(),
        "limit": limit,
        "scraped": len(raw),
        "unique": len(unique),
        "records": records,
    }, default=str, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"scraped={len(raw)} unique={len(unique)} -> {out_path}")


if __name__ == "__main__":
    main()
