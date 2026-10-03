#!/usr/bin/env python3
"""
Diagnostic test: Re-run today's pipeline data with new API configuration.
Tests whether previous quality problems were caused by API/provider config or code.
"""
import json
import os
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.config import (
    LLM_PROVIDER, GROQ_API_KEY, GROQ_MODEL, GROQ_TIMEOUT_S, GROQ_MAX_RETRIES,
    GEMINI_API_KEY, GEMINI_MODEL, CONFIDENCE_THRESHOLD,
)
from app.models import RawPost
from app.classifier import DeterministicClassifier
from app.llm.verifier import Verifier, enriched_post


def load_today_data():
    """Load today's pipeline data from fresh_preview.json (has full text)"""
    path = Path("data/validation/fresh_production_preview/fresh_preview.json")
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def verify_llm_config():
    """Verify and print LLM configuration (no secrets)"""
    print("=" * 70)
    print("STEP 2: LLM CONFIGURATION VERIFICATION")
    print("=" * 70)
    
    # Check environment
    print(f"LLM_PROVIDER (env): {os.getenv('LLM_PROVIDER', 'NOT SET -> defaults to auto')}")
    print(f"GROQ_API_KEY: {'SET (hidden)' if GROQ_API_KEY else 'NOT SET'}")
    print(f"GROQ_MODEL: {GROQ_MODEL}")
    print(f"GEMINI_API_KEY: {'SET (hidden)' if GEMINI_API_KEY else 'NOT SET'}")
    print(f"GEMINI_MODEL: {GEMINI_MODEL}")
    print(f"CONFIDENCE_THRESHOLD: {CONFIDENCE_THRESHOLD}")
    
    # Determine which provider will be used
    verifier = Verifier()
    provider_name = type(verifier.provider).__name__ if verifier.provider else "NONE (disabled)"
    print(f"\nProvider actually instantiated: {provider_name}")
    
    if provider_name == "GeminiProvider":
        print("WARNING: Using Gemini provider (not Groq as expected)")
    elif provider_name == "GroqProvider":
        print("OK: Using Groq provider as expected")
    elif provider_name == "NONE (disabled)":
        print("WARNING: No LLM provider available - all candidates will go to REVIEW")
    
    return provider_name


def create_raw_post_from_record(record):
    """Convert a record from fresh_preview.json to RawPost"""
    # fresh_preview.json has "text" field with full post content
    return RawPost(
        post_url=record["source_link"],
        post_date=record.get("date", ""),
        text=record.get("text", "") or record.get("snippet", ""),
        author_name=record.get("author", ""),
        author_profile_url="",
        company=record.get("company_meta", "Unclear"),
        job_card_location=record.get("job_card_location", ""),
    )


def run_classifier_test(raw_posts, classifier):
    """Run deterministic classifier on posts"""
    results = []
    for p in raw_posts:
        classified = classifier.classify(p)
        results.append({
            "source_link": p.post_url,
            "is_valid": classified.is_valid,
            "company": classified.company_name,
            "exact_role": classified.exact_role,
            "location": classified.location,
            "employment_type": classified.employment_type,
            "india_relevance": classified.india_relevance,
            "confidence": classified.confidence,
            "reason": classified.classification_reason,
        })
    return results


def run_verifier_test(classified_posts, verifier):
    """Run LLM verifier on classified posts"""
    results = []
    for c in classified_posts:
        if not c.is_valid:
            results.append({
                "source_link": c.source_link,
                "llm_called": False,
                "decision": "EXCLUDED",
                "reasons": [c.classification_reason],
            })
            continue
        
        gate = verifier.verify(
            post_text=c.text,
            post_url=c.post_url,
            author_name=c.author_name,
            author_profile_url=c.author_profile_url,
            job_card_location=c.job_card_location,
        )
        
        merged = enriched_post(c, gate)
        
        results.append({
            "source_link": c.post_url,
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
    return results


def compare_results(old_data, new_results):
    """Compare old vs new results for problematic examples"""
    problematic_companies = [
        "Sagepilot AI", "Rangmanch Farms", "Onextal", "OneXtel", 
        "AdYugo Media", "Agrim"
    ]
    
    print("\n" + "=" * 70)
    print("STEP 5: ROW-BY-ROW COMPARISON FOR PROBLEMATIC EXAMPLES")
    print("=" * 70)
    
    # Build lookup from old data (fresh_preview.json has flat records)
    old_by_link = {}
    for item in old_data.get("records", []):
        old_by_link[item["source_link"]] = item
    
    new_by_link = {r["source_link"]: r for r in new_results}
    
    print(f"\nTotal old records: {len(old_by_link)}")
    print(f"Total new records: {len(new_by_link)}")
    
    # Check for problematic companies
    for company in problematic_companies:
        found = False
        for link, old in old_by_link.items():
            if company.lower() in str(old.get("company_meta", "")).lower() or company.lower() in str(old.get("source_link", "")).lower():
                found = True
                new = new_by_link.get(link, {})
                print(f"\n--- {company} ---")
                print(f"  Source: {link}")
                print(f"  OLD: company={old.get('company_meta')}, role={old.get('classifier_role')}, location={old.get('classifier_location')}, "
                      f"hm={old.get('llm_hiring_mgr')}, email={old.get('llm_email')}, "
                      f"status={old.get('final')}, confidence={old.get('confidence')}")
                print(f"  NEW: company={new.get('merged_company')}, role={new.get('merged_role')}, "
                      f"location={new.get('merged_location')}, hm={new.get('merged_hiring_mgr')}, "
                      f"email={new.get('merged_cold_email')}, status={new.get('merged_status')}, "
                      f"confidence={new.get('merged_confidence')}")
                
                # Analysis
                old_company = old.get('company_meta') or old.get('llm_company')
                new_company = new.get('merged_company')
                if old_company in ["Unclear", "N/A", ""] and new_company not in ["Unclear", "N/A", ""]:
                    print(f"  OK Company IMPROVED: {old_company} -> {new_company}")
                elif old_company not in ["Unclear", "N/A", ""] and new_company in ["Unclear", "N/A", ""]:
                    print(f"  FAIL Company REGRESSED: {old_company} -> {new_company}")
        
        if not found:
            print(f"\n--- {company} ---")
            print(f"  NOT FOUND in old data")
    
    # Full comparison stats
    print("\n" + "=" * 70)
    print("STEP 5: FULL COMPARISON STATS")
    print("=" * 70)
    
    metrics = {
        "company_improved": 0,
        "company_regressed": 0,
        "role_improved": 0,
        "role_regressed": 0,
        "location_improved": 0,
        "location_regressed": 0,
        "hm_improved": 0,
        "hm_regressed": 0,
        "email_improved": 0,
        "email_regressed": 0,
        "status_changed": 0,
    }
    
    for link, old in old_by_link.items():
        new = new_by_link.get(link)
        if not new:
            continue
            
        # Company
        old_c = old.get("company_meta", "") or old.get("llm_company", "")
        new_c = new.get("merged_company", "")
        if old_c in ["Unclear", "N/A", ""] and new_c not in ["Unclear", "N/A", ""]:
            metrics["company_improved"] += 1
        elif old_c not in ["Unclear", "N/A", ""] and new_c in ["Unclear", "N/A", ""]:
            metrics["company_regressed"] += 1
            
        # Role
        old_r = old.get("classifier_role", "") or old.get("llm_exact_role", "")
        new_r = new.get("merged_role", "")
        if old_r in ["Unclear", "N/A", ""] and new_r not in ["Unclear", "N/A", ""]:
            metrics["role_improved"] += 1
        elif old_r not in ["Unclear", "N/A", ""] and new_r in ["Unclear", "N/A", ""]:
            metrics["role_regressed"] += 1
            
        # Location
        old_l = old.get("classifier_location", "") or old.get("llm_location", "")
        new_l = new.get("merged_location", "")
        if old_l in ["Unclear", "N/A", "Not Specified", ""] and new_l not in ["Unclear", "N/A", "Not Specified", ""]:
            metrics["location_improved"] += 1
        elif old_l not in ["Unclear", "N/A", "Not Specified", ""] and new_l in ["Unclear", "N/A", "Not Specified", ""]:
            metrics["location_regressed"] += 1
            
        # Hiring Manager
        old_h = old.get("llm_hiring_mgr", "") or old.get("merged_hiring_mgr", "")
        new_h = new.get("merged_hiring_mgr", "")
        if old_h in ["Unclear", "N/A", ""] and new_h not in ["Unclear", "N/A", ""]:
            metrics["hm_improved"] += 1
        elif old_h not in ["Unclear", "N/A", ""] and new_h in ["Unclear", "N/A", ""]:
            metrics["hm_regressed"] += 1
            
        # Email
        old_e = old.get("llm_email", "") or old.get("merged_cold_email", "")
        new_e = new.get("merged_cold_email", "")
        if old_e in ["Not Available", "N/A", ""] and new_e not in ["Not Available", "N/A", ""]:
            metrics["email_improved"] += 1
        elif old_e not in ["Not Available", "N/A", ""] and new_e in ["Not Available", "N/A", ""]:
            metrics["email_regressed"] += 1
            
        # Status
        old_s = old.get("final", "") or old.get("merged_status", "")
        new_s = new.get("merged_status", "")
        if old_s != new_s:
            metrics["status_changed"] += 1
    
    for k, v in metrics.items():
        print(f"  {k}: {v}")
    
    return metrics


def generate_report(data, provider_name, classifier_results, verifier_results, comparison_metrics):
    """Generate the diagnostic report"""
    report_path = Path("data/validation/phase13b_extraction_rootcause/today_new_api_retest.md")
    report_path.parent.mkdir(parents=True, exist_ok=True)
    
    # Count stats from original data (fresh_preview.json has stats in stats object)
    stats = data.get("stats", {})
    
    with open(report_path, "w", encoding="utf-8") as f:
        f.write("# Phase 13b: Today's Data Retest with New API Key\n\n")
        f.write(f"**Generated:** {datetime.now().isoformat()}\n\n")
        
        # 1. Today's input dataset
        f.write("## 1. Today's Input Dataset\n\n")
        f.write(f"- **Source:** `data/validation/fresh_production_preview/fresh_preview.json`\n")
        f.write(f"- **Queries:** {len(data.get('subset_queries', []))} (subset)\n")
        f.write(f"- **Max posts/keyword:** {data.get('max_posts', 0)}\n")
        f.write(f"- **Raw scraped:** {stats.get('scraped_raw', 0)}\n")
        f.write(f"- **Unique posts:** {stats.get('scraped_unique', 0)}\n")
        f.write(f"- **Current-run duplicates:** {stats.get('current_run_duplicates', 0)}\n")
        f.write(f"- **Deterministic candidates sent to LLM:** {stats.get('sent_to_groq', 0)}\n")
        f.write(f"- **LLM calls:** {stats.get('groq_calls', 0)}\n\n")
        
        # 2. Original pipeline counts
        f.write("## 2. Original Pipeline Counts\n\n")
        f.write(f"- **Final ACCEPT:** {stats.get('final_ACCEPT', 0)}\n")
        f.write(f"- **Final REVIEW:** {stats.get('final_REVIEW', 0)}\n")
        f.write(f"- **LLM REJECT:** {stats.get('final_REJECT', 0)}\n")
        f.write(f"- **Errors:** {stats.get('errors', 0)}\n\n")
        
        # 3. New API configuration verification
        f.write("## 3. New API Configuration Verification\n\n")
        f.write(f"- **LLM_PROVIDER env:** {os.getenv('LLM_PROVIDER', 'NOT SET (defaults to auto)')}\n")
        f.write(f"- **GROQ_API_KEY:** {'SET' if GROQ_API_KEY else 'NOT SET'}\n")
        f.write(f"- **GROQ_MODEL:** {GROQ_MODEL}\n")
        f.write(f"- **GEMINI_API_KEY:** {'SET (new key loaded)' if GEMINI_API_KEY else 'NOT SET'}\n")
        f.write(f"- **GEMINI_MODEL:** {GEMINI_MODEL}\n")
        f.write(f"- **Provider instantiated:** {provider_name}\n")
        f.write(f"- **CONFIDENCE_THRESHOLD:** {CONFIDENCE_THRESHOLD}\n\n")
        
        # 4. API smoke-test result
        f.write("## 4. API Smoke-Test Result (1-3 candidates)\n\n")
        test_candidates = [r for r in verifier_results if r.get("llm_called")][:3]
        for i, r in enumerate(test_candidates, 1):
            f.write(f"### Candidate {i}: {r['source_link']}\n\n")
            f.write(f"- **LLM called:** {r['llm_called']}\n")
            f.write(f"- **Decision:** {r['decision']}\n")
            f.write(f"- **Confidence:** {r['confidence']}\n")
            f.write(f"- **Reasons:** {r['reasons']}\n")
            f.write(f"- **Company:** {r['llm_company']}\n")
            f.write(f"- **Exact Role:** {r['llm_exact_role']}\n")
            f.write(f"- **Location:** {r['llm_location']}\n")
            f.write(f"- **Employment:** {r['llm_employment']}\n")
            f.write(f"- **Cold Email:** {r['llm_cold_email']}\n")
            f.write(f"- **Hiring Manager:** {r['llm_hiring_mgr']}\n\n")
        
        # 5. New pipeline counts
        f.write("## 5. New Pipeline Counts\n\n")
        new_accept = sum(1 for r in verifier_results if r.get("decision") == "ACCEPT")
        new_review = sum(1 for r in verifier_results if r.get("decision") == "REVIEW")
        new_reject = sum(1 for r in verifier_results if r.get("decision") == "REJECT")
        new_excluded = sum(1 for r in verifier_results if r.get("decision") == "EXCLUDED")
        f.write(f"- **Deterministic candidates:** {sum(1 for r in classifier_results if r['is_valid'])}\n")
        f.write(f"- **LLM calls:** {sum(1 for r in verifier_results if r.get('llm_called'))}\n")
        f.write(f"- **ACCEPT:** {new_accept}\n")
        f.write(f"- **REVIEW:** {new_review}\n")
        f.write(f"- **REJECT:** {new_reject}\n")
        f.write(f"- **Deterministic EXCLUDED:** {new_excluded}\n\n")
        
        # 6. Old vs New comparison
        f.write("## 6. Old vs New Comparison\n\n")
        f.write("| Metric | Old (Groq) | New (Gemini) | Delta |\n")
        f.write("|--------|-----|-----|-------|\n")
        f.write(f"| ACCEPT | {stats.get('final_ACCEPT', 0)} | {new_accept} | {new_accept - stats.get('final_ACCEPT', 0):+d} |\n")
        f.write(f"| REVIEW | {stats.get('final_REVIEW', 0)} | {new_review} | {new_review - stats.get('final_REVIEW', 0):+d} |\n")
        f.write(f"| REJECT | {stats.get('final_REJECT', 0)} | {new_reject} | {new_reject - stats.get('final_REJECT', 0):+d} |\n")
        f.write(f"| Deterministic candidates | {stats.get('sent_to_groq', 0)} | {sum(1 for r in classifier_results if r['is_valid'])} | {sum(1 for r in classifier_results if r['is_valid']) - stats.get('sent_to_groq', 0):+d} |\n")
        f.write(f"| LLM calls | {stats.get('groq_calls', 0)} | {sum(1 for r in verifier_results if r.get('llm_called'))} | {sum(1 for r in verifier_results if r.get('llm_called')) - stats.get('groq_calls', 0):+d} |\n\n")
        
        # 7. Row-by-row for problematic examples
        f.write("## 7. Row-by-Row Comparison for Problematic Examples\n\n")
        f.write("See console output above for detailed comparison.\n\n")
        
        # 8-12. Field extraction accuracy
        f.write("## 8. Company Extraction Accuracy\n\n")
        f.write(f"- **Improved:** {comparison_metrics['company_improved']}\n")
        f.write(f"- **Regressed:** {comparison_metrics['company_regressed']}\n\n")
        
        f.write("## 9. Exact-Role Extraction Accuracy\n\n")
        f.write(f"- **Improved:** {comparison_metrics['role_improved']}\n")
        f.write(f"- **Regressed:** {comparison_metrics['role_regressed']}\n\n")
        
        f.write("## 10. Location Extraction Accuracy\n\n")
        f.write(f"- **Improved:** {comparison_metrics['location_improved']}\n")
        f.write(f"- **Regressed:** {comparison_metrics['location_regressed']}\n\n")
        
        f.write("## 11. Hiring-Manager Extraction Accuracy\n\n")
        f.write(f"- **Improved:** {comparison_metrics['hm_improved']}\n")
        f.write(f"- **Regressed:** {comparison_metrics['hm_regressed']}\n\n")
        
        f.write("## 12. Cold-Email Extraction Accuracy\n\n")
        f.write(f"- **Improved:** {comparison_metrics['email_improved']}\n")
        f.write(f"- **Regressed:** {comparison_metrics['email_regressed']}\n\n")
        
        # 13. Provider/fallback behavior
        f.write("## 13. Provider/Fallback Behavior\n\n")
        if provider_name == "GeminiProvider":
            f.write("- **Provider:** Gemini (fallback from Groq due to missing GROQ_API_KEY)\n")
            f.write("- **Note:** Expected Groq but fell back to Gemini. No Groq key configured.\n")
        elif provider_name == "GroqProvider":
            f.write("- **Provider:** Groq (as expected)\n")
        else:
            f.write("- **Provider:** NONE (disabled - all candidates go to REVIEW)\n")
        f.write("- **Fallback behavior:** Verifier retries once on failure, then marks REVIEW\n\n")
        
        # 14. LinkedIn Posts vs LinkedIn Jobs findings
        f.write("## 14. LinkedIn Posts vs LinkedIn Jobs Findings\n\n")
        job_card_posts = sum(1 for r in classifier_results if r.get("job_card_location"))
        f.write(f"- Posts with job_card_location: {job_card_posts}\n")
        f.write("- Posts without job_card_location (text-only): remainder\n\n")
        
        # 15. P0/P1/P2 bugs
        f.write("## 15. P0/P1/P2 Bugs Identified\n\n")
        
        bugs = []
        
        # P0: Provider config mismatch
        if provider_name == "GeminiProvider":
            bugs.append(("P0", "Provider configuration mismatch",
                         "LLM_PROVIDER defaults to 'auto' but GROQ_API_KEY is not set, "
                         "causing fallback to Gemini instead of expected Groq. "
                         "This changes the LLM behavior entirely."))
        
        # P1: Missing GROQ_API_KEY
        if not GROQ_API_KEY:
            bugs.append(("P1", "Missing GROQ_API_KEY in environment",
                         "Expected Groq provider (openai/gpt-oss-120b) but no API key configured. "
                         "System falls back to Gemini, which has different behavior and rate limits."))
        
        # Check for specific quality regressions
        if comparison_metrics["company_regressed"] > 0:
            bugs.append(("P1", f"Company extraction regressed on {comparison_metrics['company_regressed']} records",
                         "Company names that were extracted before are now 'Unclear'"))
        
        if comparison_metrics["role_regressed"] > 0:
            bugs.append(("P1", f"Exact-role extraction regressed on {comparison_metrics['role_regressed']} records",
                         "Exact roles that were extracted before are now 'Unclear'"))
        
        if comparison_metrics["location_regressed"] > 0:
            bugs.append(("P2", f"Location extraction regressed on {comparison_metrics['location_regressed']} records",
                         "Locations that were extracted before are now 'Not Specified'"))
        
        if comparison_metrics["hm_regressed"] > 0:
            bugs.append(("P2", f"Hiring-manager extraction regressed on {comparison_metrics['hm_regressed']} records",
                         "Hiring manager names that were extracted before are now 'Unclear'"))
        
        if comparison_metrics["email_regressed"] > 0:
            bugs.append(("P2", f"Cold-email extraction regressed on {comparison_metrics['email_regressed']} records",
                         "Emails that were extracted before are now 'Not Available'"))
        
        # Check for Gemini 429 errors
        gemini_429 = any("429" in str(r.get("reasons", [])) for r in verifier_results)
        if gemini_429:
            bugs.append(("P0", "Gemini HTTP 429 rate limit errors detected",
                         "New Gemini key hitting rate limits. Groq was expected."))
        
        # Add critical finding about deterministic classifier
        deterministic_delta = sum(1 for r in classifier_results if r['is_valid']) - stats.get('sent_to_groq', 0)
        if deterministic_delta < -2:
            bugs.append(("P0", f"Deterministic classifier passed {abs(deterministic_delta)} fewer candidates",
                         f"Original run sent {stats.get('sent_to_groq', 0)} to LLM; new run only {sum(1 for r in classifier_results if r['is_valid'])}. "
                         "This indicates the deterministic classifier is rejecting valid candidates, possibly due to "
                         "different text input (full text vs snippets) or configuration differences."))
        
        # Add critical finding about Gemini errors
        gemini_errors = sum(1 for r in verifier_results if r.get("llm_called") and 
                           ("503" in str(r.get("reasons", [])) or "Expecting property name" in str(r.get("reasons", []))))
        if gemini_errors > 0:
            bugs.append(("P0", f"Gemini provider errors on {gemini_errors} calls",
                         "Gemini returning HTTP 503 (high demand) and JSON parse errors. "
                         "These cause fallback to REVIEW with 0 confidence, losing all extracted fields."))
        
        for severity, title, desc in bugs:
            f.write(f"### {severity}: {title}\n\n")
            f.write(f"{desc}\n\n")
        
        if not bugs:
            f.write("No bugs identified.\n\n")
        
        # 16. Recommended fixes
        f.write("## 16. Recommended Fixes\n\n")
        if provider_name == "GeminiProvider":
            f.write("1. **P0:** Set `GROQ_API_KEY` in `.env` and `LLM_PROVIDER=groq` to use expected provider\n")
            f.write("2. **P1:** Set `GROQ_MODEL=openai/gpt-oss-120b` in `.env` as expected\n")
        if comparison_metrics["company_regressed"] > 0 or comparison_metrics["role_regressed"] > 0:
            f.write("3. **P1:** Investigate deterministic classifier company/role extraction - may be LLM-dependent\n")
        f.write("4. **P2:** Verify enrichment phase preserves LLM-extracted fields correctly\n")
        f.write("5. **P0:** Ensure diagnostic tests use FULL post text (not snippets) for fair comparison\n")
    
    print("OK Report written to: " + str(report_path))
    return report_path


def main():
    print("=" * 70)
    print("PHASE 13b: DIAGNOSTIC RETEST WITH NEW API KEY")
    print("=" * 70)
    
    # Step 1: Load data
    print("\nSTEP 1: LOADING TODAY'S PIPELINE DATA")
    print("-" * 70)
    data = load_today_data()
    stats = data.get("stats", {})
    print(f"Loaded fresh_preview.json:")
    print(f"  Scraped raw: {stats.get('scraped_raw')}")
    print(f"  Unique: {stats.get('scraped_unique')}")
    print(f"  Deterministic candidates: {stats.get('sent_to_groq')}")
    print(f"  LLM calls: {stats.get('groq_calls')}")
    print(f"  ACCEPT: {stats.get('final_ACCEPT')}")
    print(f"  REVIEW: {stats.get('final_REVIEW')}")
    print(f"  LLM REJECT: {stats.get('final_REJECT')}")
    
    # Step 2: Verify LLM config
    provider_name = verify_llm_config()
    
    if provider_name == "NONE (disabled)":
        print("\nFAIL No LLM provider available. Cannot proceed with LLM tests.")
        return
    
    # Step 3: Small API test
    print("\n" + "=" * 70)
    print("STEP 3: SMALL API TEST (1-3 candidates)")
    print("=" * 70)
    
    # Get first 3 candidates that passed deterministic (stage != deterministic_REJECT)
    test_records = []
    for item in data.get("records", []):
        if item.get("stage") != "deterministic_REJECT" and len(test_records) < 3:
            test_records.append(item)
    
    print(f"\nTesting {len(test_records)} candidates...")
    
    # Initialize
    classifier = DeterministicClassifier()
    verifier = Verifier()
    
    # Convert to RawPosts
    test_raw_posts = [create_raw_post_from_record(r) for r in test_records]
    
    # Classify
    test_classified = []
    for p in test_raw_posts:
        c = classifier.classify(p)
        test_classified.append(c)
        print(f"\n  {p.post_url}")
        print(f"    Deterministic: valid={c.is_valid}, company={c.company_name}, role={c.exact_role}")
    
    # Verify
    print("\n  LLM Verification:")
    test_verifier_results = []
    for c in test_classified:
        if not c.is_valid:
            print(f"    {c.post_url} -> EXCLUDED by deterministic")
            test_verifier_results.append({
                "source_link": c.post_url,
                "llm_called": False,
                "decision": "EXCLUDED",
            })
            continue
        gate = verifier.verify(
            post_text=c.text,
            post_url=c.post_url,
            author_name=c.author_name,
            author_profile_url=c.author_profile_url,
            job_card_location=c.job_card_location,
        )
        test_verifier_results.append({
            "source_link": c.post_url,
            "llm_called": gate.llm_called,
            "decision": gate.decision,
            "confidence": gate.confidence,
            "reasons": gate.reasons,
            "llm_company": gate.verdict.company,
            "llm_exact_role": gate.verdict.exact_role,
            "llm_location": gate.verdict.location,
            "llm_employment": gate.verdict.employment_type,
            "llm_cold_email": gate.verdict.cold_email,
            "llm_hiring_mgr": gate.verdict.hiring_manager_name,
            "llm_hiring_mgr_linkedin": gate.verdict.hiring_manager_linkedin,
        })
        print(f"    {c.post_url}")
        print(f"      LLM decision: {gate.decision} (confidence={gate.confidence})")
        print(f"      Company: {gate.verdict.company}")
        print(f"      Role: {gate.verdict.exact_role}")
        print(f"      Location: {gate.verdict.location}")
        print(f"      Employment: {gate.verdict.employment_type}")
        print(f"      Reasons: {gate.reasons}")
    
    # Check for issues
    print("\n  Smoke test summary:")
    for r in test_verifier_results:
        if r.get("llm_called"):
            if "429" in str(r.get("reasons", [])):
                print("  FAIL " + r['source_link'] + ": HTTP 429 rate limit")
            elif "authentication" in str(r.get("reasons", [])).lower() or "unauthorized" in str(r.get("reasons", [])).lower():
                print("  FAIL " + r['source_link'] + ": Authentication error")
            elif r.get("decision") == "REVIEW" and "unavailable" in str(r.get("reasons", [])).lower():
                print("  FAIL " + r['source_link'] + ": Provider unavailable fallback")
            else:
                print("  OK " + r['source_link'] + ": OK (" + r['decision'] + ")")
    
    # Step 4: Reprocess all data
    print("\n" + "=" * 70)
    print("STEP 4: REPROCESSING ALL TODAY'S DATA")
    print("=" * 70)
    
    # Collect all records from old data (fresh_preview has flat records)
    all_old_records = data.get("records", [])
    
    print(f"Total records to reprocess: {len(all_old_records)}")
    
    # Convert to RawPosts
    all_raw_posts = [create_raw_post_from_record(r) for r in all_old_records]
    
    # Classify all
    print("Running deterministic classifier...")
    all_classified = [classifier.classify(p) for p in all_raw_posts]
    valid_count = sum(1 for c in all_classified if c.is_valid)
    print(f"  Valid (passed deterministic): {valid_count}")
    print(f"  Excluded: {len(all_classified) - valid_count}")
    
    # Verify all
    print("Running LLM verifier...")
    all_verifier_results = []
    for i, c in enumerate(all_classified):
        if not c.is_valid:
            all_verifier_results.append({
                "source_link": c.post_url,
                "llm_called": False,
                "decision": "EXCLUDED",
                "reasons": [c.classification_reason],
            })
            continue
        
        if i % 5 == 0:
            print(f"  Processing {i+1}/{len(all_classified)}...")
        
        gate = verifier.verify(
            post_text=c.text,
            post_url=c.post_url,
            author_name=c.author_name,
            author_profile_url=c.author_profile_url,
            job_card_location=c.job_card_location,
        )
        
        merged = enriched_post(c, gate)
        
        all_verifier_results.append({
            "source_link": c.post_url,
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
    
    print("  Done.")
    
    # Step 5: Compare
    classifier_results = [{
        "source_link": c.post_url,
        "is_valid": c.is_valid,
        "company": c.company_name,
        "exact_role": c.exact_role,
        "location": c.location,
        "employment_type": c.employment_type,
        "india_relevance": c.india_relevance,
        "confidence": c.confidence,
        "reason": c.classification_reason,
        "job_card_location": c.job_card_location,
    } for c in all_classified]
    
    comparison_metrics = compare_results(data, all_verifier_results)
    
    # Step 6: Already done in compare_results
    
    # Step 7: Generate report
    print("\n" + "=" * 70)
    print("STEP 7: GENERATING REPORT")
    print("=" * 70)
    
    report_path = generate_report(
        data, provider_name, classifier_results, all_verifier_results, comparison_metrics
    )
    
    print("\nOK Diagnostic complete. Report: " + str(report_path))
    print("\nNOTE: This is a DIAGNOSTIC TEST ONLY. No code was modified.")


if __name__ == "__main__":
    main()