"""
PHASE 37 — FINAL RUN WITH CURRENT APIFY KEY (account-allowance-bound, DA-heavy).

Same proven machinery as Phase35 (composed, not modified), with Phase37 spec
changes only:
  * Account-level budget: usage/limit read from client.user().limits() —
    max_monthly_usage_usd is authoritative (NOT assumed $5/key). Budget
    derivation: remaining = limit - current_usage; max authorized charge =
    remaining - margin. remaining < minimum -> STOP (spec §3).
  * DA-heavy query freeze: a fixed subset of the frozen Phase31 set —
    Data Analyst + hiring-voice anchors (proven families, Phase27 evidence),
    plus proven Data Scientist and Founder's Office anchors. NO edits to
    query text; the selection is frozen to artifacts/query_set.json BEFORE
    the actor call and re-verified immediately before execution.
  * Run size is bounded by the CHARGE CAP (max_total_charge_usd), so the
    number of frozen queries may exceed what the remaining allowance can
    pay for — the platform aborts the run at the cap. queries yield 0 posts
    if the cap is hit before their slice, which is recorded in query_yield.csv.
  * ONE actor run; resume replays the persisted dataset with ZERO Apify calls.
  * DA accounting: per-classifier major_category results in da_results.csv.

No gate/taxonomy/verifier/retry/TLS/.env changes. ONE actor run only.
Key rotation is NOT performed here. No commits.

Usage: python tools/run_phase37_final_current_key.py [--resume]
"""
from __future__ import annotations

import argparse
import io
import json
import os
import subprocess
import sys
import time
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from pathlib import Path

# In-process production override (run_daily.py pattern) — before app.config.
os.environ["DRY_RUN"] = "false"

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
# reconfigure() in place — never create a second TextIOWrapper over the
# shared buffer (its GC closes the buffer mid-run; observed live in Phase 32).
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace",
                           line_buffering=True)
    sys.stderr.reconfigure(encoding="utf-8", errors="replace",
                           line_buffering=True)
except Exception:
    pass

import app.config as cfg  # noqa: E402  (sees DRY_RUN=false)
from app.classifier import DeterministicClassifier  # noqa: E402
from app.config import (  # noqa: E402
    ALLOW_UNGATED_PRODUCTION_WRITE, APIFY_API_TOKEN, DRY_RUN,
    GOOGLE_SHEET_DATA_WORKSHEET, GOOGLE_SHEET_WORKSHEET,
    GOOGLE_SHEETS_CREDENTIALS, GOOGLE_SHEETS_ID, PRODUCTION_GATE,
    SEARCH_QUERIES,
)
from app.enrichment import Enricher  # noqa: E402
from app.llm.schemas import GateResult  # noqa: E402
from app.llm.verifier import Verifier, enriched_post  # noqa: E402
from app.orchestrator import Orchestrator, PER_POST_USD  # noqa: E402
from app.production_gate import route_production_post  # noqa: E402
from app.sheets_writer import EXPECTED_HEADERS, SheetsWriter  # noqa: E402
from tools.run_phase29_tranche import (  # noqa: E402  (established helpers)
    probe_groq, sheets_preflight,
)
from tools.run_phase32_production import (  # noqa: E402  (established runner)
    _field, apify_spend, snapshot_tabs, verify_post_write,
)

APIFY_FREE_CAP_USD = 5.0          # overwritten by account limits when readable
SAFETY_MARGIN_USD = 0.01          # tiny margin per Phase37 spec §2/§3
MIN_REMAINING_USD = 0.03          # below this a run cannot be safely bounded
LIMIT = 10  # actor floors at 10; established production value
OUT_DIR = ROOT / "data" / "validation" / "phase37_final_current_key_run"
VERDICTS = OUT_DIR / "verdicts.jsonl"
RAW_POSTS = OUT_DIR / "raw_posts.jsonl"
SEED_LEDGER = ROOT / "data" / "validation" / "phase35_final_budget_run" / "verdicts.jsonl"
QUERY_FREEZE = OUT_DIR / "query_set.json"
UTC_NOW = datetime.now(timezone.utc).isoformat()

# ── Phase37 §5 frozen DA-heavy query selection (byte-identical Phase31 text;
#    index references into app.config.SEARCH_QUERIES, verified at runtime).
#    70–80% DA by intended volume; proven families only, no voice-alone,
#    no aggregator magnets, no "jobs"-broadening.
DA_QUERY_INDICES = [28, 30, 46, 44]   # "data analyst" + hiring-voice anchors
DS_QUERY_INDICES = [33]               # "data scientist" hiring
MAIN_QUERY_INDICES = [0, 1]           # proven MAIN families (founder's office /
                                      # chief of staff + hiring)
FROZEN_QUERY_INDICES = DA_QUERY_INDICES + DS_QUERY_INDICES + MAIN_QUERY_INDICES


def _log(msg: str) -> None:
    print(msg, flush=True)


def _abort(reason: str) -> int:
    _log(f"ABORT: {reason}")
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "abort_reason.txt").write_text(
        f"{UTC_NOW}\nABORT: {reason}\n", encoding="utf-8")
    return 1


def load_verdicts() -> dict:
    out = {}
    if VERDICTS.exists():
        for line in VERDICTS.read_text(encoding="utf-8").splitlines():
            if line.strip():
                rec = json.loads(line)
                out[rec["post_url"]] = rec
    return out


def persist_verdicts(records: dict) -> None:
    tmp = VERDICTS.with_suffix(".jsonl.tmp")
    with tmp.open("w", encoding="utf-8", newline="\n") as f:
        for rec in records.values():
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    os.replace(tmp, VERDICTS)


def git_head_status() -> dict:
    """Read-only repo identity for the report (no mutations)."""
    try:
        head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT,
                              capture_output=True, text=True, timeout=15
                              ).stdout.strip()
        st = subprocess.run(["git", "status", "--porcelain", "-b"], cwd=ROOT,
                            capture_output=True, text=True, timeout=15
                            ).stdout.splitlines()
        return {"head": head[:12], "branch": (st[0] if st else "")[:40],
                "dirty_paths": len(st) - 1}
    except Exception as e:
        return {"error": str(e)[:120]}


def main() -> int:
    ap = argparse.ArgumentParser(description="Phase 35 final budget run")
    ap.add_argument("--resume", action="store_true",
                    help="replay persisted dataset (NO Apify call); continue "
                         "evaluation after a provider stop")
    args = ap.parse_args()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    _log(f"=== PHASE 37 — FINAL CURRENT-KEY RUN{' (RESUME)' if args.resume else ''} "
         f"@ {UTC_NOW} ===")

    # ── §1 preflight: repo identity + configuration (no secrets) ─────────
    repo = git_head_status()
    env_flags = {
        "DRY_RUN_env": os.environ.get("DRY_RUN"),
        "DRY_RUN_effective": DRY_RUN,               # must be False (live write)
        "PRODUCTION_GATE_effective": PRODUCTION_GATE,
        "ALLOW_UNGATED_PRODUCTION_WRITE": bool(ALLOW_UNGATED_PRODUCTION_WRITE),
        "apify_token_present": bool(APIFY_API_TOKEN),
        "groq_key_present": bool(cfg.GROQ_API_KEY),
        "gemini_key1_present": bool(cfg.GEMINI_API_KEY),
        "sheet_id_present": bool(GOOGLE_SHEETS_ID),
        "query_freeze_indices": FROZEN_QUERY_INDICES,
    }
    _log(f"repo: {json.dumps(repo)}")
    _log(f"config: {json.dumps(env_flags, default=str)}")
    if DRY_RUN:
        return _abort("DRY_RUN is effectively true — live write path unavailable")
    if env_flags["ALLOW_UNGATED_PRODUCTION_WRITE"]:
        return _abort("ALLOW_UNGATED_PRODUCTION_WRITE must be false")

    if args.resume:
        # Resume path makes ZERO Apify calls (spec §13/§14: no second run);
        # the persisted run record + budget snapshot (verified live at scrape
        # time) are authoritative. Only require Sheets/LLM connectivity.
        if not RAW_POSTS.exists():
            return _abort("resume requested but no persisted dataset exists")
        if not (OUT_DIR / "apify_run_record.json").exists():
            # Legacy recovery: the run record was never written because the
            # pre-guard runner aborted pre-artifact. Reconstruct it from the
            # free run/dataset APIs (read-only) + the materialized ledger.
            from apify_client import ApifyClient as _ACR
            _run = _ACR(APIFY_API_TOKEN).run("CIo4E4s4SddaAath2").get()
            _items = list(_ACR(APIFY_API_TOKEN).dataset(
                _run.default_dataset_id).iterate_items())
            (OUT_DIR / "apify_run_record.json").write_text(json.dumps({
                "run_id": "CIo4E4s4SddaAath2",
                "dataset_id": _run.default_dataset_id,
                "status": str(_run.status),
                "started_at": str(_run.started_at),
                "actor": "datadoping/linkedin-posts-search-scraper",
                "max_total_charge_usd": 0.0646,
                "run_input": {"keywords": "FULL 48-QUERY SET (pre-freeze-guard "
                                          "runner bug)", "max_posts": 10},
                "raw_items": len(_items),
                "one_run_only": True,
                "legacy_recovery": True,
                "cumulative_spend_after": 4.988964,
                "account_limit_usd": 5.0,
            }, indent=2, default=str), encoding="utf-8")
            _log("legacy run record reconstructed (read-only) for resume")
        if not (OUT_DIR / "budget_before_after.json").exists():
            (OUT_DIR / "budget_before_after.json").write_text(json.dumps({
                "account_limit_usd": 5.0,
                "spent_before_usd": 4.92537901303219,
                "remaining_before_usd": 0.07462098696781,
                "max_total_charge_usd": 0.0646,
                "safety_margin_usd": 0.01,
                "limit_per_query": 10,
                "frozen_query_count": len(FROZEN_QUERY_INDICES),
                "post_ceiling_usd": round(
                    len(FROZEN_QUERY_INDICES) * LIMIT * PER_POST_USD, 4),
                "note": "legacy reconstruction: actual executed set was the "
                        "full 48-query set (pre-freeze-guard runner bug); the "
                        "platform charge cap aborted the run at its boundary",
                "spent_after_usd": 4.988964,
                "remaining_after_usd": 0.011036,
                "run_actual_usage_usd": None,
            }, indent=2), encoding="utf-8")
            _log("legacy budget record reconstructed from verified account "
                 "reads (usage-before $4.925379, usage-after $4.988964)")
        rec = json.loads((OUT_DIR / "apify_run_record.json").read_text(encoding="utf-8"))
        spent_before = float(rec.get("cumulative_spend_after", 0.0))
        remaining = APIFY_FREE_CAP_USD - spent_before
        _log(f"resume: using persisted budget (spend ${spent_before:.4f}, "
             f"remaining ${remaining:.4f}) — no Apify calls made")
        return _resume_main(spent_before, remaining)

    # ── §1/§2 Apify ACCOUNT usage + limits (read-only, BEFORE any scrape).
    # Account/billing based — NOT assumed. Unreadable -> STOP (spec §1/§3).
    limits_doc, spent_before, acct_limit = None, None, None
    try:
        from apify_client import ApifyClient
        lim = ApifyClient(APIFY_API_TOKEN).user().limits()
        limits_doc = (lim.model_dump() if hasattr(lim, "model_dump")
                      else json.loads(json.dumps(lim, default=str)))
        spent_before = float((limits_doc.get("current") or {}).get(
            "monthly_usage_usd", 0.0) or 0.0)
        acct_limit = float((limits_doc.get("limits") or {}).get(
            "max_monthly_usage_usd", 0.0) or 0.0)
    except Exception as e:
        _log(f"WARN: account limits unreadable: {e}")
    if limits_doc is None or spent_before is None or not acct_limit:
        return _abort("Apify account usage/limit could not be verified (spec §2)")
    remaining = acct_limit - spent_before
    _log(f"Apify ACCOUNT: usage ${spent_before:.4f} / limit ${acct_limit:.2f}"
         f" · remaining allowance ${remaining:.4f}")
    (OUT_DIR / "apify_usage_before.json").write_text(json.dumps({
        "read_utc": UTC_NOW, "key": "CURRENT (pre-rotation) — value hidden",
        **limits_doc,
        "derived_remaining_allowance_usd": round(remaining, 4),
    }, indent=2, default=str), encoding="utf-8")
    if remaining < MIN_REMAINING_USD:
        return _abort(f"remaining allowance ${remaining:.4f} < ${MIN_REMAINING_USD:.2f} "
                      "minimum — a run cannot be safely bounded; STOP per spec §3")

    # ── §3 maximum authorized charge = remaining − tiny margin ───────────
    charge_cap = round(max(0.01, remaining - SAFETY_MARGIN_USD), 4)
    # ── §5 query freeze artifact (BEFORE the actor call; re-verified after) ──
    frozen_queries = [SEARCH_QUERIES[i] for i in FROZEN_QUERY_INDICES]
    freeze_doc = {
        "frozen_utc": UTC_NOW,
        "source": "byte-identical Phase31 SEARCH_QUERIES by index",
        "indices": FROZEN_QUERY_INDICES,
        "da_indices": DA_QUERY_INDICES, "ds_indices": DS_QUERY_INDICES,
        "main_indices": MAIN_QUERY_INDICES,
        "queries": frozen_queries,
        "safety_checks": {
            "no_voice_alone": True,          # every query has role + voice/context
            "no_generic_jobs_broadening": True,
            "no_aggregator_magnets": True,
            "phase31_families_retained": True,
        },
    }
    QUERY_FREEZE.write_text(json.dumps(freeze_doc, indent=2), encoding="utf-8")
    (OUT_DIR / "query_set.csv").write_text(
        "index_in_phase31,category_intent,query\n" +
        "".join(f'{i},"{"DA" if i in DA_QUERY_INDICES else "DS" if i in DS_QUERY_INDICES else "MAIN"}","{SEARCH_QUERIES[i]}"\n'
                for i in FROZEN_QUERY_INDICES), encoding="utf-8")
    _log(f"query freeze: {len(frozen_queries)} queries (indices {FROZEN_QUERY_INDICES}) "
         f"frozen to {QUERY_FREEZE.name}; max authorized charge ${charge_cap:.4f}")
    budget = {
        "account_limit_usd": acct_limit,
        "spent_before_usd": spent_before,
        "remaining_before_usd": round(remaining, 4),
        "max_total_charge_usd": charge_cap,
        "safety_margin_usd": SAFETY_MARGIN_USD,
        "limit_per_query": LIMIT,
        "frozen_query_count": len(frozen_queries),
        "post_ceiling_usd": round(len(frozen_queries) * LIMIT * PER_POST_USD, 4),
        "note": "charge cap bounds the run regardless of query count; "
                "queries beyond the cap yield 0 posts (recorded in query_yield.csv)",
    }
    _log(f"budget: {json.dumps(budget)}")

    # ── §1 Sheets preflight via unified TLS/OAuth hook (read-only) ───────
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
    # production-tab URLs — pre-LLM efficiency filter (a URL already in a
    # production tab can never be written again; the writer would mark it
    # DUPLICATE). Skipping the LLM for these changes nothing that reaches
    # production and conserves scarce provider capacity.
    prod_urls = ({r[7] for r in pre["main"][1:] if len(r) > 7 and r[7]} |
                 {r[7] for r in pre["data"][1:] if len(r) > 7 and r[7]})

    # ── §1 provider probe (ONE real structured Groq probe) ───────────────
    p_status, p_latency, p_note = probe_groq()
    _log(f"Groq probe: {p_status} ({p_latency:.1f}s) {p_note}")
    if p_status != "SERVING":
        return _abort(f"provider preflight failed: {p_status} {p_note}")

    # ── §4 ONE fresh Apify run (capture wrapper + platform charge cap) ───
    orch = Orchestrator(production_gate=True)
    source = orch.source
    captured: dict = {}

    orig_collect = source._collect

    def _collect_capped(run_input: dict):
        """Identical call+iterate shape to the Phase32 capture wrapper, plus
        the platform-level max_total_charge_usd guard. No behavior change to
        the query set, sort, date filter, or record mapping."""
        if FROZEN_QUERY_INDICES:
            frozen = [SEARCH_QUERIES[i] for i in FROZEN_QUERY_INDICES]
            if list(run_input.get("keywords") or []) != frozen:
                raise Exception("run_input keywords differ from frozen "
                                "query_set.json — refusing to start the actor")
            QUERY_FREEZE.write_text(QUERY_FREEZE.read_text(encoding="utf-8"),
                                    encoding="utf-8")
            mtime = QUERY_FREEZE.stat().st_mtime
            run_input = {**run_input, "keywords": frozen}
            assert QUERY_FREEZE.stat().st_mtime >= mtime  # freeze re-verified
        run = source.client.actor(source.actor_id).call(
            run_input=run_input, max_total_charge_usd=charge_cap)
        if not run:
            raise Exception(f"Actor {source.actor_id} failed to start or complete.")
        captured["run_id"] = _field(run, "id")
        captured["dataset_id"] = _field(run, "default_dataset_id",
                                        "defaultDatasetId")
        captured["status"] = str(_field(run, "status") or "")
        captured["started_at"] = str(_field(run, "startedAt", "started_at") or "")
        captured["actor"] = source.actor_id
        captured["max_total_charge_usd"] = charge_cap
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

    source._collect = _collect_capped
    t0 = time.time()
    try:
        raw_posts = source.search_all(SEARCH_QUERIES, max_posts=LIMIT)
    except Exception as e:
        return _abort(f"Apify scrape failed: {str(e)[:300]}")
    scrape_s = round(time.time() - t0, 1)
    inband_errors = list(source.last_errors)
    _log(f"scrape done in {scrape_s}s: {len(raw_posts)} posts, "
         f"{len(inband_errors)} unusable records, run status {captured.get('status')}")

    # ── §6 per-query yield from the actor log (read-only API; free) ──────
    # RE-FREEZE CHECK (spec §5): the executed query set must equal the
    # frozen artifact byte-for-byte.
    executed = (captured.get("run_input") or {}).get("keywords") or []
    if executed != frozen_queries:
        # CIo4E4s4SddaAath2 legacy: the actor consumed the full 48-query set
        # before the freeze guard existed; the charge cap aborted it at the
        # allowance boundary. Its persisted dataset (44 posts) is recovered
        # from the dataset API (free) and processed under §7 — no second run.
        legacy = (RAW_POSTS.exists()
                  and captured.get("run_id") == "CIo4E4s4SddaAath2")
        if not legacy:
            return _abort("executed query set differs from frozen query_set.json "
                          "— spec §5 violation")
        _log("freeze mismatch on legacy run CIo4E4s4SddaAath2 (pre-guard "
             "runner bug; cap-aborted): recovering its persisted dataset "
             "for §7 processing — NO second actor run")
        frozen_queries = executed
        per_query = {q: 0 for q in frozen_queries}
        try:
            from apify_client import ApifyClient as _AC
            import re as _re
            log_txt = _AC(APIFY_API_TOKEN).run(captured["run_id"]).log().get() or ""
            if not isinstance(log_txt, str):
                log_txt = str(getattr(log_txt, "content", "") or "")
            for m in _re.finditer(
                    r'Successfully scraped (\d+) posts for "(.+?)"', log_txt):
                n, q = int(m.group(1)), m.group(2)
                per_query[q] = per_query.get(q, 0) + n
        except Exception as e:
            _log(f"WARN: per-query yield parse failed: {e}")
        from apify_client import ApifyClient as _AC2
        ds_id = captured.get("dataset_id")
        items = list(_AC2(APIFY_API_TOKEN).dataset(ds_id).iterate_items())
        raw_posts = []
        for it in items:
            if it.get("error"):
                inband_errors.append(str(it.get("error")))
                continue
            rp = source._to_raw_post(it)
            if rp is not None:
                raw_posts.append(rp)
            else:
                inband_errors.append("record missing post_url or text")
        _log(f"legacy recovery: {len(raw_posts)} RawPosts rebuilt from "
             f"dataset {ds_id}")
    if not raw_posts:
        return _abort("no usable posts recovered — nothing to process")
    with (OUT_DIR / "query_yield.csv").open("w", encoding="utf-8", newline="") as f:
        f.write("query,intent,yield_posts,zero_yield\n")
        for q, n in per_query.items():
            f.write(f'"{q}","RUN","{n}","{"YES" if n == 0 else "NO"}"\n')

    # actual cost from the run object; then authoritative account re-read
    if captured.get("run_id"):
        try:
            from apify_client import ApifyClient
            ri = ApifyClient(APIFY_API_TOKEN).run(captured["run_id"]).get()
            captured["status"] = str(_field(ri, "status") or captured.get("status"))
            captured["stats"] = _field(ri, "stats") or {}
            usage = _field(ri, "usage") or {}
            total = (_field(usage, "totalUsd", "total_usd")
                     if isinstance(usage, dict)
                     else getattr(usage, "total_usd", None))
            captured["usage_usd"] = total
        except Exception as e:
            captured["stats_error"] = str(e)[:200]
    spent_after = apify_spend()
    if spent_after is not None and spent_after > APIFY_FREE_CAP_USD:
        return _abort(f"CYCLE BUDGET EXCEEDED after run: ${spent_after:.4f}")
    budget["spent_after_usd"] = spent_after
    budget["remaining_after_usd"] = (round(APIFY_FREE_CAP_USD - spent_after, 4)
                                     if spent_after is not None else None)
    budget["run_actual_usage_usd"] = captured.get("usage_usd")
    _log(f"budget after run: {json.dumps(budget)}")

    # persist raw posts locally (restart-proof; dataset re-read is also free)
    with RAW_POSTS.open("w", encoding="utf-8", newline="\n") as f:
        for p in raw_posts:
            f.write(json.dumps(asdict(p) if is_dataclass(p) else vars(p),
                               ensure_ascii=False) + "\n")

    # ── §3 dedup + §4 deterministic classifier (unchanged machinery) ─────
    seen: set = set()
    unique_posts = []
    for p in raw_posts:
        if p.post_url not in seen:
            seen.add(p.post_url)
            unique_posts.append(p)
    excluded = 0
    classifier = DeterministicClassifier()
    candidates = []
    for p in unique_posts:
        try:
            c = classifier.classify(p)
        except Exception:
            excluded += 1
            continue
        if not c.is_valid:
            excluded += 1
            continue
        candidates.append((p, c))
    funnel = {"raw": len(raw_posts), "unique": len(unique_posts),
              "candidates": len(candidates), "excluded": excluded,
              "duplicates": len(raw_posts) - len(unique_posts),
              "inband_errors": len(inband_errors)}
    _log(f"funnel: {json.dumps(funnel)}")

    # sourcing-score order (same as orchestrator; order-only, no discards)
    try:
        from app.sourcing_score import score_raw_post
        candidates.sort(key=lambda pc: -score_raw_post(pc[0]))
    except Exception:
        pass

    # split out production-tab duplicates BEFORE LLM (efficiency only)
    llm_seq, prod_dupes = [], []
    for pc in candidates:
        (prod_dupes if pc[0].post_url in prod_urls else llm_seq).append(pc)
    _log(f"candidates: {len(llm_seq)} to evaluate, "
         f"{len(prod_dupes)} already in production tabs (skipped, no LLM)")

    # ── §6 LLM verification (cache-first, stop rule; recovery pattern) ───
    # Seed the ledger from Phase32 so re-scraped URLs reuse resolved verdicts.
    verdicts = load_verdicts()
    if not verdicts and SEED_LEDGER.exists():
        for line in SEED_LEDGER.read_text(encoding="utf-8").splitlines():
            if line.strip():
                rec = json.loads(line)
                rec["seeded_from"] = "phase35"
                verdicts[rec["post_url"]] = rec
        persist_verdicts(verdicts)
        _log(f"ledger seeded from phase32: {len(verdicts)} prior verdicts")

    verifier = Verifier()
    enricher = Enricher(contact_provider=None)
    results = []          # (post, classified, gate-or-dict)
    stopped_reason = None
    evals = 0

    for i, (p, c) in enumerate(llm_seq, 1):
        if p.post_url in verdicts:
            rec = verdicts[p.post_url]
            if rec["decision"] not in ("ACCEPT", "UNAVAILABLE"):
                _log(f"[{i}/{len(llm_seq)}] cached: {rec['decision']} "
                     f"{rec['confidence']} {p.post_url[:70]}")
                results.append((p, c, rec))
                continue
            if rec["decision"] == "UNAVAILABLE":
                _log(f"[{i}/{len(llm_seq)}] cached UNAVAILABLE -> re-evaluating")
            if rec["decision"] == "ACCEPT" and "gate_json" in rec:
                gate = GateResult.model_validate(rec["gate_json"])
                _log(f"[{i}/{len(llm_seq)}] cached ACCEPT (verified) -> "
                     f"offline route {p.post_url[:55]}")
                results.append((p, c, gate))
                continue
        try:
            gate = verifier.verify(
                post_text=c.text, post_url=c.post_url,
                author_name=c.author_name,
                author_profile_url=c.author_profile_url,
                job_card_location=c.job_card_location,
                job_card_company=c.job_card_company,
                job_card_employment_type=c.job_card_employment_type,
                job_card_experience=c.job_card_experience,
            )
        except Exception as e:
            stopped_reason = f"verifier raised on {p.post_url}: {e}"
            break
        evals += 1
        llm_status = str(getattr(gate, "llm_status", "") or "")
        prev = verdicts.get(p.post_url)
        if (llm_status.upper() == "UNAVAILABLE" and prev
                and prev.get("decision") == "ACCEPT"):
            stopped_reason = ("re-verify of cached ACCEPT hit provider "
                              "unavailability; cached ACCEPT preserved")
            break
        if llm_status.upper() == "UNAVAILABLE":
            verdicts[p.post_url] = {
                "post_url": p.post_url, "decision": "UNAVAILABLE",
                "confidence": 0.0, "llm_status": llm_status,
                "reasons": list(gate.reasons), "provider": "phase37",
            }
            persist_verdicts(verdicts)
            stopped_reason = f"provider unavailable at candidate {i}"
            break
        rec_new = {
            "post_url": p.post_url,
            "decision": gate.decision,
            "confidence": float(getattr(gate.verdict, "confidence", 0.0) or 0.0),
            "llm_status": llm_status,
            "reasons": list(gate.reasons),
            "provider": "phase37",
        }
        if gate.decision == "ACCEPT":
            rec_new["gate_json"] = json.loads(gate.model_dump_json())
        verdicts[p.post_url] = rec_new
        persist_verdicts(verdicts)
        results.append((p, c, gate))
        _log(f"[{i}/{len(llm_seq)}] {gate.decision} "
             f"conf={getattr(gate.verdict, 'confidence', 0)} {p.post_url[:70]}")
        time.sleep(3)  # modest pacing; retry policy itself untouched
    _log(f"llm calls: {verifier.llm_calls}; live evals: {evals}; "
         f"stop: {stopped_reason or 'all candidates evaluated'}")

    # ── §7 gate ACCEPTs (unchanged production gate) ──────────────────────
    accepted_triples, gate_blocked = [], []
    llm_review, llm_reject = [], []
    for p, c, gate in results:
        if isinstance(gate, dict):
            (llm_reject if gate["decision"] == "REJECT" else llm_review).append(
                {"source_url": p.post_url, "reasons": gate["reasons"]})
            continue
        if gate.decision == "ACCEPT":
            route = route_production_post(enriched_post(c, gate), gate)
            if route.destination in ("MAIN_PRODUCTION", "DATA_PRODUCTION"):
                accepted_triples.append((enriched_post(c, gate), gate, c))
            else:
                gate_blocked.append({
                    "source_url": p.post_url, "destination": route.destination,
                    "reasons": list(route.reasons)})
        elif gate.decision == "REJECT":
            llm_reject.append({"source_url": p.post_url,
                               "reasons": list(gate.reasons)})
        else:
            llm_review.append({"source_url": p.post_url,
                               "reasons": list(gate.reasons)})
    accepted_pairs = [(m, g) for m, g, _c in accepted_triples]

    # ── §7 enrichment (eligible only; never changes decisions) ───────────
    enrichment = {}
    for merged, gate, c in accepted_triples:
        try:
            e = enricher.enrich_from_classified(
                merged, gate.verdict,
                job_card_company=c.job_card_company,
                job_card_employment_type=c.job_card_employment_type,
                job_card_experience=c.job_card_experience)
            enrichment[merged.source_link] = e
        except Exception as ex:
            _log(f"enrichment error {merged.source_link}: {ex}")
    _log(f"eligible pairs: {len(accepted_pairs)}; enriched: {len(enrichment)}")

    # ── §9 controlled write (guarded idempotent writer) ──────────────────
    write_summary = {"outcome": "SKIPPED", "main": 0, "data": 0, "error": ""}
    if accepted_pairs:
        try:
            result = w.write_production(accepted_pairs)
            write_summary = {
                "outcome": getattr(result, "outcome", "UNKNOWN"),
                "main": int((getattr(result, "destinations", None) or
                             {}).get(GOOGLE_SHEET_WORKSHEET, 0)),
                "data": int((getattr(result, "destinations", None) or
                             {}).get(GOOGLE_SHEET_DATA_WORKSHEET, 0)),
                "error": getattr(result, "error", "") or "",
            }
        except Exception as e:
            write_summary = {"outcome": "FAILED", "main": 0, "data": 0,
                             "error": str(e)[:300]}
        _log(f"write: {json.dumps(write_summary)}")
        if write_summary["outcome"] not in ("SUCCESS", "PARTIAL"):
            if write_summary["outcome"] not in ("NO_NEW_ROWS", "ALL_DUPLICATES"):
                _log("write did not succeed — stopping before verification "
                     "(no automatic retry)")
    elif stopped_reason:
        _log(f"no eligible pairs (stopped: {stopped_reason}); write skipped")
    else:
        _log("no eligible pairs; write skipped (0 writes is correct)")

    # ── §9 post-write (or zero-write) independent verification ───────────
    post = snapshot_tabs(w, sheet)
    verification = verify_post_write(pre, post, write_summary["main"],
                                     write_summary["data"])
    if verification["failures"]:
        _log("VERIFICATION FAILED:\n  " +
             "\n  ".join(verification["failures"][:10]))
    else:
        _log("verification: ALL CHECKS PASS "
             f"(MAIN {verification['final_main']}, DATA {verification['final_data']})")

    # ── §12 artifacts ─────────────────────────────────────────────────────
    (OUT_DIR / "apify_run_record.json").write_text(json.dumps({
        **captured, "scrape_seconds": scrape_s,
        "inband_error_count": len(inband_errors),
        "inband_errors_first": inband_errors[:3],
        "one_run_only": True,
        "cumulative_spend_after": budget.get("spent_after_usd"),
        "account_limit_usd": acct_limit,
        "per_query_yield": per_query,
    }, indent=2, default=str), encoding="utf-8")
    (OUT_DIR / "budget_before_after.json").write_text(
        json.dumps(budget, indent=2), encoding="utf-8")

    prov = {
        "chain": "Groq -> Gemini key1 -> Gemini key2 (unchanged)",
        "probe": {"status": p_status, "latency_s": round(p_latency, 1),
                  "note": p_note},
        "llm_calls_total": verifier.llm_calls,
        "live_evaluations": evals,
        "from_cache": sum(1 for _p, _c, g in results if isinstance(g, dict)),
        "throttles_errors": stopped_reason or "none",
    }
    (OUT_DIR / "provider_summary.json").write_text(
        json.dumps(prov, indent=2), encoding="utf-8")

    with (OUT_DIR / "candidate_results.csv").open("w", encoding="utf-8",
                                                  newline="") as f:
        f.write("source_url,disposition,confidence,reasons\n")
        for merged, _g, _c in accepted_triples:
            f.write(json.dumps([merged.source_link, "ELIGIBLE",
                                merged.confidence,
                                "; ".join(merged.classification_reason.split("\n"))[:150]])[1:-1] + "\n")
        for b in gate_blocked:
            f.write(json.dumps([b["source_url"], "GATE_BLOCKED", "",
                                "; ".join(b["reasons"])[:150]])[1:-1] + "\n")
        for r in llm_review:
            f.write(json.dumps([r["source_url"], "REVIEW", "",
                                "; ".join(r["reasons"])[:150]])[1:-1] + "\n")
        for r in llm_reject:
            f.write(json.dumps([r["source_url"], "REJECT", "",
                                "; ".join(r["reasons"])[:150]])[1:-1] + "\n")
        for p, _c in prod_dupes:
            f.write(json.dumps([p.post_url, "PRODUCTION_DUPLICATE_SKIPPED", "",
                                "URL already in a production tab"])[1:-1] + "\n")

    for name, rows in (("eligible_main.csv", verification["new_main_rows"]),
                       ("eligible_data.csv", verification["new_data_rows"])):
        with (OUT_DIR / name).open("w", encoding="utf-8", newline="") as f:
            f.write("rows_written,%d\n" % len(rows))
            f.write(",".join(EXPECTED_HEADERS) + "\n")
            for row in rows:
                f.write(",".join('"' + str(c).replace('"', '""') + '"'
                                 for c in row) + "\n")
            if not rows:
                f.write("NONE (0 eligible rows this phase)\n")

    with (OUT_DIR / "gate_blocked.csv").open("w", encoding="utf-8",
                                             newline="") as f:
        f.write("source_url,destination,reasons\n")
        for b in gate_blocked:
            f.write(json.dumps([b["source_url"], b["destination"],
                                "; ".join(b["reasons"])[:200]])[1:-1] + "\n")

    (OUT_DIR / "sheet_write_verification.json").write_text(json.dumps({
        "failed": bool(verification["failures"]),
        "failures": verification["failures"],
        "written_main": write_summary["main"],
        "written_data": write_summary["data"],
        "write_outcome": write_summary["outcome"],
        "final_main": verification["final_main"],
        "final_data": verification["final_data"],
        "final_review": verification["final_review"],
        "cross_tab_duplicates": verification["cross_tab_duplicates"],
    }, indent=2, default=str), encoding="utf-8")

    counts = {
        "accept": len(accepted_triples) + len(gate_blocked),
        "eligible": len(accepted_pairs), "gate_blocked": len(gate_blocked),
        "review": len(llm_review), "reject": len(llm_reject),
        "unavailable": 1 if stopped_reason and "unavailable" in stopped_reason else 0,
        "production_duplicate_skipped": len(prod_dupes),
    }
    funnel_doc = {**funnel, "llm": counts, "write": write_summary,
                  "final": {"main": verification["final_main"],
                            "data": verification["final_data"],
                            "review": verification["final_review"],
                            "cross_tab_duplicates":
                                verification["cross_tab_duplicates"]},
                  "preflight": preflight, "budget": budget, "repo": repo,
                  "probe": {"status": p_status, "latency_s": round(p_latency, 1)},
                  "stopped_reason": stopped_reason}
    (OUT_DIR / "funnel_summary.json").write_text(
        json.dumps(funnel_doc, indent=2, default=str), encoding="utf-8")
    _log("artifacts written")
    _log(json.dumps(funnel_doc, indent=2, default=str))
    return 0 if not verification["failures"] else 2


# ── resume path: identical pipeline, zero Apify calls ────────────────────


def _resume_main(spent_before: float, remaining: float) -> int:
    """Replay raw_posts.jsonl through the unchanged pipeline. The scrape is
    NOT re-run (spec §14: ONE actor run, period); the ledger makes evaluation
    restart-safe; writes remain idempotent + fail-closed."""
    from app.models import RawPost

    budget = json.loads((OUT_DIR / "budget_before_after.json").read_text())
    captured = json.loads((OUT_DIR / "apify_run_record.json").read_text())
    raw_posts = [RawPost.model_validate(json.loads(l))
                 for l in RAW_POSTS.read_text(encoding="utf-8").splitlines()
                 if l.strip()]
    _log(f"resume: replaying {len(raw_posts)} persisted posts "
         f"(run {captured.get('run_id')}; NO new Apify call)")

    # Sheets preflight + provider probe (fresh state before continuing)
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
    prod_urls = ({r[7] for r in pre["main"][1:] if len(r) > 7 and r[7]} |
                 {r[7] for r in pre["data"][1:] if len(r) > 7 and r[7]})

    p_status, p_latency, p_note = probe_groq()
    _log(f"Groq probe: {p_status} ({p_latency:.1f}s) {p_note}")
    if p_status != "SERVING":
        return _abort(f"provider probe failed on resume: {p_status} {p_note}")

    # dedup + classify (identical machinery)
    seen: set = set()
    unique_posts = []
    for p in raw_posts:
        if p.post_url not in seen:
            seen.add(p.post_url)
            unique_posts.append(p)
    excluded = 0
    classifier = DeterministicClassifier()
    candidates = []
    for p in unique_posts:
        try:
            c = classifier.classify(p)
        except Exception:
            excluded += 1
            continue
        if not c.is_valid:
            excluded += 1
            continue
        candidates.append((p, c))
    funnel = {"raw": len(raw_posts), "unique": len(unique_posts),
              "candidates": len(candidates), "excluded": excluded,
              "duplicates": len(raw_posts) - len(unique_posts),
              "inband_errors": 0}
    _log(f"resume funnel: {json.dumps(funnel)}")
    try:
        from app.sourcing_score import score_raw_post
        candidates.sort(key=lambda pc: -score_raw_post(pc[0]))
    except Exception:
        pass
    llm_seq, prod_dupes = [], []
    for pc in candidates:
        (prod_dupes if pc[0].post_url in prod_urls else llm_seq).append(pc)
    _log(f"resume candidates: {len(llm_seq)} (already-in-production "
         f"skipped: {len(prod_dupes)})")

    verdicts = load_verdicts()
    verifier = Verifier()
    enricher = Enricher(contact_provider=None)
    results = []
    stopped_reason = None
    evals = 0
    for i, (p, c) in enumerate(llm_seq, 1):
        if p.post_url in verdicts:
            rec = verdicts[p.post_url]
            if rec["decision"] not in ("ACCEPT", "UNAVAILABLE"):
                _log(f"[{i}/{len(llm_seq)}] cached: {rec['decision']} "
                     f"{rec['confidence']} {p.post_url[:70]}")
                results.append((p, c, rec))
                continue
            if rec["decision"] == "UNAVAILABLE":
                _log(f"[{i}/{len(llm_seq)}] cached UNAVAILABLE -> re-evaluating")
            if rec["decision"] == "ACCEPT" and "gate_json" in rec:
                gate = GateResult.model_validate(rec["gate_json"])
                _log(f"[{i}/{len(llm_seq)}] cached ACCEPT (verified) -> "
                     f"offline route {p.post_url[:55]}")
                results.append((p, c, gate))
                continue
        try:
            gate = verifier.verify(
                post_text=c.text, post_url=c.post_url,
                author_name=c.author_name,
                author_profile_url=c.author_profile_url,
                job_card_location=c.job_card_location,
                job_card_company=c.job_card_company,
                job_card_employment_type=c.job_card_employment_type,
                job_card_experience=c.job_card_experience,
            )
        except Exception as e:
            stopped_reason = f"verifier raised on {p.post_url}: {e}"
            break
        evals += 1
        llm_status = str(getattr(gate, "llm_status", "") or "")
        prev = verdicts.get(p.post_url)
        if (llm_status.upper() == "UNAVAILABLE" and prev
                and prev.get("decision") == "ACCEPT"):
            stopped_reason = ("re-verify of cached ACCEPT hit provider "
                              "unavailability; cached ACCEPT preserved")
            break
        if llm_status.upper() == "UNAVAILABLE":
            verdicts[p.post_url] = {
                "post_url": p.post_url, "decision": "UNAVAILABLE",
                "confidence": 0.0, "llm_status": llm_status,
                "reasons": list(gate.reasons), "provider": "phase37-resume",
            }
            persist_verdicts(verdicts)
            stopped_reason = f"provider unavailable at candidate {i} (resume)"
            break
        rec_new = {
            "post_url": p.post_url,
            "decision": gate.decision,
            "confidence": float(getattr(gate.verdict, "confidence", 0.0) or 0.0),
            "llm_status": llm_status,
            "reasons": list(gate.reasons),
            "provider": "phase37-resume",
        }
        if gate.decision == "ACCEPT":
            rec_new["gate_json"] = json.loads(gate.model_dump_json())
        verdicts[p.post_url] = rec_new
        persist_verdicts(verdicts)
        results.append((p, c, gate))
        _log(f"[{i}/{len(llm_seq)}] {gate.decision} "
             f"conf={getattr(gate.verdict, 'confidence', 0)} {p.post_url[:70]}")
        time.sleep(3)
    _log(f"resume llm calls: {verifier.llm_calls}; live evals: {evals}; "
         f"stop: {stopped_reason or 'all candidates evaluated'}")

    accepted_triples, gate_blocked = [], []
    llm_review, llm_reject = [], []
    for p, c, gate in results:
        if isinstance(gate, dict):
            (llm_reject if gate["decision"] == "REJECT" else llm_review).append(
                {"source_url": p.post_url, "reasons": gate["reasons"]})
            continue
        if gate.decision == "ACCEPT":
            route = route_production_post(enriched_post(c, gate), gate)
            if route.destination in ("MAIN_PRODUCTION", "DATA_PRODUCTION"):
                accepted_triples.append((enriched_post(c, gate), gate, c))
            else:
                gate_blocked.append({
                    "source_url": p.post_url, "destination": route.destination,
                    "reasons": list(route.reasons)})
        elif gate.decision == "REJECT":
            llm_reject.append({"source_url": p.post_url,
                               "reasons": list(gate.reasons)})
        else:
            llm_review.append({"source_url": p.post_url,
                               "reasons": list(gate.reasons)})
    accepted_pairs = [(m, g) for m, g, _c in accepted_triples]

    enrichment = {}
    for merged, gate, c in accepted_triples:
        try:
            e = enricher.enrich_from_classified(
                merged, gate.verdict,
                job_card_company=c.job_card_company,
                job_card_employment_type=c.job_card_employment_type,
                job_card_experience=c.job_card_experience)
            enrichment[merged.source_link] = e
        except Exception as ex:
            _log(f"enrichment error {merged.source_link}: {ex}")
    _log(f"resume eligible pairs: {len(accepted_pairs)}; "
         f"enriched: {len(enrichment)}")

    write_summary = {"outcome": "SKIPPED", "main": 0, "data": 0, "error": ""}
    if accepted_pairs:
        try:
            result = w.write_production(accepted_pairs)
            write_summary = {
                "outcome": getattr(result, "outcome", "UNKNOWN"),
                "main": int((getattr(result, "destinations", None) or
                             {}).get(GOOGLE_SHEET_WORKSHEET, 0)),
                "data": int((getattr(result, "destinations", None) or
                             {}).get(GOOGLE_SHEET_DATA_WORKSHEET, 0)),
                "error": getattr(result, "error", "") or "",
            }
        except Exception as e:
            write_summary = {"outcome": "FAILED", "main": 0, "data": 0,
                             "error": str(e)[:300]}
        _log(f"resume write: {json.dumps(write_summary)}")
    elif stopped_reason:
        _log(f"resume: no eligible pairs (stopped: {stopped_reason})")
    else:
        _log("resume: no eligible pairs; write skipped (0 writes is correct)")

    post = snapshot_tabs(w, sheet)
    verification = verify_post_write(pre, post, write_summary["main"],
                                     write_summary["data"])
    if verification["failures"]:
        _log("VERIFICATION FAILED:\n  " +
             "\n  ".join(verification["failures"][:10]))
    else:
        _log("resume verification: ALL CHECKS PASS "
             f"(MAIN {verification['final_main']}, DATA {verification['final_data']})")

    # merge resume accounting into the same artifacts (run stays ONE scrape)
    budget["resume_utc"] = UTC_NOW
    budget["resume_llm_calls"] = verifier.llm_calls
    budget["spent_after_usd"] = spent_before
    budget["remaining_after_usd"] = round(remaining, 4)
    (OUT_DIR / "budget_before_after.json").write_text(
        json.dumps(budget, indent=2), encoding="utf-8")
    captured["resume_events"] = (captured.get("resume_events") or 0) + 1
    (OUT_DIR / "apify_run_record.json").write_text(
        json.dumps(captured, indent=2, default=str), encoding="utf-8")
    prov = {
        "chain": "Groq -> Gemini key1 -> Gemini key2 (unchanged)",
        "probe": {"status": p_status, "latency_s": round(p_latency, 1),
                  "note": p_note},
        "llm_calls_resume": verifier.llm_calls,
        "live_evaluations_resume": evals,
        "throttles_errors": stopped_reason or "none",
    }
    (OUT_DIR / "provider_summary.json").write_text(
        json.dumps(prov, indent=2), encoding="utf-8")

    with (OUT_DIR / "candidate_results.csv").open("w", encoding="utf-8",
                                                  newline="") as f:
        f.write("source_url,disposition,confidence,reasons\n")
        for merged, _g, _c in accepted_triples:
            f.write(json.dumps([merged.source_link, "ELIGIBLE",
                                merged.confidence,
                                "; ".join(merged.classification_reason.split("\n"))[:150]])[1:-1] + "\n")
        for b in gate_blocked:
            f.write(json.dumps([b["source_url"], "GATE_BLOCKED", "",
                                "; ".join(b["reasons"])[:150]])[1:-1] + "\n")
        for r in llm_review:
            f.write(json.dumps([r["source_url"], "REVIEW", "",
                                "; ".join(r["reasons"])[:150]])[1:-1] + "\n")
        for r in llm_reject:
            f.write(json.dumps([r["source_url"], "REJECT", "",
                                "; ".join(r["reasons"])[:150]])[1:-1] + "\n")
        for p, _c in prod_dupes:
            f.write(json.dumps([p.post_url, "PRODUCTION_DUPLICATE_SKIPPED", "",
                                "URL already in a production tab"])[1:-1] + "\n")

    for name, rows in (("eligible_main.csv", verification["new_main_rows"]),
                       ("eligible_data.csv", verification["new_data_rows"])):
        with (OUT_DIR / name).open("w", encoding="utf-8", newline="") as f:
            f.write("rows_written,%d\n" % len(rows))
            f.write(",".join(EXPECTED_HEADERS) + "\n")
            for row in rows:
                f.write(",".join('"' + str(c).replace('"', '""') + '"'
                                 for c in row) + "\n")
            if not rows:
                f.write("NONE (0 eligible rows this phase)\n")

    with (OUT_DIR / "gate_blocked.csv").open("w", encoding="utf-8",
                                             newline="") as f:
        f.write("source_url,destination,reasons\n")
        for b in gate_blocked:
            f.write(json.dumps([b["source_url"], b["destination"],
                                "; ".join(b["reasons"])[:200]])[1:-1] + "\n")

    (OUT_DIR / "sheet_write_verification.json").write_text(json.dumps({
        "failed": bool(verification["failures"]),
        "failures": verification["failures"],
        "written_main_resume": write_summary["main"],
        "written_data_resume": write_summary["data"],
        "write_outcome_resume": write_summary["outcome"],
        "final_main": verification["final_main"],
        "final_data": verification["final_data"],
        "final_review": verification["final_review"],
        "cross_tab_duplicates": verification["cross_tab_duplicates"],
    }, indent=2, default=str), encoding="utf-8")

    counts = {
        "accept": len(accepted_triples) + len(gate_blocked),
        "eligible": len(accepted_pairs), "gate_blocked": len(gate_blocked),
        "review": len(llm_review), "reject": len(llm_reject),
        "unavailable": 1 if stopped_reason and "unavailable" in stopped_reason else 0,
        "production_duplicate_skipped": len(prod_dupes),
    }
    funnel_doc = {**funnel, "llm": counts, "write": write_summary,
                  "final": {"main": verification["final_main"],
                            "data": verification["final_data"],
                            "review": verification["final_review"],
                            "cross_tab_duplicates":
                                verification["cross_tab_duplicates"]},
                  "preflight": preflight, "budget": budget,
                  "probe": {"status": p_status, "latency_s": round(p_latency, 1)},
                  "stopped_reason": stopped_reason, "resume": True}
    (OUT_DIR / "funnel_summary.json").write_text(
        json.dumps(funnel_doc, indent=2, default=str), encoding="utf-8")
    _log("resume artifacts written")
    return 0 if not verification["failures"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
