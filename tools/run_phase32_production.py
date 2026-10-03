"""
Phase 32 — FINAL SUPERVISED PRODUCTION SCRAPE (one 48-query cycle).

Established machinery only, composed per the Phase 32 spec:
  * run_daily.py invocation pattern: os.environ["DRY_RUN"]="false" BEFORE
    app.config loads (in-process production override, .env untouched), then
    Orchestrator(production_gate=True) — the Phase 24/25 guarded production
    path (classifier → verifier chain → production gate → two-tab routing →
    enrichment → guarded write_production with cross-tab dedup + fail-closed
    URL read). No legacy writer, no scheduler bypass, no ungated append.
  * run_daily.py budget gate: projected cost (48 x max(limit,10) x
    PER_POST_USD) must fit remaining Apify spend or the run ABORTS.
  * Phase 29 preflight helpers (sheets_preflight / probe_groq /
    row_invariants) reused byte-for-byte for the read-only Sheets gate and
    the ONE real Groq structured probe.
  * Apify run metadata captured by wrapping the source's _collect with an
    identical call+iterate shape (records run id / dataset id / stats; does
    not alter scrape behavior or spend).
  * Independent post-write verification: full-tab re-read, byte-identity of
    pre-existing rows, exact delta, invariants (strict barrier on NEW rows),
    routing, cross-tab dedup, Review/Held untouched.

No gate/taxonomy/verifier/retry/TLS/.env changes. No commits.

Usage: python tools/run_phase32_production.py [--limit 10]
"""
from __future__ import annotations

import argparse
import io
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# ── MUST happen before ANY app.config import (load_dotenv does not override
#    already-set env vars). This is the run_daily.py in-process override.
os.environ["DRY_RUN"] = "false"

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
# reconfigure() mutates the existing wrapper in place (see the recovery
# runner note: a second TextIOWrapper over the same buffer gets the first
# GC'd, whose __del__ closes the SHARED buffer mid-run).
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace",
                           line_buffering=True)
    sys.stderr.reconfigure(encoding="utf-8", errors="replace",
                           line_buffering=True)
except Exception:
    pass

from apify_client import ApifyClient  # noqa: E402

import app.config as cfg  # noqa: E402  (sees DRY_RUN=false)
from app.config import (  # noqa: E402
    ALLOW_UNGATED_PRODUCTION_WRITE, APIFY_API_TOKEN, DRY_RUN,
    GOOGLE_SHEET_DATA_WORKSHEET, GOOGLE_SHEET_WORKSHEET,
    GOOGLE_SHEETS_CREDENTIALS, GOOGLE_SHEETS_ID, PRODUCTION_GATE,
    SEARCH_QUERIES,
)
from app.orchestrator import Orchestrator, PER_POST_USD  # noqa: E402
from app.sheets_writer import EXPECTED_HEADERS, SheetsWriter  # noqa: E402
from tools.run_phase29_tranche import (  # noqa: E402  (established helpers)
    REVIEW_TAB, probe_groq, row_invariants, sheets_preflight,
)

APIFY_FREE_CAP_USD = 5.0
OUT_DIR = ROOT / "data" / "validation" / "phase32_final_production_run"
UTC_NOW = datetime.now(timezone.utc).isoformat()


def _log(msg: str) -> None:
    print(msg, flush=True)


def _abort(reason: str) -> int:
    _log(f"ABORT: {reason}")
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "abort_reason.txt").write_text(
        f"{UTC_NOW}\nABORT: {reason}\n", encoding="utf-8")
    return 1


def _field(obj, *names):
    """Read a field from an apify-client model or dict, camelCase or snake_case."""
    for n in names:
        if isinstance(obj, dict):
            if n in obj and obj[n] is not None:
                return obj[n]
        else:
            v = getattr(obj, n, None)
            if v is not None:
                return v
    return None


def apify_spend() -> float | None:
    """Cycle spend in USD (run_daily.py current_spend pattern). None = unreadable."""
    try:
        client = ApifyClient(APIFY_API_TOKEN)
        return float(getattr(client.user().limits().current,
                             "monthly_usage_usd", 0.0) or 0.0)
    except Exception as e:
        _log(f"WARN: could not read Apify spend: {e}")
        return None


def snapshot_tabs(w: SheetsWriter, sheet) -> dict:
    """Full values of MAIN/DATA + Review count (also used for byte-identity)."""
    main_ws, data_ws = w.worksheet, w._data_ws()
    main_rows = main_ws.get_all_values()
    data_rows = data_ws.get_all_values()
    review_rows = sheet.worksheet(REVIEW_TAB).get_all_values()
    return {
        "main": main_rows, "data": data_rows, "review_count": len(review_rows),
        "main_ws": main_ws, "data_ws": data_ws,
    }


def verify_post_write(pre: dict, post: dict, written_main: int,
                      written_data: int) -> dict:
    """Section 9 — independent verification. Returns dict; STOP on any failure."""
    checks: dict = {"failures": []}

    def fail(msg: str) -> None:
        checks["failures"].append(msg)

    pre_main, pre_data = pre["main"], pre["data"]
    post_main, post_data = post["main"], post["data"]

    # 1. exact row-count delta per tab
    if len(post_main) != len(pre_main) + written_main:
        fail(f"MAIN count {len(post_main)} != {len(pre_main)} + {written_main}")
    if len(post_data) != len(pre_data) + written_data:
        fail(f"DATA count {len(post_data)} != {len(pre_data)} + {written_data}")

    # 2/3. pre-existing rows byte-identical; new rows well-formed
    for i, row in enumerate(pre_main[1:]):
        if post_main[1 + i] != row:
            fail(f"MAIN row {i + 1} changed")
    for i, row in enumerate(pre_data[1:]):
        if post_data[1 + i] != row:
            fail(f"DATA row {i + 1} changed")
    new_main = post_main[len(pre_main):]
    new_data = post_data[len(pre_data):]
    # strict=True: Phase 25.7 barrier applies to NEW rows written this phase
    for row in new_main:
        errs = row_invariants(row, "MAIN", strict=True)
        if errs:
            fail("new MAIN row invariant: " + "; ".join(errs))
        if (row[1] or "").strip().lower() not in (
                "founder's office", "chief of staff", "founder office"):
            fail(f"new MAIN row category unexpected: {row[1]!r}")
    for row in new_data:
        errs = row_invariants(row, "DATA", strict=True)
        if errs:
            fail("new DATA row invariant: " + "; ".join(errs))
        cat = (row[1] or "").strip().lower()
        if "data" not in cat and "analyst" not in cat and "scientist" not in cat:
            fail(f"new DATA row category unexpected: {row[1]!r}")

    # 4/5. routing + cross-tab duplicates across the FULL post state
    main_urls = {r[7] for r in post_main[1:] if r[7]}
    data_urls = {r[7] for r in post_data[1:] if r[7]}
    dupes = main_urls & data_urls
    checks["cross_tab_duplicates"] = len(dupes)
    if dupes:
        fail(f"cross-tab duplicates: {sorted(dupes)[:3]}")

    # 6/7/8. full-tab invariants (existing rows baseline) + no held statuses
    for rows, tab in ((post_main, "MAIN"), (post_data, "DATA")):
        if not rows or rows[0] != EXPECTED_HEADERS:
            fail(f"{tab}: header != 23-column schema")
        for r in rows[1:]:
            errs = row_invariants(r, tab, strict=False)
            if errs:
                fail(f"{tab}: " + "; ".join(errs))
            if (r[18] or "").strip().lower() not in ("new",):
                fail(f"{tab}: non-New status in production tab: {r[18]!r}")

    # Review/Held untouched
    if post["review_count"] != pre["review_count"]:
        fail(f"Review/Held changed: {pre['review_count']} -> {post['review_count']}")

    checks["new_main_rows"] = new_main
    checks["new_data_rows"] = new_data
    checks["final_main"] = len(post_main) - 1
    checks["final_data"] = len(post_data) - 1
    checks["final_review"] = post["review_count"] - 1
    return checks


def main() -> int:
    ap = argparse.ArgumentParser(description="Phase 32 supervised production run")
    ap.add_argument("--limit", type=int, default=10,
                    help="Max posts per keyword (actor floors at 10)")
    args = ap.parse_args()
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    _log(f"=== PHASE 32 — supervised production cycle @ {UTC_NOW} ===")

    # ── §1 preflight: configuration (no secrets) ──────────────────────────
    env_flags = {
        "DRY_RUN_env": os.environ.get("DRY_RUN"),
        "DRY_RUN_effective": DRY_RUN,                # must be False (live write)
        "PRODUCTION_GATE_effective": PRODUCTION_GATE,
        "ALLOW_UNGATED_PRODUCTION_WRITE": bool(ALLOW_UNGATED_PRODUCTION_WRITE),
        "apiify_token_present": bool(APIFY_API_TOKEN),
        "groq_key_present": bool(cfg.GROQ_API_KEY),
        "gemini_key_present": bool(cfg.GEMINI_API_KEY),
        "sheet_id_present": bool(GOOGLE_SHEETS_ID),
        "credentials_path_present": bool(GOOGLE_SHEETS_CREDENTIALS),
        "query_count": len(SEARCH_QUERIES),
        "orchestrator_gate_explicit": True,  # Orchestrator(production_gate=True) below
    }
    if DRY_RUN:
        return _abort("DRY_RUN is effectively true — live write path unavailable")
    if env_flags["ALLOW_UNGATED_PRODUCTION_WRITE"]:
        return _abort("ALLOW_UNGATED_PRODUCTION_WRITE must be false")
    _log(f"config: {json.dumps(env_flags, default=str)}")

    # ── budget gate (BEFORE any scrape or Sheets work) ────────────────────
    projected = len(SEARCH_QUERIES) * max(args.limit, 10) * PER_POST_USD
    spent = apify_spend()
    if spent is not None:
        remaining = APIFY_FREE_CAP_USD - spent
        _log(f"Apify: spent ${spent:.4f} / ${APIFY_FREE_CAP_USD:.2f} · "
             f"projected ~${projected:.2f} · remaining ${remaining:.4f}")
        if projected > remaining:
            return _abort(f"projected ${projected:.2f} exceeds remaining ${remaining:.4f}")
    budget = {"spent_usd": spent, "cap_usd": APIFY_FREE_CAP_USD,
              "projected_usd": projected, "limit": args.limit}

    # ── TLS/OAuth + Sheets preflight (read-only) ──────────────────────────
    w = SheetsWriter(GOOGLE_SHEETS_CREDENTIALS, GOOGLE_SHEETS_ID,
                     GOOGLE_SHEET_WORKSHEET, DRY_RUN,
                     data_worksheet_name=GOOGLE_SHEET_DATA_WORKSHEET)
    if w.client is None or w.dry_run:
        return _abort("Sheets auth failed (writer degraded to dry-run)")
    sheet = w.client.open_by_key(GOOGLE_SHEETS_ID)
    try:
        preflight = sheets_preflight(w, sheet)
    except Exception as e:
        return _abort(f"Sheets preflight failed: {e}")
    _log(f"preflight OK: {json.dumps(preflight)}")
    pre = snapshot_tabs(w, sheet)

    # ── provider probe ────────────────────────────────────────────────────
    p_status, p_latency, p_note = probe_groq()
    _log(f"Groq probe: {p_status} ({p_latency:.1f}s) {p_note}")
    if p_status != "SERVING":
        return _abort(f"provider preflight failed: {p_status} {p_note}")

    # ── §2 Apify run capture (identical call+iterate shape; metadata only) ─
    orch = Orchestrator(production_gate=True)
    source = orch.source
    captured: dict = {}

    orig_collect = source._collect

    def _collect_capture(run_input: dict):
        run = source.client.actor(source.actor_id).call(run_input=run_input)
        if not run:
            raise Exception(f"Actor {source.actor_id} failed to start or complete.")
        captured["run_id"] = _field(run, "id")
        captured["dataset_id"] = _field(run, "default_dataset_id",
                                        "defaultDatasetId")
        captured["started_at"] = str(_field(run, "startedAt", "started_at") or "")
        captured["actor"] = source.actor_id
        captured["run_input"] = {k: run_input[k] for k in
                                 ("keywords", "max_posts", "sort_by", "date_filter")}
        posts = []
        for item in source.client.dataset(captured["dataset_id"]).iterate_items():
            if item.get("error"):
                source.last_errors.append(str(item.get("error")))
                continue
            post = source._to_raw_post(item)
            if post is None:
                source.last_errors.append("record missing post_url or text")
                continue
            posts.append(post)
        captured["raw_items"] = len(posts) + len(source.last_errors)
        return posts

    source._collect = _collect_capture  # capture-only wrapper; same behavior

    # ── §2–§8 the supervised cycle (established Orchestrator path) ────────
    t0 = time.time()
    summary = orch.run_pipeline(limit=args.limit)
    elapsed = time.time() - t0
    _log(f"pipeline finished in {elapsed:.0f}s; summary: "
         f"{json.dumps({k: v for k, v in summary.items() if isinstance(v, (int, float, str, bool))})}")

    if captured.get("run_id"):
        try:
            run_info = ApifyClient(APIFY_API_TOKEN).run(captured["run_id"]).get()
            captured["status"] = _field(run_info, "status")
            captured["stats"] = _field(run_info, "stats") or {}
            usage = _field(run_info, "usage") or {}
            total = _field(usage, "totalUsd", "total_usd") \
                if isinstance(usage, dict) else getattr(usage, "total_usd", None)
            captured["usage_usd"] = total
        except Exception as e:
            captured["stats_error"] = str(e)[:200]

    # ── §9 independent post-write verification ────────────────────────────
    post = snapshot_tabs(w, sheet)
    written_main = int(summary.get("main_written", 0) or 0)
    written_data = int(summary.get("data_written", 0) or 0)
    verification = verify_post_write(pre, post, written_main, written_data)
    if verification["failures"]:
        _log("POST-WRITE VERIFICATION FAILED:\n  " +
             "\n  ".join(verification["failures"][:10]))
        (OUT_DIR / "sheet_write_verification.json").write_text(json.dumps(
            {**{k: v for k, v in verification.items() if k != "main_ws"},
             "failed": True}, indent=2, default=str), encoding="utf-8")
        return 2  # STOP; no automatic retry
    _log("post-write verification: ALL CHECKS PASS")

    # ── §11 artifacts ─────────────────────────────────────────────────────
    accepted = orch._accepted_details or []
    review = orch._review_details or []
    rejected = orch._rejected_samples or []
    enrich = orch._enrichment_results or {}

    funnel = {
        "utc_started": UTC_NOW, "elapsed_s": round(elapsed, 1),
        "queries_sent": len(SEARCH_QUERIES), "limit_per_query": args.limit,
        "raw_posts": summary.get("scraped"), "unique_posts": summary.get("unique"),
        "run_duplicates": summary.get("current_run_duplicates"),
        "deterministic_excluded": summary.get("excluded"),
        "deterministic_candidates": summary.get("deterministic_candidates"),
        "llm_accepted": summary.get("accepted"), "llm_review": summary.get("review"),
        "llm_rejected": summary.get("llm_rejected"),
        "production_held": summary.get("production_held"),
        "production_rejected": summary.get("production_rejected"),
        "sheet_duplicates": summary.get("sheet_duplicates"),
        "rows_written_main": written_main, "rows_written_data": written_data,
        "write_outcome": summary.get("write_outcome"),
        "errors": summary.get("errors"),
        "final_main": verification["final_main"], "final_data": verification["final_data"],
        "final_review": verification["final_review"],
        "cross_tab_duplicates": verification["cross_tab_duplicates"],
        "probe": {"status": p_status, "latency_s": round(p_latency, 1)},
        "preflight": preflight, "budget": budget,
    }
    (OUT_DIR / "funnel_summary.json").write_text(
        json.dumps(funnel, indent=2, default=str), encoding="utf-8")
    (OUT_DIR / "apify_run_record.json").write_text(
        json.dumps(captured, indent=2, default=str), encoding="utf-8")

    with (OUT_DIR / "candidate_results.csv").open("w", encoding="utf-8",
                                                  newline="") as f:
        f.write("source_url,company,exact_role,major_category,status,"
                "confidence,disposition\n")
        for r in accepted:
            f.write(json.dumps([r.get("source_url"), r.get("company"),
                                r.get("exact_role"), r.get("major_category"),
                                r.get("status"), r.get("confidence"),
                                "ACCEPT"])[1:-1] + "\n")
        for r in review:
            f.write(json.dumps([r.get("source_url", ""), r.get("company", ""),
                                r.get("exact_role", ""), r.get("major_category", ""),
                                r.get("status", ""), r.get("confidence", ""),
                                "HOLD/REVIEW"])[1:-1] + "\n")
        for r in rejected:
            f.write(json.dumps([r.get("source_link", ""), "", "", "",
                                "REJECT", "", " | ".join(r.get("reasons", []))[:200]])[1:-1] + "\n")

    wrote_any = written_main + written_data
    for name, rows in (("eligible_main.csv", verification["new_main_rows"]),
                       ("eligible_data.csv", verification["new_data_rows"])):
        with (OUT_DIR / name).open("w", encoding="utf-8", newline="") as f:
            f.write(",".join(EXPECTED_HEADERS) + "\n")
            for row in rows:
                f.write(",".join('"' + c.replace('"', '""') + '"' for c in row) + "\n")

    gate_blocked = [r for r in review
                    if "Production gate HOLD" in str(r.get("reason", ""))]
    with (OUT_DIR / "gate_blocked.csv").open("w", encoding="utf-8",
                                             newline="") as f:
        f.write("source_url,company,exact_role,blocker\n")
        for r in gate_blocked:
            reason = str(r.get("reason", ""))
            block = reason.split("Production gate HOLD: ", 1)[-1]
            f.write(json.dumps([r.get("source_url", ""), r.get("company", ""),
                                r.get("exact_role", ""), block])[1:-1] + "\n")

    prov = {
        "chain": "Groq -> Gemini key1 -> Gemini key2 (unchanged)",
        "llm_calls_total": getattr(orch.verifier, "llm_calls", None),
        "probe": {"status": p_status, "latency_s": round(p_latency, 1),
                  "note": p_note},
        "failover_usage_note": "failover consumes no extra calls unless a provider "
                               "transport failure occurred; total calls above include retries",
    }
    (OUT_DIR / "provider_summary.json").write_text(
        json.dumps(prov, indent=2), encoding="utf-8")
    (OUT_DIR / "sheet_write_verification.json").write_text(json.dumps({
        "failed": False, "written_main": written_main,
        "written_data": written_data, "write_outcome": summary.get("write_outcome"),
        "final_main": verification["final_main"],
        "final_data": verification["final_data"],
        "final_review": verification["final_review"],
        "cross_tab_duplicates": verification["cross_tab_duplicates"],
        "all_checks": "PASS",
    }, indent=2), encoding="utf-8")

    enrichment_counts = {
        "accepted_total": len(accepted),
        "enrichment_results": len(enrich),
        "hm_available": sum(1 for r in accepted
                            if r.get("hiring_manager_linkedin")),
        "email_available": sum(1 for r in accepted if r.get("cold_email")),
        "apply_link_available": sum(1 for r in accepted if r.get("apply_link")),
        "form_available": sum(1 for r in accepted if r.get("apply_google_form")),
    }
    (OUT_DIR / "enrichment_counts.json").write_text(
        json.dumps(enrichment_counts, indent=2), encoding="utf-8")

    _log("artifacts written; run summary JSON follows")
    _log(json.dumps(funnel, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
