#!/usr/bin/env python3
"""Replay today's full dataset with Groq provider."""
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.config import (
    LLM_PROVIDER, GROQ_API_KEY, GROQ_MODEL, GEMINI_API_KEY,
    GEMINI_MODEL, GROQ_TIMEOUT_S, GROQ_MAX_RETRIES,
)
from app.models import RawPost
from app.classifier import DeterministicClassifier
from app.llm.verifier import Verifier, enriched_post


def load_data():
    path = Path("data/validation/fresh_production_preview/fresh_preview.json")
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def create_raw_post(record):
    return RawPost(
        post_url=record["source_link"],
        post_date=record.get("date", ""),
        text=record.get("text", ""),
        author_name=record.get("author", ""),
        author_profile_url="",
        company=record.get("company_meta", "Unclear"),
        job_card_location=record.get("job_card_location", ""),
        job_card_company=record.get("job_card_company", "Unclear"),
        job_card_employment_type=record.get("job_card_employment_type", "Unclear"),
        job_card_experience=record.get("job_card_experience", "Unclear"),
    )


def main():
    print("=" * 70)
    print("PHASE 14A: FULL DATASET REPLAY WITH GROQ")
    print("=" * 70)
    
    # Load data
    data = load_data()
    all_records = data.get("records", [])
    stats = data.get("stats", {})
    
    print(f"\nTotal records to reprocess: {len(all_records)}")
    print(f"Original stats: ACCEPT={stats.get('final_ACCEPT')}, REVIEW={stats.get('final_REVIEW')}, REJECT={stats.get('final_REJECT')}")
    
    # Initialize
    classifier = DeterministicClassifier()
    verifier = Verifier()
    
    # Verify provider
    provider_name = type(verifier.provider).__name__ if verifier.provider else "NONE"
    print(f"Provider: {provider_name}, Model: {verifier.provider.model if verifier.provider else 'N/A'}")
    
    if provider_name != "GroqProvider":
        print("ERROR: Expected GroqProvider")
        return
    
    # Process all records
    classifier_results = []
    verifier_results = []
    
    for i, record in enumerate(all_records):
        p = create_raw_post(record)
        c = classifier.classify(p)
        
        classifier_results.append({
            "source_link": p.post_url,
            "is_valid": c.is_valid,
            "company": c.company_name,
            "exact_role": c.exact_role,
            "location": c.location,
            "employment_type": c.employment_type,
            "india_relevance": c.india_relevance,
            "confidence": c.confidence,
            "reason": c.classification_reason,
            "job_card_location": c.job_card_location,
        })
        
        if not c.is_valid:
            verifier_results.append({
                "source_link": p.post_url,
                "llm_called": False,
                "decision": "EXCLUDED",
                "reasons": [c.classification_reason],
            })
            continue
        
        if i % 5 == 0:
            print(f"  Processing {i+1}/{len(all_records)}...")
        
        gate = verifier.verify(
            post_text=c.text,
            post_url=c.post_url,
            author_name=c.author_name,
            author_profile_url=c.author_profile_url,
            job_card_location=c.job_card_location,
            job_card_company=c.job_card_company,
            job_card_employment_type=c.job_card_employment_type,
            job_card_experience=c.job_card_experience,
        )
        
        merged = enriched_post(c, gate)
        
        verifier_results.append({
            "source_link": p.post_url,
            "llm_called": gate.llm_called,
            "decision": gate.decision,
            "status": gate.status,
            "confidence": gate.confidence,
            "reasons": gate.reasons,
            "llm_company": gate.verdict.company,
            "llm_exact_role": gate.verdict.exact_role,
            "llm_location": gate.verdict.location,
            "llm_employment": gate.verdict.employment_type,
            "llm_ctc": gate.verdict.ctc,
            "llm_cold_email": gate.verdict.cold_email,
            "llm_hiring_mgr": gate.verdict.hiring_manager_name,
            "llm_hiring_mgr_linkedin": gate.verdict.hiring_manager_linkedin,
            "llm_actual_role": gate.verdict.actual_role,
            "llm_major_category": gate.verdict.major_category,
            "llm_india_relevance": gate.verdict.india_relevance,
            "merged_company": merged.company_name,
            "merged_role": merged.exact_role,
            "merged_location": merged.location,
            "merged_employment": merged.employment_type,
            "merged_confidence": merged.confidence,
            "merged_status": merged.status,
        })
    
    # Count results
    new_accept = sum(1 for r in verifier_results if r.get("decision") == "ACCEPT")
    new_review = sum(1 for r in verifier_results if r.get("decision") == "REVIEW")
    new_reject = sum(1 for r in verifier_results if r.get("decision") == "REJECT")
    new_excluded = sum(1 for r in verifier_results if r.get("decision") == "EXCLUDED")
    deterministic_candidates = sum(1 for r in classifier_results if r['is_valid'])
    llm_calls = sum(1 for r in verifier_results if r.get('llm_called'))
    
    print(f"\nNew Pipeline Counts:")
    print(f"  Deterministic candidates: {deterministic_candidates}")
    print(f"  LLM calls: {llm_calls}")
    print(f"  ACCEPT: {new_accept}")
    print(f"  REVIEW: {new_review}")
    print(f"  REJECT: {new_reject}")
    print(f"  Deterministic EXCLUDED: {new_excluded}")
    
    # Compare with original
    print(f"\nOld vs New Comparison:")
    print(f"| Metric | Old (Groq) | New (Groq) | Delta |")
    print(f"|--------|-----|-----|-------|")
    print(f"| ACCEPT | {stats.get('final_ACCEPT', 0)} | {new_accept} | {new_accept - stats.get('final_ACCEPT', 0):+d} |")
    print(f"| REVIEW | {stats.get('final_REVIEW', 0)} | {new_review} | {new_review - stats.get('final_REVIEW', 0):+d} |")
    print(f"| REJECT | {stats.get('final_REJECT', 0)} | {new_reject} | {new_reject - stats.get('final_REJECT', 0):+d} |")
    print(f"| Deterministic candidates | {stats.get('sent_to_groq', 0)} | {deterministic_candidates} | {deterministic_candidates - stats.get('sent_to_groq', 0):+d} |")
    print(f"| LLM calls | {stats.get('groq_calls', 0)} | {llm_calls} | {llm_calls - stats.get('groq_calls', 0):+d} |")
    
    # Field recovery comparison
    print("\nField Recovery Analysis:")
    
    # Build lookup from old data
    old_by_link = {}
    for item in all_records:
        old_by_link[item["source_link"]] = item
    
    new_by_link = {r["source_link"]: r for r in verifier_results}
    
    metrics = {
        "company_recovered": 0,  # Was Unclear, now has value
        "company_lost": 0,       # Had value, now Unclear
        "role_recovered": 0,
        "role_lost": 0,
        "location_recovered": 0,
        "location_lost": 0,
        "hm_recovered": 0,
        "hm_lost": 0,
        "email_recovered": 0,
        "email_lost": 0,
    }
    
    for link, old in old_by_link.items():
        new = new_by_link.get(link)
        if not new:
            continue
        
        # Company
        old_c = old.get("company_meta", "") or old.get("llm_company", "")
        new_c = new.get("merged_company", "")
        if old_c in ["Unclear", "N/A", ""] and new_c not in ["Unclear", "N/A", ""]:
            metrics["company_recovered"] += 1
        elif old_c not in ["Unclear", "N/A", ""] and new_c in ["Unclear", "N/A", ""]:
            metrics["company_lost"] += 1
            
        # Role
        old_r = old.get("classifier_role", "") or old.get("llm_exact_role", "")
        new_r = new.get("merged_role", "")
        if old_r in ["Unclear", "N/A", ""] and new_r not in ["Unclear", "N/A", ""]:
            metrics["role_recovered"] += 1
        elif old_r not in ["Unclear", "N/A", ""] and new_r in ["Unclear", "N/A", ""]:
            metrics["role_lost"] += 1
            
        # Location
        old_l = old.get("classifier_location", "") or old.get("llm_location", "")
        new_l = new.get("merged_location", "")
        if old_l in ["Unclear", "N/A", "Not Specified", ""] and new_l not in ["Unclear", "N/A", "Not Specified", ""]:
            metrics["location_recovered"] += 1
        elif old_l not in ["Unclear", "N/A", "Not Specified", ""] and new_l in ["Unclear", "N/A", "Not Specified", ""]:
            metrics["location_lost"] += 1
            
        # Hiring Manager
        old_h = old.get("llm_hiring_mgr", "") or old.get("merged_hiring_mgr", "")
        new_h = new.get("merged_hiring_mgr", "")
        if old_h in ["Unclear", "N/A", ""] and new_h not in ["Unclear", "N/A", ""]:
            metrics["hm_recovered"] += 1
        elif old_h not in ["Unclear", "N/A", ""] and new_h in ["Unclear", "N/A", ""]:
            metrics["hm_lost"] += 1
            
        # Email
        old_e = old.get("llm_email", "") or old.get("merged_cold_email", "")
        new_e = new.get("merged_cold_email", "")
        if old_e in ["Not Available", "N/A", ""] and new_e not in ["Not Available", "N/A", ""]:
            metrics["email_recovered"] += 1
        elif old_e not in ["Not Available", "N/A", ""] and new_e in ["Not Available", "N/A", ""]:
            metrics["email_lost"] += 1
    
    for k, v in metrics.items():
        print(f"  {k}: {v}")
    
    # Check if any Gemini calls occurred
    gemini_mentioned = any("gemini" in str(r.get("reasons", [])).lower() for r in verifier_results)
    print(f"\nGemini contacted during run: {gemini_mentioned}")
    
    # Save detailed results for report
    output = {
        "configuration": {
            "LLM_PROVIDER": LLM_PROVIDER,
            "GROQ_MODEL": GROQ_MODEL,
            "provider_instantiated": provider_name,
        },
        "smoke_test_passed": True,
        "replay": {
            "total_records": len(all_records),
            "deterministic_candidates": deterministic_candidates,
            "llm_calls": llm_calls,
            "accept": new_accept,
            "review": new_review,
            "reject": new_reject,
            "excluded": new_excluded,
        },
        "comparison": {
            "accept_delta": new_accept - stats.get('final_ACCEPT', 0),
            "review_delta": new_review - stats.get('final_REVIEW', 0),
            "reject_delta": new_reject - stats.get('final_REJECT', 0),
            "field_recovery": metrics,
        },
        "gemini_contacted": gemini_mentioned,
        "detailed_results": verifier_results,
    }
    
    output_path = Path("data/validation/phase13b_extraction_rootcause/phase14a_replay_results.json")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)
    
    print(f"\nResults saved to: {output_path}")
    
    return output


if __name__ == "__main__":
    main()