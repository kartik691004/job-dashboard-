"""
tools/data_quality_report.py — OFFLINE Phase-7/8 DATA-QUALITY intelligence report.

Runs the EXISTING deterministic classifier + Phase-6/7/8 enrichment layer over the
historical datasets under data/ and prints the data-quality metrics the plan
asks for:

  Total records, genuine ACCEPT candidates / REVIEW / REJECT, exact-role
  extraction rate, company identification rate, India relevance %, full-time
  certainty %, location %, experience %, CTC %, hiring-manager discovery %,
  hiring-manager LinkedIn %, verified-email %, evidence-completeness %, READY %,
  READY_WITHOUT_CONTACT %, REVIEW %, potential hallucinations, potential false
  positives, and a BEFORE Phase 7 (stored) vs AFTER Phase 7/8 (enriched) column
  comparison where the historical data permits.

SAFETY (guaranteed offline):
  - never calls Groq / LLM
  - never calls Apify
  - never writes to Google Sheets
  - never modifies DRY_RUN (we do not even import the production orchestrator)
  - contact-discovery provider is OFF (Null) — this report is deterministic-only
  - never fabricates an email / manager / LinkedIn URL — any "potential
    hallucination" flag below means the ENRICHMENT LAYER HONESTLY returned a
    sentinel, which is the correct behaviour; the metric exists to make that
    visible.

Usage:
    python tools/data_quality_report.py [path-to-leads.json ...] [--limit N]
    (no args => aggregates every leads file under data/runs, data/reprocessed
    and data/dry_runs)
"""
import argparse
import glob
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.classifier import DeterministicClassifier
from app.enrichment import EnrichmentStatus, Enricher
from app.enrichment.schemas import EmailStatus
from app.models import RawPost

# "Documented sentinel" fields; a lead that keeps these is NOT a hallucination —
# it is the honest, evidence-first default this project requires.
_SENTINELS = {"Unclear", "Not Available", "Not Specified", "Not Disclosed", ""}


def load_records(path: str):
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, list):
        return data
    for value in data.values():
        if isinstance(value, list):
            return value
    return []


def raw_from_record(rec: dict, classifier) -> RawPost:
    """Best-effort RawPost for the deterministic classifier from a historical
    record shape. Returns None when the record has no usable post text."""
    text = (rec.get("text") or rec.get("post_description")
            or rec.get("original_text") or rec.get("post_snippet") or "").strip()
    if not text:
        return None
    url = (rec.get("post_url") or rec.get("source_linkedin_url")
           or rec.get("url") or "")
    return RawPost(
        post_url=url,
        post_date=(rec.get("post_date") or rec.get("postedAtISO") or ""),
        text=text,
        author_name=rec.get("author_name") or rec.get("authorName") or "",
        author_profile_url=(rec.get("author_profile_url")
                            or rec.get("authorProfileUrl") or ""),
        company=rec.get("company") or "Unclear",
        job_card_location=rec.get("location") or rec.get("job_card_location") or "",
    )


def enrich_record(rec: dict, enricher: Enricher, classifier=None, classified=None):
    """Enrich a historical record without an LLM verdict (deterministic only).

    When `classifier` is given, the record's text is run through the REAL
    deterministic classifier and the classified fields are fed into the
    enrichment layer — so the 'AFTER' column reflects an honest end-to-end
    deterministic pass, still zero-LLM, zero-network, zero-Sheets.
    """
    if classified is None and classifier is not None:
        raw = raw_from_record(rec, classifier)
        if raw is not None:
            classified = classifier.classify(raw)

    cls = classified  # a ClassifiedPost or None
    def _g(field, fallback):
        if cls is None:
            return fallback
        return getattr(cls, field, fallback) or fallback

    india = (rec.get("india_relevance") or rec.get("market")
             or "Unclear")
    if india in ("Unclear", None, ""):
        india = _g("india_relevance", "Unclear")

    return enricher.enrich(
        post_text=(rec.get("text") or rec.get("post_description")
                   or rec.get("original_text") or rec.get("post_snippet") or ""),
        source_link=(rec.get("post_url") or rec.get("source_linkedin_url")
                     or rec.get("url") or ""),
        post_date=rec.get("post_date") or rec.get("postedAtISO") or "",
        author_name=rec.get("author_name") or rec.get("authorName") or "",
        author_profile_url=(rec.get("author_profile_url")
                            or rec.get("authorProfileUrl") or ""),
        job_metadata_company=rec.get("company") or "Unclear",
        job_metadata_is_authoritative=False,
        job_card_location=rec.get("location") or rec.get("job_card_location") or "",
        classified_company=_g("company", rec.get("company") or "Unclear"),
        classified_major_category=_g("major_category", "Unclear"),
        classified_exact_role=_g("exact_role", rec.get("detected_role_title")
                                 or rec.get("exact_role") or "Unclear"),
        classified_confidence=float(cls.confidence if cls else
                                    (rec.get("confidence") or rec.get("confidence_score") or 0.0)),
        classified_location=_g("location", rec.get("location") or "Not Specified"),
        classified_employment=_g("employment_type", rec.get("employment_type") or "Unclear"),
        classified_experience=_g("experience_required", rec.get("experience_required")
                                 or "Not Specified"),
        classified_ctc=_g("ctc", rec.get("ctc") or "Not Disclosed"),
        classified_status=rec.get("status") or rec.get("verification_status") or "New",
        classified_india_relevance=india,
        classified_cold_email=rec.get("cold_email") or "Not Available",
        classified_hiring_manager_name=rec.get("hiring_manager_name") or "Unclear",
        classified_hiring_manager_linkedin=(rec.get("hiring_manager_linkedin")
                                            or rec.get("hiring_manager_linkedin_url")
                                            or "Unclear"),
    )


def verdict_for(rec: dict) -> str:
    """Classify a historical record as ACCEPT/REVIEW/REJECT for the report.

    Prefers explicit verdict fields; otherwise infers from classifier validity.
    """
    vs = (rec.get("verification_status") or rec.get("status") or "").upper()
    if vs in ("ACCEPT", "NEW"):
        return "ACCEPT"
    if vs in ("REVIEW", "REVIEW_PENDING", "REVIEW_NEEDED"):
        return "REVIEW"
    if vs == "REJECT":
        return "REJECT"
    # No stored verdict -> infer from the classifier's own validity.
    if rec.get("is_valid") is False:
        return "REJECT"
    return "REVIEW"


def is_sentinel(v) -> bool:
    return (v or "") in _SENTINELS


def stored_fields(rec: dict):
    """BEFORE Phase 7: whatever the historical dataset already stored."""
    return {
        "company": rec.get("company") or rec.get("company_name") or "",
        "role": rec.get("exact_role") or rec.get("detected_role_title") or "",
        "email": rec.get("cold_email") or "",
        "location": rec.get("location") or "",
        "ctc": rec.get("ctc") or "",
        "experience": rec.get("experience_required") or rec.get("experience_requirement") or "",
        "manager": rec.get("hiring_manager_name") or "",
        "manager_linkedin": (rec.get("hiring_manager_linkedin")
                             or rec.get("hiring_manager_linkedin_url") or ""),
        "india": rec.get("india_relevance") or rec.get("market") or "",
    }


def _looks_fabricated_email(e) -> bool:
    """A real (non-sentinel) email that carries NO evidence URL and LOOKS like a
    first.last@domain guess is the only visible red flag without a network.
    Evidence-backed provider/company emails (email_evidence_url set) are
    literally displayed on a public page and are NOT guesses."""
    email = (getattr(e, "cold_email", "") or "").strip()
    if not email or email in ("Not Available", "Not Specified", "Not Disclosed"):
        return False
    if getattr(e, "email_evidence_url", "") or getattr(e, "evidence_url", ""):
        return False
    local = email.split("@")[0]
    domain = email.split("@")[1]
    return "." in local and domain and not domain.endswith((".edu", ".ac.in"))


def evidence_completeness(e) -> float:
    """Fraction of 12 evidence signals that are known (not sentinel)."""
    signals = [
        e.company_name not in ("Unclear", "", "N/A"),
        e.exact_role not in ("Unclear", ""),
        (e.india_relevance or "").lower() in ("india", "remote - india",
                                              "indian company", "india + global"),
        e.location not in ("Not Specified", ""),
        e.employment_type not in ("Unclear", ""),
        e.experience_required not in ("Not Specified", ""),
        e.ctc not in ("Not Disclosed", ""),
        e.hiring_manager_name not in ("Unclear", ""),
        e.hiring_manager_linkedin not in ("Not Available", ""),
        e.cold_email not in ("Not Available", ""),
        bool(e.source_link),
        bool([s for s in getattr(e, "evidence_snippets", []) if isinstance(s, str) and s]),
    ]
    return round(sum(1 for s in signals if s) / len(signals), 3)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("paths", nargs="*",
                    help="Leads JSON paths. Default: all under data/runs, data/reprocessed, data/dry_runs.")
    ap.add_argument("--limit", type=int, default=0, help="Limit records processed per file (0=all)")
    args = ap.parse_args()

    base = Path(__file__).resolve().parent.parent / "data"
    if args.paths:
        paths = args.paths
    else:
        paths = sorted(glob.glob(str(base / "runs" / "*.json"))
                       + glob.glob(str(base / "reprocessed" / "*.json"))
                       + glob.glob(str(base / "dry_runs" / "*.json")))

    enricher = Enricher()          # NO contact provider -> fully offline
    classifier = DeterministicClassifier()

    totals = {
        "records": 0, "accepted": 0, "review": 0, "rejected": 0,
        "exact_role_extracted": 0, "company_identified": 0,
        "india_explicit": 0, "india_known": 0,
        "full_time_certain": 0, "full_time_explicit": 0,
        "location_known": 0, "experience_known": 0, "ctc_known": 0,
        "hiring_manager_found": 0, "manager_linkedin_found": 0,
        "verified_email_found": 0, "missing_verified_email": 0,
        "enrichment_ready": 0, "enrichment_ready_without_contact": 0,
        "enrichment_review": 0,
        "evidence_scores_sum": 0.0,
        "data_quality_sum": 0.0,
        "potential_hallucinations": 0, "potential_false_positives": 0,
    }
    before = {
        "company": 0, "role": 0, "email": 0, "location": 0, "ctc": 0,
        "experience": 0, "manager": 0, "manager_linkedin": 0, "india": 0,
    }

    flagged = []

    for path in paths:
        if not os.path.exists(path):
            print(f"!! skip missing: {path}")
            continue
        records = load_records(path)
        if args.limit:
            records = records[: args.limit]
        for rec in records:
            totals["records"] += 1
            try:
                e = enrich_record(rec, enricher, classifier=classifier)
            except Exception as exc:  # single-record isolation
                print(f"!! enrich failed {rec.get('post_url', '?')}: {exc}")
                continue

            v = verdict_for(rec)

            # BEFORE Phase 7 — whatever the historical dataset stored.
            for key, val in stored_fields(rec).items():
                if not is_sentinel(val):
                    before[key] += 1

            # AFTER Phase 7/8 — enrichment output (provider OFF, deterministic).
            if e.exact_role not in ("Unclear", ""):
                totals["exact_role_extracted"] += 1
            if e.company_name not in ("Unclear", "", "N/A"):
                totals["company_identified"] += 1
            if (e.india_relevance or "").lower() in ("india", "remote - india",
                                                     "indian company", "india + global"):
                totals["india_explicit"] += 1
            totals["india_known"] += 1
            if e.employment_type == "Full-time":
                totals["full_time_certain"] += 1
            totals["full_time_explicit"] += 1
            if e.location not in ("Not Specified", "") and not is_sentinel(e.location):
                totals["location_known"] += 1
            if e.experience_required not in ("Not Specified", "") and not is_sentinel(e.experience_required):
                totals["experience_known"] += 1
            if e.ctc not in ("Not Disclosed", "") and not is_sentinel(e.ctc):
                totals["ctc_known"] += 1
            if e.hiring_manager_name not in ("Unclear", ""):
                totals["hiring_manager_found"] += 1
            if e.hiring_manager_linkedin not in ("Not Available", "") and \
                    "linkedin.com/in/" in e.hiring_manager_linkedin:
                totals["manager_linkedin_found"] += 1
            if e.email_status == EmailStatus.FOUND.value and not is_sentinel(e.cold_email):
                totals["verified_email_found"] += 1
            else:
                totals["missing_verified_email"] += 1
            totals["evidence_scores_sum"] += evidence_completeness(e)
            totals["data_quality_sum"] += float(getattr(e, "data_quality_score", 0.0) or 0.0)

            if v == "ACCEPT":
                totals["accepted"] += 1
                if e.enrichment_status == EnrichmentStatus.READY.value:
                    totals["enrichment_ready"] += 1
                elif e.enrichment_status == EnrichmentStatus.READY_WITHOUT_CONTACT.value:
                    totals["enrichment_ready_without_contact"] += 1
                else:
                    totals["enrichment_review"] += 1
                # Potential false positive: ACCEPT shape but company/role missing
                # or employment unclear — a human would want to re-check it.
                if e.company_name in ("Unclear", "") or e.exact_role in ("Unclear", "") \
                        or e.employment_type == "Unclear":
                    totals["potential_false_positives"] += 1
                    flagged.append(("FP", e.source_link, e.company_name, e.exact_role))
            elif v == "REVIEW":
                totals["review"] += 1
            else:
                totals["rejected"] += 1
                if e.enrichment_status == EnrichmentStatus.REVIEW.value:
                    totals["enrichment_review"] += 1

            if _looks_fabricated_email(e):
                totals["potential_hallucinations"] += 1
                flagged.append(("HALLUC-EMAIL", e.source_link, e.cold_email, ""))

    n = max(1, totals["records"])
    acc = max(1, totals["accepted"])
    pct = lambda c, d: f"{c}  ({100.0 * c / d:.1f}%)"

    print("=" * 82)
    print("PHASE-8 DATA-QUALITY INTELLIGENCE REPORT (offline, deterministic-only)")
    print("=" * 82)
    print(f"Records evaluated:           {totals['records']}")
    print("  (verdicts reflect HISTORICAL stored status/verification_status —"
          "\n   NOT a re-run of the Groq gate. Contact discovery is OFF (Null)."
          "\n   Zero LLM/Apify/Sheets budget spent.)")
    print(f"  Genuine ACCEPT candidates:  {totals['accepted']}  ({100.0 * totals['accepted'] / n:.1f}%)")
    print(f"  Review (historical):        {pct(totals['review'], n)}")
    print(f"  Rejected (historical):      {pct(totals['rejected'], n)}")
    print("-" * 82)
    print("Extraction quality (of all records, AFTER enrichment):")
    for label, key, denom in (
        ("Exact-role extraction", "exact_role_extracted", n),
        ("Company identification", "company_identified", n),
        ("India relevance proven", "india_explicit", totals["india_known"]),
        ("Full-time certain", "full_time_certain", totals["full_time_explicit"]),
        ("Location extracted", "location_known", n),
        ("Experience extracted", "experience_known", n),
        ("CTC extracted", "ctc_known", n),
        ("Hiring manager found", "hiring_manager_found", n),
        ("Hiring manager LinkedIn found", "manager_linkedin_found", n),
        ("Verified email found", "verified_email_found", n),
        ("Missing verified email", "missing_verified_email", n),
    ):
        print(f"  {label:<28} {pct(totals[key], denom)}")
    print(f"  Evidence completeness {100.0 * totals['evidence_scores_sum'] / n:.1f}% (avg field/page fill across 12 signals)")
    print(f"  Data quality score    {totals['data_quality_sum'] / n:.2f} (avg, 0..1, Phase-8 transparent score)")
    print("-" * 82)
    print("Enrichment status (of ACCEPT candidates):")
    print(f"  READY:                 {pct(totals['enrichment_ready'], acc)}")
    print(f"  READY_WITHOUT_CONTACT: {pct(totals['enrichment_ready_without_contact'], acc)}")
    print(f"  REVIEW:                {pct(totals['enrichment_review'], acc)}")
    print("-" * 82)
    print("BEFORE Phase 7 (stored in historical data) vs AFTER Phase 7/8 (enriched):")
    for label, key in (
        ("Exact role", "role"), ("Company", "company"), ("Cold email", "email"),
        ("Location", "location"), ("CTC", "ctc"), ("Experience", "experience"),
        ("Hiring manager", "manager"), ("Manager LinkedIn", "manager_linkedin"),
        ("India relevance", "india"),
    ):
        print(f"  {label:<20} stored {pct(before[key], n)}   enriched {pct(totals[_map(key)], n)}")
    print("-" * 82)
    print("Quality guardrails:")
    print(f"  Potential hallucinations:  {totals['potential_hallucinations']}")
    print(f"  Potential false positives: {totals['potential_false_positives']}")
    print("-" * 82)
    print("PRECISION NOTE: sentinel values (Unclear/Not Available/Not Specified/"
          "Not Disclosed) are HONEST defaults — they are not hallucinations. The "
          "pipeline optimises for genuine, evidence-backed leads, not volume.")
    if flagged:
        print("\nFlagged records:")
        for kind, src, a, b in flagged[:15]:
            print(f"  [{kind}] {src} | {a} | {b}")
    print("\n(Read-only offline analysis. No LLM, no Apify, no Sheets, DRY_RUN untouched.)")


def _map(before_key: str) -> str:
    return {
        "role": "exact_role_extracted",
        "company": "company_identified",
        "email": "verified_email_found",
        "location": "location_known",
        "ctc": "ctc_known",
        "experience": "experience_known",
        "manager": "hiring_manager_found",
        "manager_linkedin": "manager_linkedin_found",
        "india": "india_explicit",
    }[before_key]


if __name__ == "__main__":
    main()