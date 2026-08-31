"""
tools/reprocess_historical_leads.py  (READ-ONLY)

Re-run the OLD Google Sheet leads (backup pre-migration) through the CURRENT
production pipeline — current DeterministicClassifier (incl. Phase 9E fixes)
+ current Groq Verifier (Structured Outputs) + fail-closed enrichment — and
produce a BEFORE -> AFTER comparison showing which old leads still survive.

Guarantees (matching the safety contract):
  * NO Google SheetsWriter — never instantiated, no auth, no writes.
  * NO Apify scrape — reads full post text already on disk only.
  * NO classifier / gate / prompt / config / enrichment changes.
  * CONTACT_PROVIDER=null -> Enricher(contact_provider=None) offline/fail-closed.
  * No fabrication: a lead with no on-disk full text is reported as
    NOT_REPROCESSABLE (never guessed, never fed a truncated snippet).
"""
import os
import sys
import json
from collections import Counter, defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app.classifier import DeterministicClassifier
from app.llm.verifier import Verifier, enriched_post
from app.enrichment import Enricher
from app.models import RawPost

BACKUP = os.path.join(ROOT, "data", "analysis", "jobdashboard_linkedin_leads_backup_pre_migration.json")
TEXT_SOURCES = [
    os.path.join(ROOT, "data", "runs", "2026-08-23T121618Z_leads.json"),
    os.path.join(ROOT, "data", "runs", "2026-08-23T121621Z_leads.json"),
    os.path.join(ROOT, "data", "dry_runs", "2026-08-23T100816Z_leads.json"),
    os.path.join(ROOT, "data", "dry_runs", "2026-08-23_classified_leads.json"),
    os.path.join(ROOT, "data", "analysis", "2026-08-23_geo_projection.json"),
]
OUT_DIR = os.path.join(ROOT, "data", "validation", "historical_before_after")


def _utcnow():
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()



def _iter_items(d):
    if isinstance(d, list):
        return d
    if isinstance(d, dict) and isinstance(d.get("rows"), list):
        return d["rows"]
    return []


def _get(d, *keys):
    for k in keys:
        v = d.get(k)
        if v:
            return v
    return ""


def load_full_text():
    """Index on-disk post URLs -> full text."""
    index = {}
    for path in TEXT_SOURCES:
        if not os.path.exists(path):
            continue
        try:
            data = json.load(open(path, encoding="utf-8"))
        except Exception as e:
            print(f"  !! could not read {path}: {e}")
            continue
        for it in _iter_items(data):
            if not isinstance(it, dict):
                continue
            url = (_get(it, "post_url", "url", "source_linkedin_url") or "").strip()
            text = _get(it, "text", "post_text", "original_text") or ""
            if url and text and url not in index:
                index[url] = {
                    "text": text,
                    "author_name": _get(it, "author_name", "authorName") or "",
                    "author_profile_url": _get(it, "author_profile_url", "authorProfileUrl") or "",
                    "post_date": _get(it, "post_date", "postedAtISO") or "",
                }
    return index


def load_backup():
    data = json.load(open(BACKUP, encoding="utf-8"))
    rows = data["rows"]
    header = rows[0]
    leads = []
    for r in rows[1:]:
        lead = {h: v for h, v in zip(header, r)}
        lead["_post_url"] = (lead.get("Post URL") or "").strip()
        leads.append(lead)
    return leads


def run():
    cls = DeterministicClassifier()
    verifier = Verifier()
    enricher = Enricher(contact_provider=None)

    text_index = load_full_text()
    leads = load_backup()

    print(f"Backup leads: {len(leads)}")
    print(f"On-disk full-text coverage: {len(text_index)} unique URLs")

    results = []
    accepts, reviews, rejects, not_reprocessable = [], [], [], []

    for lead in leads:
        url = lead["_post_url"]
        src = text_index.get(url)
        base = {
            "source_linkedin_url": url,
            "before": {
                "role_category": lead.get("Role Category", ""),
                "employment_type": lead.get("Employment Type", ""),
                "detected_role_title": lead.get("Detected Role Title", ""),
                "company": lead.get("Company", ""),
                "confidence": lead.get("Confidence", ""),
                "status": lead.get("Status", ""),
                "classification_reason": lead.get("Classification Reason", ""),
            },
        }
        if src is None:
            base["after"] = None
            base["outcome"] = "NOT_REPROCESSABLE"
            base["note"] = "No full post text on disk; would require a new scrape (disallowed)."
            not_reprocessable.append(base)
            results.append(base)
            continue

        rp = RawPost(
            post_url=url,
            post_date=src["post_date"],
            text=src["text"],
            author_name=src["author_name"] or lead.get("Author Name", ""),
            author_profile_url=src["author_profile_url"] or lead.get("Author Profile URL", ""),
            company=lead.get("Company", "Unclear") or "Unclear",
            job_card_location="",
        )

        try:
            c = cls.classify(rp)
        except Exception as e:
            base["outcome"] = "ERROR"
            base["error"] = repr(e)
            results.append(base)
            continue

        if not c.is_valid:
            base["outcome"] = "DETERMINISTIC_REJECT"
            base["after"] = {
                "status": "REJECT",
                "stage": "deterministic",
                "reason": c.classification_reason,
                "exact_role": c.exact_role,
                "major_category": c.major_category,
                "confidence": c.confidence,
            }
            rejects.append(base)
            results.append(base)
            continue

        base["deterministic"] = {
            "exact_role": c.exact_role,
            "major_category": c.major_category,
            "confidence": c.confidence,
            "reason": c.classification_reason,
        }

        try:
            gate = verifier.verify(
                post_text=c.text,
                post_url=c.post_url,
                author_name=c.author_name,
                author_profile_url=c.author_profile_url,
                job_card_location=c.job_card_location,
            )
        except Exception as e:
            base["outcome"] = "ERROR"
            base["error"] = repr(e)
            results.append(base)
            continue

        merged = enriched_post(c, gate)
        after = {
            "status": gate.decision,
            "stage": "llm",
            "reasons": gate.reasons,
            "verdict_reason": (gate.verdict.reason or ""),
            "company": merged.company_name,
            "major_category": merged.major_category,
            "exact_role": merged.exact_role,
            "ctc": merged.ctc,
            "location": merged.location,
            "employment_type": merged.employment_type,
            "india_relevance": merged.india_relevance,
            "confidence": merged.confidence,
            "hiring_manager_name": merged.hiring_manager_name,
            "hiring_manager_linkedin": merged.hiring_manager_linkedin,
            "cold_email": merged.cold_email,
            "description": (merged.description or src["text"])[:600],
        }

        if gate.decision == "ACCEPT":
            base["outcome"] = "ACCEPT"
            base["after"] = after
            try:
                enriched = enricher.enrich_from_classified(merged, gate.verdict)
            except Exception as e:
                enriched = None
                after["enrichment_note"] = repr(e)
            if enriched is not None:
                after["enrichment"] = {
                    "enrichment_status": getattr(enriched, "enrichment_status", ""),
                    "data_quality_score": getattr(enriched, "data_quality_score", 0.0),
                    "company_domain": getattr(enriched, "company_domain", "Not Available"),
                    "company_evidence": getattr(enriched, "company_evidence", ""),
                    "email_status": getattr(enriched, "email_status", ""),
                    "email_source": getattr(enriched, "email_source", ""),
                    "email_evidence_url": getattr(enriched, "email_evidence_url", ""),
                    "contact_discovery_status": getattr(enriched, "contact_discovery_status", ""),
                    "hiring_manager_confidence": getattr(enriched, "hiring_manager_confidence", 0.0),
                    "hiring_manager_evidence": getattr(enriched, "hiring_manager_evidence", ""),
                }
            accepts.append(base)
        elif gate.decision == "REVIEW":
            base["outcome"] = "REVIEW"
            base["after"] = after
            reviews.append(base)
        else:
            base["outcome"] = "REJECT"
            base["after"] = after
            rejects.append(base)

        results.append(base)

    outcome_counts = Counter(r["outcome"] for r in results)
    os.makedirs(OUT_DIR, exist_ok=True)

    doc = {
        "generated_at": _utcnow(),
        "summary": {
            "backup_leads": len(leads),
            "on_disk_text_coverage": len(text_index),
            "outcome_counts": dict(outcome_counts),
            "llm_calls": verifier.llm_calls,
        },
        "accepts": accepts,
        "reviews": reviews,
        "rejects": rejects,
        "not_reprocessable": not_reprocessable,
    }

    with open(os.path.join(OUT_DIR, "historical_before_after.json"), "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=2)

    print("\n=== HISTORICAL BEFORE/ AFTER AUDIT ===")
    print("Backup leads:", len(leads))
    print("Outcomes:", dict(outcome_counts))
    print("Groq (LLM) calls:", verifier.llm_calls)
    print("\nACCEPT leads:")
    for a in accepts:
        print("  -", a["source_linkedin_url"])
    print("\nREVIEW leads:")
    for r in reviews:
        print("  -", r["source_linkedin_url"])
    print("\nNOT_REPROCESSABLE:", len(not_reprocessable))
    print("Report written to:", os.path.join(OUT_DIR, "historical_before_after.json"))
    return doc


if __name__ == "__main__":
    run()
