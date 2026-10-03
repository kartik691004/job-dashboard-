"""PHASE 37 — data artifact generator (offline, no Apify, no LLM calls).

Writes the two Phase37 artifacts that the resume path does not produce:

  * query_yield.csv  — per-query yield for the ONE actor run (CIo4E4s4SddaAath2).
    The executed set was the FULL 48-query list (pre-freeze-guard runner bug,
    documented as legacy). The platform charge cap (max_total_charge_usd=0.0646)
    aborted the run after 9 queries had logged "Successfully scraped" lines;
    posts persisted to the dataset: 44. Remaining queries: NOT_EXECUTED_CAP_ABORT.
    The frozen 7-query set (indices 28,30,46,44 / 33 / 0,1) is marked FROZEN_SET
    so the artifact shows which of them never ran.

  * da_results.csv   — Data-Analyst-track accounting per ClassifiedPost.major_category
    over the deterministic funnel (raw -> dedup -> classifier -> candidates),
    with each candidate's verdict disposition from the cache-first ledger.

No secrets. Deterministic. Safe to re-run (idempotent overwrite).
"""

from __future__ import annotations

import csv
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from app.classifier import DeterministicClassifier  # noqa: E402
from app.config import SEARCH_QUERIES  # noqa: E402
from app.models import RawPost  # noqa: E402

OUT_DIR = REPO / "data" / "validation" / "phase37_final_current_key_run"
RAW_POSTS = OUT_DIR / "raw_posts.jsonl"
VERDICTS = OUT_DIR / "verdicts.jsonl"
RUN_LOG = REPO / "logs" / "phase37_final_run.log"
RUN_ID = "CIo4E4s4SddaAath2"
FROZEN_INDICES = [28, 30, 46, 44, 33, 0, 1]


def load_raw_posts() -> list[RawPost]:
    posts: list[RawPost] = []
    with RAW_POSTS.open(encoding="utf-8") as f:
        for line in f:
            if line.strip():
                posts.append(RawPost.model_validate_json(line))
    return posts


def parse_query_yield_from_log() -> dict[int, int]:
    """Map actor-log 'Successfully scraped N posts for \"Q\"' lines to query indices."""
    text = RUN_LOG.read_text(encoding="utf-8", errors="replace")
    queries = [q[0] if isinstance(q, (list, tuple)) else q for q in SEARCH_QUERIES]
    by_text: dict[str, int] = {}
    for i, q in enumerate(queries):
        by_text.setdefault(q, i)  # first index when duplicates exist
    yields: dict[int, int] = {}
    # log lines carry ANSI color codes; query text holds embedded quotes and
    # ends at EOL, so capture everything between 'for ' and the line break
    yield_pat = re.compile(
        r"runId:" + RUN_ID
        + r".*?Successfully scraped (\d+) posts for (.*?)\r?\n"
    )
    for m in yield_pat.finditer(text):
        n, qtext = int(m.group(1)), m.group(2)
        idx = by_text.get(qtext)
        if idx is None:
            raise SystemExit(f"log query text not found in SEARCH_QUERIES: {qtext!r}")
        yields[idx] = yields.get(idx, 0) + n  # same query never repeats; sum is safe
    return yields


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    # ---------- query_yield.csv ----------
    queries = [q[0] if isinstance(q, (list, tuple)) else q for q in SEARCH_QUERIES]
    executed = parse_query_yield_from_log()
    frozen = set(FROZEN_INDICES)
    with (OUT_DIR / "query_yield.csv").open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["query_index", "query", "status", "posts_scraped",
                    "frozen_set", "note"])
        for i, q in enumerate(queries):
            if i in executed:
                status, n = "EXECUTED", executed[i]
                note = ""
            else:
                status, n = "NOT_EXECUTED_CAP_ABORT", 0
                note = "cap abort at max_total_charge_usd=0.0646"
            w.writerow([i, q, status, n, "YES" if i in frozen else "NO", note])
        total_logged = sum(executed.values())
        w.writerow(["", "TOTAL logged by actor (9 queries executed)", "SUM",
                    total_logged, "", "44 posts persisted to dataset before abort"])

    # ---------- da_results.csv ----------
    posts = load_raw_posts()
    seen: set = set()
    unique_posts = []
    for p in posts:
        if p.post_url not in seen:
            seen.add(p.post_url)
            unique_posts.append(p)
    classifier = DeterministicClassifier()
    verdicts: dict[str, dict] = {}
    with VERDICTS.open(encoding="utf-8") as f:
        for line in f:
            if line.strip():
                rec = json.loads(line)
                verdicts[rec["post_url"]] = rec

    # production Source Links (column index 7) for write attribution.
    # Plain gspread client (same auth path as app/main.py); READ of the two
    # production tabs only — no writes.
    prod_urls: set[str] = set()
    try:
        import gspread
        from app.config import GOOGLE_SHEETS_CREDENTIALS, GOOGLE_SHEETS_ID
        if not GOOGLE_SHEETS_ID:
            raise RuntimeError("SHEET_ID not set")
        gc = gspread.service_account(filename=GOOGLE_SHEETS_CREDENTIALS)
        sh = gc.open_by_key(GOOGLE_SHEETS_ID)
        for tab in ("LinkedIn Hiring Leads", "Data Analyst & Data Scientist"):
            ws = sh.worksheet(tab)
            for row in ws.get_all_values()[1:]:
                if len(row) > 7 and row[7]:
                    prod_urls.add(row[7])
    except Exception as e:  # offline fallback: no Sheet read
        print(f"note: production URL read skipped ({e})")

    # gate-blocked URLs (pre-write HOLD decision by production gate)
    gate_blocked_urls: set[str] = set()
    gate_blocked_path = OUT_DIR / "gate_blocked.csv"
    if gate_blocked_path.exists():
        with gate_blocked_path.open(encoding="utf-8", newline="") as f:
            for row in csv.DictReader(f):
                u = (row.get("source_url") or "").strip().strip('"')
                if u:
                    gate_blocked_urls.add(u)

    # category -> candidate dispositions
    stats: dict[str, dict[str, int]] = defaultdict(
        lambda: {"candidates": 0, "excluded": 0, "written": 0, "accept": 0,
                 "gate_blocked": 0, "review": 0, "reject": 0, "unavailable": 0,
                 "unevaluated": 0}
    )
    for p in unique_posts:
        try:
            c = classifier.classify(p)
        except Exception:
            stats["(classifier_error)"]["excluded"] += 1
            continue
        if not c.is_valid:
            stats[c.major_category or "(none)"]["excluded"] += 1
            continue
        cat = c.major_category or "(none)"
        stats[cat]["candidates"] += 1
        rec = verdicts.get(p.post_url)
        if p.post_url in prod_urls:
            stats[cat]["written"] += 1
        elif rec is None:
            stats[cat]["unevaluated"] += 1
        elif rec["decision"] == "ACCEPT":
            stats[cat]["accept"] += 1
            if p.post_url in gate_blocked_urls:
                stats[cat]["gate_blocked"] += 1
        elif rec["decision"] == "REVIEW":
            stats[cat]["review"] += 1
        elif rec["decision"] == "REJECT":
            stats[cat]["reject"] += 1
        elif rec["decision"] == "UNAVAILABLE":
            stats[cat]["unavailable"] += 1

    with (OUT_DIR / "da_results.csv").open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["major_category", "candidates", "excluded_by_classifier",
                    "written_production", "accept", "accept_gate_blocked",
                    "review", "reject", "unavailable", "unevaluated"])
        for cat in sorted(stats):
            s = stats[cat]
            w.writerow([cat, s["candidates"], s["excluded"], s["written"],
                        s["accept"], s["gate_blocked"], s["review"], s["reject"],
                        s["unavailable"], s["unevaluated"]])
        tot = {k: sum(v[k] for v in stats.values())
               for k in ("candidates", "excluded", "written", "accept",
                         "gate_blocked", "review", "reject", "unavailable",
                         "unevaluated")}
        w.writerow(["TOTAL", tot["candidates"], tot["excluded"], tot["written"],
                    tot["accept"], tot["gate_blocked"], tot["review"],
                    tot["reject"], tot["unavailable"], tot["unevaluated"]])
        da = stats.get("Data Analyst", {})
        ds = stats.get("Data Scientist", {})
        w.writerow(["DA_CANDIDATES", da.get("candidates", 0), "", "", "", "", "",
                    "", "", ""])
        w.writerow(["DS_CANDIDATES", ds.get("candidates", 0), "", "", "", "", "",
                    "", "", ""])

    print("query_yield.csv: executed queries:", sorted(executed),
          "logged total:", total_logged, "prod_urls read:", len(prod_urls))
    print("da_results.csv categories:", dict(stats))
    print("OK")


if __name__ == "__main__":
    main()
