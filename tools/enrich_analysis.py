"""
tools/enrich_analysis.py — OFFLINE Phase-6 enrichment demo on historical leads.

Reads the real classified-lead datasets under data/runs/ (or a specific JSON),
runs the Phase-6 enrichment layer on each ACCEPTED lead, and prints a
before/after report.

This is a READ-ONLY, DRY analysis:
  - it never touches Google Sheets
  - it never spends Apify credits
  - it never calls an LLM (deterministic enrichment only)
  - DRY_RUN is never modified

Usage:
    python tools/enrich_analysis.py [path-to-leads.json] [--limit N]
"""
import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.enrichment import Enricher, EnrichmentStatus
from app.enrichment.schemas import EmailStatus

DEFAULT = Path(__file__).resolve().parent.parent / "data" / "runs" / "2026-08-23T121621Z_leads.json"


def load_leads(path: str):
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, list):
        return data
    # Some exports are dict-typed records; surface whatever list is present.
    for value in data.values():
        if isinstance(value, list):
            return value
    raise ValueError("Unrecognised leads file shape")


def to_enrich_input(rec: dict):
    """Map a historical run-lead record to Enricher.enrich keyword args."""
    post_url = rec.get("post_url") or rec.get("source_link") or ""
    text = rec.get("text") or rec.get("post_snippet") or ""
    author = rec.get("author_name") or ""
    author_url = rec.get("author_profile_url") or ""
    job_meta = rec.get("company") or "Unclear"
    job_loc = rec.get("location") or ""
    emp = rec.get("employment_type") or "Unclear"
    return dict(
        post_text=text,
        source_link=post_url,
        post_date=rec.get("post_date") or "",
        author_name=author,
        author_profile_url=author_url,
        job_metadata_company=job_meta,
        job_metadata_is_authoritative=(job_meta not in ("Unclear", "")),
        job_card_location=job_loc,
        classified_major_category="Chief of Staff" if "Chief" in (rec.get("role_category") or "") else "Founder's Office",
        classified_exact_role=rec.get("detected_role_title") or "Unclear",
        classified_confidence=float(rec.get("confidence") or 0.0),
        classified_location=job_loc,
        classified_employment=emp,
        classified_status=rec.get("status") or "New",
        classified_india_relevance="India",
    )


_HEADER = "{:<12} {:<26} {:<34} {:<22} {:<14} {:<16} {:<14} {}".format(
    "Status", "Company", "Exact Role", "Location", "Employment", "Experience", "CTC", "Email")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("path", nargs="?", default=str(DEFAULT))
    ap.add_argument("--limit", type=int, default=8)
    args = ap.parse_args()

    if not os.path.exists(args.path):
        print(f"Input file not found: {args.path}")
        sys.exit(1)

    leads = load_leads(args.path)
    if not leads:
        print("No leads found.")
        return

    enricher = Enricher()
    print(f"Analysing {len(leads)} historical records from {os.path.basename(args.path)} "
          f"(limit {args.limit} shown). OFFLINE / no sheet writes.\n")

    status_counts = {}
    email_found = 0
    for rec in leads[: args.limit]:
        try:
            e = enricher.enrich(**to_enrich_input(rec))
        except Exception as exc:  # never let a single record break the report
            print(f"!! enrichment failed for {rec.get('post_url')}: {exc}")
            continue

        status_counts[e.enrichment_status] = status_counts.get(e.enrichment_status, 0) + 1
        if e.email_status == EmailStatus.FOUND.value:
            email_found += 1

        print(_HEADER)
        print(f"{e.enrichment_status:<12} {e.company_name:<26} {e.exact_role[:34]:<34} "
              f"{e.location[:22]:<22} {e.employment_type:<14} {e.experience_required[:16]:<16} "
              f"{e.ctc:<14} {e.cold_email}")
        print(f"    conf={e.enrichment_confidence:.2f} | email={e.email_status} "
              f"| HM={e.hiring_manager_name!r} ({e.hiring_manager_evidence}) | "
              f"evidence={e.company_evidence}")
        print(f"    source: {e.source_link}")
        print()

    print("=== Enrichment status distribution ===")
    for st in EnrichmentStatus:
        print(f"  {st.value:<24} {status_counts.get(st.value, 0)}")
    print(f"\nEmails verified found: {email_found} / {min(len(leads), args.limit)}\n")
    print("REMINDER: emails are only reported when found in legit public evidence. "
          "No emails are ever guessed.")


if __name__ == "__main__":
    main()
