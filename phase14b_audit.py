#!/usr/bin/env python3
"""Phase 14B: Comprehensive Extraction Quality Audit with Working Groq"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.models import RawPost
from app.classifier import DeterministicClassifier
from app.llm.verifier import Verifier, enriched_post
from app.enrichment import Enricher
from app.contact import get_contact_provider
from app.config import (
    CONTACT_PROVIDER, CONTACT_CACHE_PATH, CONTACT_MAX_PAGES,
    CONTACT_MAX_REQUESTS, CONTACT_MIN_INTERVAL_S, CONTACT_TIMEOUT_S,
)


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
    )


def classify_field_issue(source_text, deterministic_val, llm_val, merged_val, field_name):
    """Classify a field issue as A-F"""
    source_lower = source_text.lower()
    
    # Check if information is present in source
    info_in_source = False
    if field_name == "company":
        # Look for company patterns
        patterns = [
            "is hiring", "are hiring", "is looking for", "are looking for",
            "at ", "join ", "role at ", "position at ", "vacancy at ",
            "hiring |", "hiring:", "position:", "role:", "title:",
            "opening:", "vacancy:", "designation:", "join as",
        ]
        for p in patterns:
            if p in source_lower:
                info_in_source = True
                break
    elif field_name == "exact_role":
        # Look for role keywords
        if any(kw in source_lower for kw in ["founder's office", "founders office", "chief of staff", "founder office", "founder associate"]):
            info_in_source = True
    elif field_name == "location":
        # Look for location patterns
        locations = ["bangalore", "bengaluru", "mumbai", "delhi", "gurgaon", "gurugram", "noida", "hyderabad", "pune", "chennai", "kolkata", "ahmedabad", "jaipur", "india", "remote"]
        for loc in locations:
            if loc in source_lower:
                info_in_source = True
                break
    elif field_name == "hiring_manager":
        # Look for hiring manager evidence
        if any(p in source_lower for p in ["i'm hiring", "we're hiring", "i am hiring", "my team", "our team", "contact me", "dm me", "reach out"]):
            info_in_source = True
    elif field_name == "cold_email":
        # Look for email
        if "@" in source_text and "." in source_text:
            info_in_source = True
    
    det_has = deterministic_val not in ["Unclear", "N/A", "", "Not Available", "Not Specified", "Not Disclosed"]
    llm_has = llm_val not in ["Unclear", "N/A", "", "Not Available", "Not Specified", "Not Disclosed"]
    merged_has = merged_val not in ["Unclear", "N/A", "", "Not Available", "Not Specified", "Not Disclosed"]
    
    # Classification logic
    if merged_has and (det_has or llm_has):
        # Value preserved
        return "A"
    elif not info_in_source and not merged_has:
        # Info not in source, correctly Unclear
        return "B"
    elif info_in_source and not det_has and not llm_has and not merged_has:
        # Info in source but not extracted by anyone
        return "C"
    elif (det_has or llm_has) and not merged_has:
        # Extracted but lost in merge
        return "D"
    elif llm_has and not info_in_source:
        # LLM hallucinated
        return "E"
    elif det_has and not info_in_source:
        # Deterministic extractor hallucinated
        return "F"
    elif info_in_source and not merged_has:
        # Present but not extracted
        return "C"
    else:
        return "B"  # Default to not present


def main():
    print("=" * 80)
    print("PHASE 14B: EXTRACTION QUALITY AUDIT AFTER GROQ RESTORATION")
    print("=" * 80)
    
    data = load_data()
    records = data.get("records", [])
    
    # Initialize pipeline
    classifier = DeterministicClassifier()
    verifier = Verifier()
    
    # Check provider
    provider_name = type(verifier.provider).__name__ if verifier.provider else "NONE"
    print(f"\nProvider: {provider_name}")
    print(f"Model: {verifier.provider.model if verifier.provider else 'N/A'}")
    
    # Get enrichment config
    _contact_provider = None
    if CONTACT_PROVIDER and CONTACT_PROVIDER != "null":
        _contact_provider = get_contact_provider(
            CONTACT_PROVIDER,
            cache_path=CONTACT_CACHE_PATH,
            max_pages=CONTACT_MAX_PAGES,
            max_requests=CONTACT_MAX_REQUESTS,
            min_interval_s=CONTACT_MIN_INTERVAL_S,
            timeout_s=CONTACT_TIMEOUT_S,
        )
    enricher = Enricher(contact_provider=_contact_provider)
    
    # Process only deterministic_PASS records
    candidates = [r for r in records if r.get("stage") == "deterministic_PASS"]
    print(f"\nCandidates to audit: {len(candidates)}")
    
    audit_results = []
    
    for record in candidates:
        p = create_raw_post(record)
        c = classifier.classify(p)
        
        if not c.is_valid:
            continue
        
        # LLM verification
        gate = verifier.verify(
            post_text=c.text,
            post_url=c.post_url,
            author_name=c.author_name,
            author_profile_url=c.author_profile_url,
            job_card_location=c.job_card_location,
        )
        
        # Merge
        merged = enriched_post(c, gate)
        
        # Enrichment
        enriched = enricher.enrich_from_classified(merged, gate.verdict)
        
        # Collect field values at each stage
        fields = {
            "company": {
                "source": record.get("company_meta", ""),
                "deterministic": c.company_name,
                "llm": gate.verdict.company,
                "merged": merged.company_name,
                "enriched": getattr(enriched, "company", "") if enriched else "",
            },
            "exact_role": {
                "source": "N/A (inferred from text)",
                "deterministic": c.exact_role,
                "llm": gate.verdict.exact_role,
                "merged": merged.exact_role,
                "enriched": getattr(enriched, "exact_role", "") if enriched else "",
            },
            "location": {
                "source": record.get("job_card_location", ""),
                "deterministic": c.location,
                "llm": gate.verdict.location,
                "merged": merged.location,
                "enriched": getattr(enriched, "location", "") if enriched else "",
            },
            "source_link": {
                "source": p.post_url,
                "deterministic": p.post_url,
                "llm": gate.verdict.post_url if hasattr(gate.verdict, 'post_url') else p.post_url,
                "merged": merged.source_link,
                "enriched": getattr(enriched, "source_link", "") if enriched else "",
            },
            "hiring_manager": {
                "source": "N/A (inferred from text)",
                "deterministic": c.hiring_manager_name,
                "llm": gate.verdict.hiring_manager_name,
                "merged": merged.hiring_manager_name,
                "enriched": getattr(enriched, "hiring_manager_name", "") if enriched else "",
            },
            "hiring_manager_linkedin": {
                "source": "N/A (inferred from text)",
                "deterministic": c.hiring_manager_linkedin,
                "llm": gate.verdict.hiring_manager_linkedin,
                "merged": merged.hiring_manager_linkedin,
                "enriched": getattr(enriched, "hiring_manager_linkedin", "") if enriched else "",
            },
            "cold_email": {
                "source": "N/A (inferred from text)",
                "deterministic": c.cold_email,
                "llm": gate.verdict.cold_email,
                "merged": merged.cold_email,
                "enriched": getattr(enriched, "email", "") if enriched else "",
            },
        }
        
        # Classify each field
        classifications = {}
        for field_name, vals in fields.items():
            if field_name == "source_link":
                classifications[field_name] = "A"  # Always preserved
            else:
                classifications[field_name] = classify_field_issue(
                    p.text, vals["deterministic"], vals["llm"], vals["merged"], field_name
                )
        
        audit_results.append({
            "source_link": p.post_url,
            "text": p.text[:500],
            "author": p.author_name,
            "job_card_location": p.job_card_location,
            "decision": gate.decision,
            "confidence": gate.confidence,
            "fields": fields,
            "classifications": classifications,
            "llm_reasons": gate.reasons,
        })
    
    # Print detailed audit
    print("\n" + "=" * 80)
    print("DETAILED FIELD AUDIT FOR EACH CANDIDATE")
    print("=" * 80)
    
    def safe_print(text):
        """Print text safely handling unicode"""
        try:
            print(text)
        except UnicodeEncodeError:
            print(text.encode('ascii', 'replace').decode())
    
    for i, r in enumerate(audit_results, 1):
        safe_print(f"\n{'='*80}")
        safe_print(f"CANDIDATE {i}: {r['source_link']}")
        safe_print(f"{'='*80}")
        safe_print(f"Decision: {r['decision']} (confidence={r['confidence']})")
        safe_print(f"Author: {r['author']}")
        safe_print(f"Job Card Location: {r['job_card_location']}")
        safe_print(f"Source Text: {r['text'][:300]}...")
        safe_print(f"LLM Reasons: {r['llm_reasons']}")
        
        for field_name, vals in r["fields"].items():
            if field_name == "source_link":
                continue
            cls = r["classifications"][field_name]
            safe_print(f"\n  {field_name.upper()}:")
            safe_print(f"    Deterministic: {vals['deterministic']}")
            safe_print(f"    LLM:           {vals['llm']}")
            safe_print(f"    Merged:        {vals['merged']}")
            safe_print(f"    Enriched:      {vals['enriched']}")
            safe_print(f"    Classification: {cls}")
            meanings = ["CORRECT", "NOT IN SOURCE", "PRESENT NOT EXTRACTED", "LOST IN MERGE", "LLM MIS-EXTRACTED", "DET MIS-EXTRACTED"]
            safe_print(f"    Meaning: {meanings[ord(cls)-65]}")
    
    # Summary statistics
    print("\n" + "=" * 80)
    print("SUMMARY STATISTICS")
    print("=" * 80)
    
    field_counts = {}
    for field in ["company", "exact_role", "location", "hiring_manager", "hiring_manager_linkedin", "cold_email"]:
        counts = {"A": 0, "B": 0, "C": 0, "D": 0, "E": 0, "F": 0}
        for r in audit_results:
            cls = r["classifications"].get(field, "B")
            counts[cls] += 1
        field_counts[field] = counts
        print(f"\n{field.upper()}:")
        labels = ["A: CORRECT", "B: NOT IN SOURCE", "C: PRESENT NOT EXTRACTED", "D: LOST IN MERGE", "E: LLM MIS-EXTRACTED", "F: DET MIS-EXTRACTED"]
        for j, (k, v) in enumerate(counts.items()):
            print(f"  {labels[j]}: {v}")
    
    # Search recall analysis
    print("\n" + "=" * 80)
    print("SEARCH RECALL ANALYSIS")
    print("=" * 80)
    total = len(records)
    det_pass = len([r for r in records if r.get("stage") == "deterministic_PASS"])
    det_reject = len([r for r in records if r.get("stage") == "deterministic_REJECT"])
    print(f"Total unique posts: {total}")
    print(f"Deterministic PASS: {det_pass}")
    print(f"Deterministic REJECT: {det_reject}")
    
    reject_reasons = {}
    for r in records:
        if r.get("stage") == "deterministic_REJECT":
            reason = r.get("classify_reason", "unknown")
            reject_reasons[reason] = reject_reasons.get(reason, 0) + 1
    print("\nRejection reasons:")
    for reason, count in sorted(reject_reasons.items(), key=lambda x: -x[1]):
        print(f"  {reason}: {count}")
    
    # LinkedIn Jobs audit
    print("\n" + "=" * 80)
    print("LINKEDIN JOBS AUDIT")
    print("=" * 80)
    job_card_count = sum(1 for r in records if r.get("job_card_location"))
    print(f"Posts with job_card_location: {job_card_count}/{total}")
    print(f"Posts without job_card_location: {total - job_card_count}")
    
    # Check if job card metadata is being used
    print("\nJob card metadata usage:")
    for r in candidates:
        job_loc = r.get("job_card_location", "")
        if job_loc:
            print(f"  {r['source_link']}: {job_loc}")
    
    # Internship/pay-to-apply audit
    print("\n" + "=" * 80)
    print("INTERNSHIP / PAY-TO-APPLY AUDIT")
    print("=" * 80)
    internship_posts = [r for r in records if "intern" in r.get("classify_reason", "").lower()]
    print(f"Posts rejected as internship: {len(internship_posts)}")
    for r in internship_posts:
        text = r.get("text", "").lower()
        signals = []
        if "paid internship" in text or "stipend" in text:
            signals.append("paid/stipend")
        if "registration fee" in text or "application fee" in text or "training fee" in text or "security deposit" in text:
            signals.append("fee/deposit")
        if "100% placement" in text or "guaranteed placement" in text:
            signals.append("guaranteed placement")
        if "course" in text and ("internship" in text or "training" in text):
            signals.append("course disguised")
        print(f"  {r['source_link']}: {', '.join(signals) if signals else 'unpaid/free apply'}")
    
    # Save detailed results
    output = {
        "provider": provider_name,
        "model": verifier.provider.model if verifier.provider else "N/A",
        "candidates_audited": len(audit_results),
        "field_classifications": field_counts,
        "reject_reasons": reject_reasons,
        "job_card_count": job_card_count,
        "total_posts": total,
        "detailed_audit": audit_results,
    }
    
    output_path = Path("data/validation/phase13b_extraction_rootcause/phase14b_audit_details.json")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)
    
    print(f"\nDetailed results saved to: {output_path}")
    
    return audit_results, field_counts


if __name__ == "__main__":
    main()