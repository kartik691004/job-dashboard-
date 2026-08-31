"""
tools/phase10_historical_reprocess.py  (READ-ONLY)

Phase 10: Re-run the OLD Google Sheet leads (backup pre-migration) through the
CURRENT deterministic classifier ONLY (offline — no Groq, no Sheets, no Apify)
and compare BEFORE (Phase-9E baseline deterministic outcome) -> AFTER (Phase-10,
with the explicit-vacancy-label acceptance fix).

Why deterministic-only: the Phase-10 change acts exclusively in the classifier's
Stage-3 hiring-intent gate. Running the full pipeline with the live Groq verifier
is slow/flaky and not needed to isolate the effect of the label-signal fix on how
many previously-rejected records now survive the deterministic stage.

Guarantees (safety contract):
  * NO SheetsWriter instantiated; no auth; no writes.
  * NO Apify scrape; reads full text already on disk only.
  * No fabrication: a lead with no on-disk full text -> NOT_REPROCESSABLE.
  * Offline: deterministic classifier only; zero Groq/network calls.

Output: data/validation/phase10_historical_reprocess.json
  - per lead: source URL, BEFORE (Phase-9E outcome + stored fields), AFTER
    (Phase-10 deterministic outcome + current exact_role/company), recovery flag.
  - newly_recovered: leads that were DETERMINISTIC_REJECT in Phase-9E and now
    pass the deterministic classifier (the records the label fix recovers).
"""
import os
import sys
import json
from collections import Counter

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app.classifier import DeterministicClassifier
from app.models import RawPost

BACKUP = os.path.join(ROOT, "data", "analysis", "jobdashboard_linkedin_leads_backup_pre_migration.json")
PHASE9E = os.path.join(ROOT, "data", "validation", "historical_before_after", "historical_before_after_phase9E.json")
TEXT_SOURCES = [
    os.path.join(ROOT, "data", "runs", "2026-08-23T121618Z_leads.json"),
    os.path.join(ROOT, "data", "runs", "2026-08-23T121621Z_leads.json"),
    os.path.join(ROOT, "data", "dry_runs", "2026-08-23T100816Z_leads.json"),
    os.path.join(ROOT, "data", "dry_runs", "2026-08-23_classified_leads.json"),
    os.path.join(ROOT, "data", "analysis", "2026-08-23_geo_projection.json"),
]
OUT = os.path.join(ROOT, "data", "validation", "phase10_historical_reprocess.json")


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


def load_phase9e_outcomes():
    """Map Phase-9E url -> deterministic outcome + stored fields."""
    doc = json.load(open(PHASE9E, encoding="utf-8"))
    m = {}
    for section in ("accepts", "reviews", "rejects", "not_reprocessable"):
        for item in doc.get(section, []):
            url = item.get("source_linkedin_url", "")
            outcome = item.get("outcome")
            after = item.get("after") or {}
            if url:
                m[url] = {
                    "before_outcome": outcome,
                    "before_status": (item.get("before", {}) or {}).get("status", ""),
                    "before_role": (item.get("before", {}) or {}).get("detected_role_title", ""),
                    "before_company": (item.get("before", {}) or {}).get("company", ""),
                    "phase9e_exact_role": after.get("exact_role") or (item.get("deterministic") or {}).get("exact_role", ""),
                    "phase9e_company": after.get("company", ""),
                }
    return m


def run():
    cls = DeterministicClassifier()
    text_index = load_full_text()
    leads = load_backup()
    phase9e = load_phase9e_outcomes()

    results = []
    newly_recovered = []

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
            },
        }
        p9 = phase9e.get(url, {})
        base["phase9e"] = p9

        if src is None:
            base["after_outcome"] = "NOT_REPROCESSABLE"
            base["note"] = "No full post text on disk; would require a new scrape (disallowed)."
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

        c = cls.classify(rp)
        base["current"] = {
            "exact_role": c.exact_role,
            "major_category": c.major_category,
            "company_name": c.company_name,
            "confidence": c.confidence,
            "reason": c.classification_reason,
            "hiring_intent_signals": c.hiring_intent_signals,
        }

        if not c.is_valid:
            base["after_outcome"] = "DETERMINISTIC_REJECT"
            base["after_reason"] = c.classification_reason
        else:
            base["after_outcome"] = "DETERMINISTIC_PASS"

        # True recovery = was DETERMINISTIC_REJECT (deterministic stage) in
        # Phase-9E but now passes deterministic. LLM-REJECT ("REJECT") leads
        # already PASSED deterministic in Phase-9E too, so the label fix does
        # not "recover" them at the deterministic stage — they merely remain
        # deterministic-passes (their final status still hinges on the LLM gate).
        p9_outcome = p9.get("before_outcome", "")
        is_now_pass = c.is_valid
        if p9_outcome == "DETERMINISTIC_REJECT" and is_now_pass:
            base["recovered"] = True
            newly_recovered.append(base)
        elif p9_outcome == "REJECT" and is_now_pass:
            base["was_llm_reject_still_det_pass"] = True

        results.append(base)

    outcome_counts = Counter(r["after_outcome"] for r in results)
    recovered_count = len(newly_recovered)
    llm_reject_still_pass = [r for r in results if r.get("was_llm_reject_still_det_pass")]

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    doc = {
        "generated_at": __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(),
        "description": (
            "Phase-10 offline deterministic re-audit. BEFORE = Phase-9E baseline "
            "(deterministic outcome from historical_before_after_phase9E.json). "
            "AFTER = current classifier after the explicit-vacancy-label acceptance fix. "
            "Deterministic-only (no Groq LLM, no Sheets, no Apify). The final ACCEPT/"
            "REVIEW/REJECT status of newly-recovered records requires the LLM verifier "
            "(forwarded separately, not run here to keep this audit offline)."
        ),
        "summary": {
            "backup_leads": len(leads),
            "on_disk_text_coverage": len(text_index),
            "phase10_after_outcome_counts": dict(outcome_counts),
            "newly_recovered_deterministic": recovered_count,
            "llm_reject_still_det_pass": len(llm_reject_still_pass),
        },
        "newly_recovered": newly_recovered,
        "llm_reject_still_det_pass": llm_reject_still_pass,
        "results": results,
    }

    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=2)

    print("=== PHASE-10 HISTORICAL DETERMINISTIC REPROCESS ===")
    print("Backup leads:", len(leads))
    print("Phase-10 deterministic outcomes:", dict(outcome_counts))
    print("Newly recovered at deterministic stage (Phase-9E DET_REJECT -> now pass):", recovered_count)
    print("Phase-9E LLM-REJECT that still pass deterministic (not 'recovered' by fix):", len(llm_reject_still_pass))
    print("\nNewly recovered records (must be 0 if the fix changed nothing):")
    for r in newly_recovered:
        cur = r.get("current", {})
        print(f"  - {r['source_linkedin_url']}")
        print(f"      before(status={r['phase9e'].get('before_status','')}, role={r['phase9e'].get('before_role','')})")
        print(f"      now(exact_role={cur.get('exact_role')!r}, company={cur.get('company_name')!r}, cat={cur.get('major_category')!r})")
        print(f"      reason={cur.get('reason')!r}")
    print("\nWritten to:", OUT)
    return doc


if __name__ == "__main__":
    run()
