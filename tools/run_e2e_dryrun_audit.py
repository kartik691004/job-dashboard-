"""
tools/run_e2e_dryrun_audit.py — Controlled production-readiness end-to-end dry run
using the validated Groq Structured Outputs transport (openai/gpt-oss-120b).

PURPOSE (Phase-9 allowed run):
  - DRY_RUN=True (from .env) — no real writes anywhere.
  - Google Sheets COMPLETELY DISABLED: this harness deliberately does NOT import
    or instantiate SheetsWriter, so there is zero Google connectivity (no auth,
    no reads, no writes). The orchestrator's SheetsWriter is intentionally
    bypassed by reproducing its exact production loop here.
  - Uses the EXISTING production Apify source (datadoping/...), the EXISTING
    DeterministicClassifier and all Phase 8/9A gates UNCHANGED, the now-validated
    Groq openai/gpt-oss-120b Structured Outputs transport, and CONTACT_PROVIDER
    =null (Enricher(contact_provider=None) => fully offline, no-fabrication).
  - Does NOT modify classifier, gates, schemas, prompts, enrichment, or thresholds.

OUTPUT: per-candidate audit artifact + aggregate metrics, written under
data/validation/ as JSON (one row per candidate) for the manual 100% inspection
of every ACCEPT and REVIEW record.
"""
import json
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import (
    APIFY_API_TOKEN, SEARCH_QUERIES, CONTACT_PROVIDER,
)
from app.classifier import DeterministicClassifier
from app.enrichment import Enricher
from app.sources.datadoping_source import DataDopingSource
from app.llm.verifier import Verifier, enriched_post


def dumps_clean(obj):
    return json.dumps(obj, default=str, ensure_ascii=False)


def gate_record(classified, gate) -> dict:
    """Capture the complete per-candidate audit record."""
    v = gate.verdict
    return {
        "post_url": classified.post_url,
        "author_name": classified.author_name,
        "author_profile_url": classified.author_profile_url,
        "job_card_location": getattr(classified, "job_card_location", ""),
        # Deterministic classifier output
        "deterministic_valid": classified.is_valid,
        "deterministic_company": classified.company_name,
        "deterministic_major_category": classified.major_category,
        "deterministic_exact_role": classified.exact_role,
        "deterministic_ctc": classified.ctc,
        "deterministic_location": classified.location,
        "deterministic_employment_type": classified.employment_type,
        "deterministic_india_relevance": classified.india_relevance,
        "deterministic_hiring_manager": classified.hiring_manager_name,
        "deterministic_cold_email": classified.cold_email,
        "deterministic_confidence": classified.confidence,
        "deterministic_reason": classified.classification_reason,
        "deterministic_matched_role": classified.matched_role_keywords,
        "deterministic_hiring_intent_signal": classified.hiring_intent_signals,
        "deterministic_india_evidence": classified.india_evidence,
        # LLM gate output (Groq Structured Outputs transport)
        "llm_called": gate.llm_called,
        "llm_decision": getattr(v, "decision", ""),
        "llm_reason": getattr(v, "reason", ""),
        "llm_confidence": getattr(v, "confidence", 0.0),
        "llm_actual_role": getattr(v, "actual_role", ""),
        "llm_exact_role": getattr(v, "exact_role", ""),
        "llm_company": getattr(v, "company", ""),
        "llm_location": getattr(v, "location", ""),
        "llm_employment_type": getattr(v, "employment_type", ""),
        "llm_india_relevance": getattr(v, "india_relevance", ""),
        "llm_hiring_manager_name": getattr(v, "hiring_manager_name", ""),
        "llm_cold_email": getattr(v, "cold_email", ""),
        "llm_is_aggregator": getattr(v, "is_aggregator", None),
        "llm_has_negation": getattr(v, "has_negation", None),
        "llm_experience_requirement_only": getattr(v, "is_experience_requirement_only", None),
        # Final decision (hard gates applied — authoritative)
        "final_decision": gate.decision,
        "final_status": gate.status,
        "final_reasons": gate.reasons,
        # Evidence snippets (LLM per-field verbatim quotes)
        "evidence": list(getattr(v, "evidence", []) or []),
        "evidence_vacancy": getattr(v, "evidence_vacancy", ""),
        "evidence_role": getattr(v, "evidence_role", ""),
        "evidence_company": getattr(v, "evidence_company", ""),
        "evidence_location": getattr(v, "evidence_location", ""),
        "evidence_employment": getattr(v, "evidence_employment", ""),
        "evidence_experience": getattr(v, "evidence_experience", ""),
        "evidence_ctc": getattr(v, "evidence_ctc", ""),
        "evidence_hiring_manager": getattr(v, "evidence_hiring_manager", ""),
        "evidence_email": getattr(v, "evidence_email", ""),
        # Full post text (for manual inspection)
        "post_text": classified.text,
    }


def enrichment_record(enriched) -> dict:
    if enriched is None:
        return {"present": False}
    return {
        "present": True,
        "enrichment_status": getattr(enriched, "enrichment_status", ""),
        "enrichment_confidence": getattr(enriched, "enrichment_confidence", 0.0),
        "data_quality_score": getattr(enriched, "data_quality_score", 0.0),
        "company": getattr(enriched, "company_name", ""),
        "company_confidence": getattr(enriched, "company_confidence", 0.0),
        "company_evidence": getattr(enriched, "company_evidence", ""),
        "company_domain": getattr(enriched, "company_domain", "Not Available"),
        "exact_role": getattr(enriched, "exact_role", ""),
        "role_confidence": getattr(enriched, "role_confidence", 0.0),
        "location": getattr(enriched, "location", ""),
        "employment_type": getattr(enriched, "employment_type", ""),
        "experience_required": getattr(enriched, "experience_required", ""),
        "ctc": getattr(enriched, "ctc", ""),
        "hiring_manager_name": getattr(enriched, "hiring_manager_name", ""),
        "hiring_manager_linkedin": getattr(enriched, "hiring_manager_linkedin", ""),
        "cold_email": getattr(enriched, "cold_email", ""),
        "email_status": getattr(enriched, "email_status", ""),
        "email_source": getattr(enriched, "email_source", ""),
        "contact_discovery_status": getattr(enriched, "contact_discovery_status", ""),
        "evidence_snippets": list(getattr(enriched, "evidence_snippets", []) or []),
        "remote_is_india": getattr(enriched, "remote_is_india", False),
    }


def main():
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else 10
    print(f"CONTROLLED E2E DRY RUN — limit={limit} (DRY_RUN from .env)")
    print(f"  CONTACT_PROVIDER={CONTACT_PROVIDER} (must be 'null')")

    if CONTACT_PROVIDER and CONTACT_PROVIDER != "null":
        print("ABORT: CONTACT_PROVIDER is not 'null'. Refusing to run web enrichment.")
        return

    source = DataDopingSource(APIFY_API_TOKEN)
    classifier = DeterministicClassifier()
    verifier = Verifier()
    # contact_provider=None => fully offline, no-fabrication, no web lookups.
    enricher = Enricher(contact_provider=None)

    errors = 0
    deterministic_candidates = 0
    deterministic_rejects = 0
    groq_calls = 0
    accepted = 0
    review = 0
    reject = 0
    records = []
    enriched_detail = []

    est = len(SEARCH_QUERIES) * max(limit, 10)
    print(f"Scraping {len(SEARCH_QUERIES)} keywords in one actor run "
          f"(max {max(limit,10)}/keyword, ceiling {est} posts)")

    try:
        raw_posts = source.search_all(SEARCH_QUERIES, max_posts=limit)
    except Exception as e:
        print(f"SCRAPE ERROR: {e}\n{traceback.format_exc()}")
        return {"scraped": 0, "errors": 1}
    if source.last_errors:
        errors += len(source.last_errors)
        print(f"Actor reported {len(source.last_errors)} unusable records; "
              f"first: {source.last_errors[0]}")

    # Level-1 dedup (current run) — same as orchestrator.
    unique_raw_posts = []
    seen_urls = set()
    for p in raw_posts:
        if p.post_url not in seen_urls:
            seen_urls.add(p.post_url)
            unique_raw_posts.append(p)
    current_run_duplicates = len(raw_posts) - len(unique_raw_posts)

    for p in unique_raw_posts:
        try:
            classified = classifier.classify(p)
        except Exception as e:
            print(f"Classifier error on {p.post_url}: {e}")
            errors += 1
            records.append({
                "post_url": p.post_url, "post_text": p.text,
                "processing_error": f"classifier: {e}",
            })
            continue

        if not classified.is_valid:
            deterministic_rejects += 1
            records.append({
                "post_url": p.post_url,
                "author_name": classified.author_name,
                "post_text": classified.text,
                "deterministic_valid": False,
                "deterministic_company": classified.company_name,
                "deterministic_major_category": classified.major_category,
                "deterministic_india_relevance": classified.india_relevance,
                "deterministic_location": classified.location,
                "deterministic_reason": classified.classification_reason,
                "final_decision": "DETERMINISTIC_REJECT",
                "final_status": "",
                "evidence": [],
            })
            continue

        deterministic_candidates += 1

        # Deterministic-pass -> Groq semantic gate (only boundary where LLM cost accrues).
        try:
            gate = verifier.verify(
                post_text=classified.text,
                post_url=classified.post_url,
                author_name=classified.author_name,
                author_profile_url=classified.author_profile_url,
                job_card_location=classified.job_card_location,
            )
        except Exception as e:
            print(f"Verifier crash on {p.post_url}: {e}")
            errors += 1
            records.append(gate_record(classified, None) | {
                "processing_error": f"verifier: {e}",
            })
            continue

        groq_calls = verifier.llm_calls
        rec = gate_record(classified, gate)

        if gate.decision == "REJECT":
            reject += 1
        else:
            merged = enriched_post(classified, gate)
            rec["merged_company"] = merged.company_name
            rec["merged_exact_role"] = merged.exact_role
            rec["merged_location"] = merged.location
            rec["merged_employment_type"] = merged.employment_type
            rec["merged_india_relevance"] = merged.india_relevance
            rec["merged_hiring_manager"] = merged.hiring_manager_name
            rec["merged_cold_email"] = merged.cold_email
            if gate.decision == "ACCEPT":
                accepted += 1
                try:
                    enriched = enricher.enrich_from_classified(merged, gate.verdict)
                except Exception as e:
                    print(f"Enrichment error on {merged.source_link}: {e}")
                    enriched = None
                rec["enrichment"] = enrichment_record(enriched)
                if enriched is not None:
                    enriched_detail.append({
                        "post_url": p.post_url,
                        "enrichment": enrichment_record(enriched),
                    })
            else:
                review += 1
                rec["enrichment"] = {"present": False, "note": "REVIEW not enriched (only ACCEPT is enriched)"}

        records.append(rec)

    # Aggregate metrics
    accepts = [r for r in records if r.get("final_decision") == "ACCEPT"]
    reviews = [r for r in records if r.get("final_decision") == "REVIEW"]
    rejects_det = [r for r in records if r.get("final_decision") == "DETERMINISTIC_REJECT"]
    rejects_llm = [r for r in records if r.get("final_decision") == "REJECT"]
    with_errors = [r for r in records if "processing_error" in r]

    def missing_rate(rows, key, notavl):
        n = sum(1 for r in rows if (r.get(key) or "").strip() in ("", notavl, "N/A", "Unclear", "Not Available"))
        return round(n / len(rows), 3) if rows else 0.0

    metrics = {
        "scraped": len(raw_posts),
        "unique": len(unique_raw_posts),
        "current_run_duplicates": current_run_duplicates,
        "deterministic_candidates": deterministic_candidates,
        "deterministic_rejects": len(rejects_det),
        "groq_calls": groq_calls,
        "accepted": accepted,
        "review": review,
        "reject": reject,
        "enrichment_statuses": {},
        "missing_email_rate_accept": missing_rate(accepts, "merged_cold_email", "Not Available"),
        "missing_company_rate_accept": missing_rate(accepts, "merged_company", "Unclear"),
        "missing_role_rate_accept": missing_rate(accepts, "merged_exact_role", "Unclear"),
        "errors": errors,
        "processing_errors": len(with_errors),
    }
    for st in ("READY", "READY_WITHOUT_CONTACT", "REVIEW"):
        metrics["enrichment_statuses"][st] = sum(
            1 for d in enriched_detail
            if d["enrichment"].get("enrichment_status") == st
        )
    # False-positive indicator flags (preliminary signals for manual audit)
    aggregation_leak = [r["post_url"] for r in accepts
                        if r.get("llm_is_aggregator")]
    non_india = [r["post_url"] for r in accepts
                 if (r.get("merged_india_relevance") or "").strip().lower() == "not india"]
    internship = [r["post_url"] for r in accepts
                  if (r.get("merged_employment_type") or "").strip().lower() == "internship"]
    metrics["fp_flags"] = {
        "accepts_with_aggregator_flag": len(aggregation_leak),
        "accepts_non_india": len(non_india),
        "accepts_internship": len(internship),
    }

    report = {
        "run_time": datetime.now(timezone.utc).isoformat(),
        "limit": limit,
        "config": {
            "DRY_RUN": True,
            "CONTACT_PROVIDER": CONTACT_PROVIDER,
            "GROQ_MODEL": "openai/gpt-oss-120b",
            "transport": "json_schema strict:false (LeadVerdict)",
            "sheets": "COMPLETELY DISABLED (no SheetsWriter, no auth, no writes)",
            "apify_source": "datadoping/linkedin-posts-search-scraper",
        },
        "metrics": metrics,
        "candidates": records,
    }

    out_dir = Path("data/validation")
    out_dir.mkdir(exist_ok=True)
    ts = datetime.now().strftime("%Y%m%dT%H%M%SZ")
    out_path = out_dir / f"e2e_dryrun_{ts}.json"
    out_path.write_text(dumps_clean(report), encoding="utf-8")

    print("\n===== AGGREGATE METRICS =====")
    for k, v in metrics.items():
        print(f"  {k}: {v}")
    print(f"\nWROTE AUDIT -> {out_path}")
    return report


if __name__ == "__main__":
    main()
