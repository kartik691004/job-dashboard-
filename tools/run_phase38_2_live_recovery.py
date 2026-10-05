"""run_phase38_2_live_recovery.py — Phase 38.2: live Tier-A HM recovery measurement.

ONE bounded Apify run (charge-capped), deterministic classification only.
NO Google Sheets writes. NO LLM calls. NO gate changes.

Measures how many fresh real posts yield an explicit-context hiring manager
(Tier A: "reports to / working with / reach out to / contact person /
Hiring Manager: <Name>") — the Phase-38 capability — vs the pre-existing
author-first-person tier.
"""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx
from dotenv import load_dotenv

from app.config import SEARCH_QUERIES
from app.classifier import DeterministicClassifier
from app.models import RawPost
from app.sources.datadoping_source import ACTOR_ID, DataDopingSource

OUT = Path("data/validation/phase38_2_live_hm_recovery")
OUT.mkdir(parents=True, exist_ok=True)
CHARGE_CAP_USD = 1.50          # hard platform-level cap for this single run
MAX_POSTS_PER_QUERY = 10       # actor floors at 10
SENTINELS = {"", "unclear", "not available", "not found", "unknown",
             "n/a", "na", "none", "null", "-", "ambiguous"}
CUE_RE = __import__("app.extraction", fromlist=["_HM_REPORTS_TO_RE"])._HM_REPORTS_TO_RE


def ver(v):
    return str(v or "").strip().lower() not in SENTINELS


def tier_of(cp):
    ev = getattr(cp, "hiring_manager_evidence", "") or ""
    if not ver(cp.hiring_manager_name):
        return "none"
    for pre, t in (("author states hiring side", "author_first_person"),
                   ("recruiter signature", "recruiter_signature"),
                   ("named in post", "named_founder")):
        if ev.startswith(pre):
            return t
    return "tier_a_contextual"


def monthly_usage(token):
    r = httpx.get("https://api.apify.com/v2/users/me/usage/monthly",
                  params={"token": token, "date": datetime.now(timezone.utc).strftime("%Y-%m")},
                  timeout=30)
    return r.json().get("data", {}).get("monthlyServiceUsageUsd", 0.0) if r.status_code == 200 else None


def main():
    load_dotenv(".env")
    import os
    token = os.getenv("APIFY_API_TOKEN")
    if not token:
        print("FATAL: APIFY_API_TOKEN missing"); sys.exit(1)

    usage_before = monthly_usage(token)
    print(f"usage before: ${usage_before}", flush=True)

    src = DataDopingSource(token)
    run_input = {"keywords": list(SEARCH_QUERIES), "max_posts": MAX_POSTS_PER_QUERY,
                 "sort_by": "date_posted", "date_filter": "past-24h"}
    print(f"starting ONE actor run: {ACTOR_ID} | {len(run_input['keywords'])} queries "
          f"| max_posts={MAX_POSTS_PER_QUERY}/query | charge cap ${CHARGE_CAP_USD}", flush=True)

    run = src.client.actor(ACTOR_ID).call(run_input=run_input,
                                          max_total_charge_usd=CHARGE_CAP_USD)

    def run_field(obj, key):
        """apify_client returns Run as dict-like in some versions and
        attribute-like in others — support both."""
        if isinstance(obj, dict):
            return obj.get(key)
        return getattr(obj, key, None)

    run_id = run_field(run, "id")
    status = run_field(run, "status")
    stats = run_field(run, "stats") or {}
    print(f"run {run_id} status={status}", flush=True)

    posts, seen, errors = [], set(), []
    dataset_id = run_field(run, "defaultDatasetId")
    for item in src.client.dataset(dataset_id).iterate_items():
        if item.get("error"):
            errors.append(str(item.get("error"))[:200]); continue
        rp = src._to_raw_post(item)
        if rp is None or rp.post_url in seen:
            continue
        seen.add(rp.post_url)
        posts.append(rp)

    usage_after = monthly_usage(token)
    spend = (usage_after - usage_before) if (usage_before is not None and usage_after is not None) else None
    (OUT / "raw_posts.jsonl").write_text(
        "\n".join(json.dumps(p.model_dump(), ensure_ascii=False) for p in posts) + "\n", encoding="utf-8")
    run_record = {"run_id": run_id, "status": status, "actor": ACTOR_ID,
                  "queries": len(run_input["keywords"]), "max_posts_per_query": MAX_POSTS_PER_QUERY,
                  "charge_cap_usd": CHARGE_CAP_USD, "raw_items": len(posts), "errors": errors[:10],
                  "usage_before_usd": usage_before, "usage_after_usd": usage_after,
                  "spend_usd": round(spend, 4) if spend is not None else None,
                  "run_stats": {k: stats.get(k) for k in ("totalChargeUsd", "computeUnits") if k in stats},
                  "started_at": datetime.now(timezone.utc).isoformat()}
    (OUT / "apify_run_record.json").write_text(json.dumps(run_record, indent=2), encoding="utf-8")
    print(f"raw posts: {len(posts)} | spend: ${spend}", flush=True)

    # ── deterministic classification + Tier-A measurement ──
    import csv
    import re
    c = DeterministicClassifier()
    rows, m = [], {"total": len(posts), "cue_posts": 0, "valid": 0, "tierA_valid_recoveries": 0,
                   "author_first_person": 0, "recruiter_signature": 0, "named_founder": 0,
                   "url_attached": 0, "invalid_posts": 0, "false_pos": 0}
    for i, rp in enumerate(posts):
        if CUE_RE.search(rp.text or ""):
            m["cue_posts"] += 1
        cp = c.classify(rp)
        t = tier_of(cp)
        if cp.major_category == "N/A":
            m["invalid_posts"] += 1
        else:
            m["valid"] += 1
        if t == "tier_a_contextual":
            m["tierA_valid_recoveries"] += 1
            if cp.hiring_manager_name not in (rp.text or ""):
                m["false_pos"] += 1
        elif t == "author_first_person":
            m["author_first_person"] += 1
            if cp.hiring_manager_name != (rp.author_name or ""):
                m["false_pos"] += 1
        elif t == "recruiter_signature":
            m["recruiter_signature"] += 1
        elif t == "named_founder":
            m["named_founder"] += 1
        if t != "none" and ver(cp.hiring_manager_linkedin):
            m["url_attached"] += 1
        if t == "tier_a_contextual" or (t != "none" and t != "author_first_person"):
            m2 = CUE_RE.search(rp.text or "")
            cue = m2.group(0)[:len(m2.group(0)) - len(m2.group(1))].strip() if m2 else ""
            sent = ""
            for s in re.split(r"(?<=[.!?])\s+|\n+", rp.text or ""):
                if cp.hiring_manager_name in s:
                    sent = re.sub(r"\s+", " ", s).strip()[:220]; break
            rows.append({"activity_id": (re.search(r"activity-(\d+)", rp.post_url) or [None, ""])[1],
                         "post_url": rp.post_url[:100], "exact_role": cp.exact_role,
                         "company": cp.company_name, "hm_name": cp.hiring_manager_name,
                         "cue_phrase": cue[:40], "evidence_span": sent,
                         "resolution_tier": t, "post_valid": "no" if cp.major_category == "N/A" else "yes",
                         "linkedin_url": cp.hiring_manager_linkedin if ver(cp.hiring_manager_linkedin) else ""})
    with open(OUT / "live_tierA_recovery.csv", "w", newline="", encoding="utf-8") as f:
        if rows:
            w = csv.DictWriter(f, fieldnames=list(rows[0].keys())); w.writeheader(); w.writerows(rows)
        else:
            f.write("activity_id,post_url,exact_role,company,hm_name,cue_phrase,evidence_span,"
                    "resolution_tier,post_valid,linkedin_url\n")
    (OUT / "metrics.json").write_text(json.dumps(m, indent=2), encoding="utf-8")
    print("metrics:", json.dumps(m, indent=2), flush=True)


if __name__ == "__main__":
    main()
