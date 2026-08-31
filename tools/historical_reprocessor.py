"""
tools/historical_reprocessor.py

Reprocess HISTORICAL scraped LinkedIn data through the EXISTING production
verification protocol (app.classifier + app.llm.verifier + hard gates).

This module does NOT modify production logic. It is an orchestration wrapper:

  1. Normalize each raw Apify record into a RawPost, reusing the production
     normalization helpers from app.sources.datadoping_source.
  2. Run the production DeterministicClassifier (which includes the India gate).
  3. Run the production Groq Verifier (with hard gates) on eligible candidates
     only.
  4. Emit a cleaned dataset + machine-readable report.

No Google Sheets writes. No Apify. DRY_RUN behavior is untouched (we never
touch the Sheets writer at all).
"""
import os
import sys
import json
import csv
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app.sources.datadoping_source import (
    _strip_query,
    _company_from_job_card,
    _location_from_job_card,
)
from app.classifier import DeterministicClassifier
from app.llm.verifier import Verifier, enriched_post
from app.models import RawPost

DEFAULT_RAW = os.path.join(ROOT, "data", "recovered", "apify_posts_2026-08-17_18.json")
DEFAULT_FINAL = os.path.join(ROOT, "data", "runs", "2026-08-23T121621Z_leads.json")
OUT_DIR = os.path.join(ROOT, "data", "reprocessed")

# 16-field business schema required by the brief.
BUSINESS_FIELDS = [
    "company", "major_category", "exact_role", "ctc", "location",
    "employment_type", "experience_required", "india_relevance",
    "hiring_manager_name", "hiring_manager_linkedin_url", "cold_email",
    "post_description", "confidence_score",
]


def _post_date_from_item(item):
    """Prefer the ISO string the actor emits; fall back to epoch-ms (as the
    production helper expects). Keeps the original post date for fidelity."""
    iso = item.get("postedAtISO") or item.get("postedAtISO")
    if iso:
        return iso
    ts = item.get("postedAtTimestamp") or item.get("timestamp")
    if ts:
        try:
            return datetime.fromtimestamp(int(ts) / 1000, tz=timezone.utc).isoformat()
        except (TypeError, ValueError):
            return ""
    return ""


def normalize_item(item):
    """Produce a RawPost using the SAME field mapping the production scraper
    uses (datadoping_source._to_raw_post), plus a richer post_date capture.

    Historical raw snapshots (e.g. data/recovered/apify_posts_*.json) were
    exported with a FLAT key schema (url, authorName, authorProfileUrl,
    postedAtISO) instead of the live actor schema (post_url, nested author,
    timestamp). We adapt those key names here; the underlying production
    normalization helpers for company/location are reused unchanged.
    """
    post_url = _strip_query(item.get("post_url") or item.get("url") or "")
    text = item.get("text") or ""
    if not post_url or not text:
        return None
    author = item.get("author") or {}
    if not isinstance(author, dict):
        author = {}
    author_name = (
        author.get("name")
        or item.get("authorName")
        or item.get("owner_name")
        or ""
    )
    author_profile_url = (
        author.get("profile_url")
        or item.get("authorProfileUrl")
        or ""
    )
    return RawPost(
        post_url=post_url,
        post_date=_post_date_from_item(item),
        text=text,
        author_name=author_name,
        author_profile_url=_strip_query(author_profile_url),
        company=_company_from_job_card(item),
        job_card_location=_location_from_job_card(item),
    )


def _na_to_placeholder(value, default):
    """Map production 'N/A'/empty sentinels to the project's placeholder words."""
    if value is None:
        return default
    if isinstance(value, str) and value.strip() in ("", "N/A", "Unclear"):
        return default
    return value


def build_record(raw, classified, gate, legacy=None):
    """Assemble the 16-field record + status + reason from the pipeline output.

    `gate` is None for deterministic-stage rejects.
    """
    stage = "deterministic"
    status = "REJECT"
    reason = classified.classification_reason
    merged = classified

    if gate is not None:
        stage = "llm"
        status = gate.decision
        merged = enriched_post(classified, gate)
        v = gate.verdict
        reason = "; ".join(
            [x for x in ([v.reason] if v.reason else []) + gate.reasons if x]
        )

    company_default = raw.company if raw.company != "Unclear" else "Unclear"
    rec = {
        "source_linkedin_url": raw.post_url,
        "post_date": raw.post_date,
        "company": _na_to_placeholder(merged.company_name, company_default),
        "major_category": _na_to_placeholder(merged.major_category, "Unclear"),
        "exact_role": _na_to_placeholder(merged.exact_role, "Unclear"),
        "ctc": _na_to_placeholder(merged.ctc, "Not Disclosed"),
        "location": _na_to_placeholder(merged.location, raw.job_card_location or "Not Specified"),
        "employment_type": _na_to_placeholder(merged.employment_type, "Unclear"),
        "experience_required": _na_to_placeholder(merged.experience_requirement, "Not Specified"),
        "india_relevance": _na_to_placeholder(merged.india_relevance, "Unclear"),
        "hiring_manager_name": _na_to_placeholder(merged.hiring_manager_name, "Unclear"),
        "hiring_manager_linkedin_url": _na_to_placeholder(merged.hiring_manager_linkedin, "Unclear"),
        "cold_email": _na_to_placeholder(merged.cold_email, "Not Available"),
        "post_description": (merged.description or raw.text)[:600],
        "confidence_score": merged.confidence,
        "verification_status": status,
        "stage": stage,
        "rejection_review_reason": reason,
        "original_text": raw.text,
    }
    if legacy:
        rec["legacy_role_category"] = legacy.get("role_category")
        rec["legacy_detected_role_title"] = legacy.get("detected_role_title")
    return rec


def _build_legacy_lookup(final_path):
    if not final_path or not os.path.exists(final_path):
        return {}
    try:
        rows = json.load(open(final_path, encoding="utf-8"))
    except Exception:
        return {}
    lookup = {}
    for r in rows:
        if isinstance(r, dict) and r.get("post_url"):
            lookup[_strip_query(r["post_url"])] = r
    return lookup


def reprocess(raw_items, verifier=None, legacy_lookup=None):
    """Run every raw item through the production protocol.

    Returns (records, report). `verifier` may be injected for offline tests;
    when None the production Groq verifier is used.
    """
    cls = DeterministicClassifier()
    if verifier is None:
        verifier = Verifier()

    # 1. Normalize + dedup by normalized source URL.
    seen = set()
    unique = []
    dup_count = 0
    for it in raw_items:
        rp = normalize_item(it)
        if rp is None:
            continue
        if rp.post_url in seen:
            dup_count += 1
            continue
        seen.add(rp.post_url)
        unique.append(rp)

    records = []
    accepted = reviewed = rejected = llm_failures = errors = 0
    det_cands = 0

    for rp in unique:
        try:
            c = cls.classify(rp)
        except Exception:
            errors += 1
            continue

        gate = None
        if c.is_valid:
            det_cands += 1
            try:
                gate = verifier.verify(
                    post_text=c.text,
                    post_url=c.post_url,
                    author_name=c.author_name,
                    author_profile_url=c.author_profile_url,
                    job_card_location=c.job_card_location,
                )
            except Exception:
                errors += 1
                continue
            v = gate.verdict
            is_api_failure = (
                (gate.reasons and gate.reasons[0].startswith("LLM unavailable after retry"))
                or (v.reason or "").startswith("LLM verification unavailable")
            )
            if is_api_failure:
                llm_failures += 1

        legacy = legacy_lookup.get(rp.post_url) if legacy_lookup else None
        rec = build_record(rp, c, gate, legacy)
        records.append(rec)

        if rec["verification_status"] == "ACCEPT":
            accepted += 1
        elif rec["verification_status"] == "REVIEW":
            reviewed += 1
        else:
            rejected += 1

    report = {
        "total_input": len(raw_items),
        "unique_after_dedup": len(unique),
        "duplicates": dup_count,
        "deterministic_candidates": det_cands,
        "llm_calls": verifier.llm_calls,
        "accepted": accepted,
        "review": reviewed,
        "rejected": rejected,
        "llm_api_failures": llm_failures,
        "processing_errors": errors,
    }
    return records, report


CSV_COLUMNS = [
    "source_linkedin_url", "post_date", "company", "major_category",
    "exact_role", "ctc", "location", "employment_type", "experience_required",
    "india_relevance", "hiring_manager_name", "hiring_manager_linkedin_url",
    "cold_email", "post_description", "confidence_score",
    "verification_status", "rejection_review_reason", "original_text",
]


def write_outputs(records, report):
    os.makedirs(OUT_DIR, exist_ok=True)

    with open(os.path.join(OUT_DIR, "historical_cleaned.json"), "w", encoding="utf-8") as f:
        json.dump(records, f, ensure_ascii=False, indent=2)

    with open(os.path.join(OUT_DIR, "historical_cleaned.csv"), "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=CSV_COLUMNS, extrasaction="ignore")
        w.writeheader()
        for r in records:
            w.writerow(r)

    accepts = [r for r in records if r["verification_status"] == "ACCEPT"]
    reviews = [r for r in records if r["verification_status"] == "REVIEW"]
    rejects = [r for r in records if r["verification_status"] == "REJECT"]

    report_doc = {
        "metrics": report,
        "accepts": accepts,
        "reviews": reviews,
        "rejects": [
            {
                "source_linkedin_url": r["source_linkedin_url"],
                "company": r["company"],
                "major_category": r["major_category"],
                "exact_role": r["exact_role"],
                "verification_status": r["verification_status"],
                "rejection_review_reason": r["rejection_review_reason"],
            }
            for r in rejects
        ],
        "all_source_urls": [r["source_linkedin_url"] for r in records],
    }
    with open(os.path.join(OUT_DIR, "historical_report.json"), "w", encoding="utf-8") as f:
        json.dump(report_doc, f, ensure_ascii=False, indent=2)

    return OUT_DIR


def main():
    raw_items = json.load(open(DEFAULT_RAW, encoding="utf-8"))
    legacy_lookup = _build_legacy_lookup(DEFAULT_FINAL)
    records, report = reprocess(raw_items, legacy_lookup=legacy_lookup)
    write_outputs(records, report)
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
