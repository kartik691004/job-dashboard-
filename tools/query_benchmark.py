"""
tools/query_benchmark.py

Query-recall benchmark for the LinkedIn FO/CoS hiring pipeline.

PURPOSE
  Measure SEARCH RECALL only. The verifier / classifier / gates are NEVER
  loosened here; this tool merely reports how many genuine FO/CoS vacancy
  candidates each candidate query would surface, using the EXISTING
  DeterministicClassifier for the "deterministic candidate" count.

MODES
  dry   : offline. Analyse a local JSON of scraped posts (no Apify, no Sheets).
          Used for inspection + CI tests.
  live  : ONE small controlled Apify run with a low max_posts limit. Results
          are written to data/benchmark/ only (never Google Sheets).

The live mode deliberately issues a SINGLE actor run with all candidate
queries (the same pattern production uses) to keep cost/time small, then
attributes each returned post to the queries it satisfies via
query_strategy.post_matches_query. "matched_posts" is the per-query proxy for
"posts returned".
"""
import argparse
import json
import os
import sys
from datetime import datetime, timezone
from typing import Any, Dict, List

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from app.classifier import DeterministicClassifier
from app.models import RawPost
from app.config import APIFY_API_TOKEN
from app.sources.datadoping_source import DataDopingSource
from tools.query_strategy import (
    build_query_matrix, post_matches_query,
    ROLE_TERMS, HIRING_TERMS, INDIA_TERMS,
)

OUT_DIR = os.path.join(ROOT, "data", "benchmark")


def _norm(s):
    return (s or "").lower().replace("\u2019", "'").replace("\u2018", "'")


def build_raw_post(rec: Dict[str, Any]) -> RawPost:
    """Map a flat scraped record (e.g. recovered apify_posts JSON) to RawPost."""
    def g(*keys, default=""):
        for k in keys:
            if k in rec and rec[k] not in (None, ""):
                return rec[k]
        return default
    text = g("text", "postText", "content", "body") or g("authorName", "title")
    return RawPost(
        post_url=g("url", "postUrl", "post_url") or f"unknown://{abs(hash(json.dumps(rec, default=str)))}",
        post_date=g("postedAt", "post_date", "date") or "",
        text=str(text),
        author_name=g("authorName", "author", "author_name") or "Unclear",
        author_profile_url=g("authorProfileUrl", "authorUrl", "author_profile_url") or "",
        company=g("company") or "Unclear",
    )


def analyze(posts: List[RawPost], matrix=None) -> Dict[str, Any]:
    """Compute per-query recall metrics over a list of RawPosts. Pure/offline."""
    matrix = matrix or build_query_matrix()
    classifier = DeterministicClassifier()

    total = len(posts)
    unique = len({p.post_url for p in posts})

    per_query = []
    for q in matrix:
        matched = [p for p in posts if post_matches_query(p.text, q["query"])]
        matched_urls = {p.post_url for p in matched}
        candidates = [p for p in matched if classifier.classify(p).is_valid]
        hiring_hits = sum(
            1 for p in matched if any(h in _norm(p.text) for h in HIRING_TERMS)
        )
        india_hits = sum(
            1 for p in matched if any(i in _norm(p.text) for i in INDIA_TERMS)
        )
        rejected = len(matched) - len(candidates)
        rate = (len(candidates) / len(matched)) if matched else 0.0
        per_query.append({
            "id": q["id"],
            "query": q["query"],
            "role": q["role"],
            "matched_posts": len(matched),
            "unique_matched": len(matched_urls),
            "role_keyword_hits": len(matched),  # post_matches_query already requires a role term
            "hiring_signal_hits": hiring_hits,
            "india_evidence": india_hits,
            "deterministic_candidates": len(candidates),
            "rejected": rejected,
            "candidate_rate": round(rate, 3),
        })

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "mode": "analyze",
        "total_posts": total,
        "unique_posts": unique,
        "query_count": len(matrix),
        "per_query": per_query,
    }


def run_dry(sample_path: str) -> Dict[str, Any]:
    with open(sample_path, "r", encoding="utf-8") as f:
        records = json.load(f)
    posts = [build_raw_post(r) for r in records]
    result = analyze(posts)
    result["mode"] = "dry"
    result["sample_path"] = sample_path
    return result


def run_live(limit: int) -> Dict[str, Any]:
    """ONE small controlled live Apify run over the candidate query matrix."""
    matrix = build_query_matrix()
    queries = [q["query"] for q in matrix]
    src = DataDopingSource(APIFY_API_TOKEN)
    posts = src.search_all(queries, max_posts=limit)
    result = analyze(posts)
    result["mode"] = "live"
    result["apify_limit_per_keyword"] = limit
    result["raw_post_count"] = len(posts)
    # Persist the raw scrape for reproducibility / re-analysis.
    os.makedirs(OUT_DIR, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H%M%SZ")
    raw_path = os.path.join(OUT_DIR, f"benchmark_raw_{stamp}.json")
    with open(raw_path, "w", encoding="utf-8") as f:
        json.dump([p.model_dump() for p in posts], f, indent=2)
    result["raw_path"] = raw_path
    return result


def print_report(result: Dict[str, Any]) -> None:
    print("=" * 78)
    print(f"QUERY BENCHMARK  |  mode={result.get('mode')}  "
          f"total_posts={result.get('total_posts')}  "
          f"unique={result.get('unique_posts')}  "
          f"queries={result.get('query_count')}")
    print("=" * 78)
    hdr = (f"{'Q':<3} {'query':<40} {'mtch':>4} {'hir':>4} "
           f"{'ind':>4} {'cand':>4} {'rej':>4} {'rate':>5}")
    print(hdr)
    print("-" * 78)
    tot_cand = 0
    for r in result["per_query"]:
        tot_cand += r["deterministic_candidates"]
        print(f"{r['id']:<3} {r['query'][:38]:<40} {r['matched_posts']:>4} "
              f"{r['hiring_signal_hits']:>4} {r['india_evidence']:>4} "
              f"{r['deterministic_candidates']:>4} {r['rejected']:>4} "
              f"{r['candidate_rate']:>5}")
    print("-" * 78)
    print(f"TOTAL deterministic candidates across matrix: {tot_cand}")
    print("=" * 78)


def main():
    ap = argparse.ArgumentParser(description="FO/CoS query-recall benchmark")
    ap.add_argument("--mode", choices=["dry", "live"], default="dry")
    ap.add_argument("--sample",
                    default=os.path.join(ROOT, "data", "recovered",
                                         "apify_posts_2026-08-17_18.json"))
    ap.add_argument("--limit", type=int, default=10,
                    help="max_posts per keyword for the live run (actor floors at 10)")
    ap.add_argument("--out", default=None, help="write JSON report to this path")
    args = ap.parse_args()

    if args.mode == "live":
        result = run_live(args.limit)
    else:
        result = run_dry(args.sample)

    print_report(result)

    out_path = args.out or os.path.join(
        OUT_DIR,
        f"benchmark_{datetime.now(timezone.utc).strftime('%Y-%m-%dT%H%M%SZ')}.json",
    )
    os.makedirs(OUT_DIR, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2)
    print(f"Report written: {out_path}")


if __name__ == "__main__":
    main()
