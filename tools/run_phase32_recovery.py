"""
Phase 32 RECOVERY — re-evaluation of run QoFA6qQmhCNZHZRe9's candidates.

Context: the Phase 32 supervised cycle completed its Apify scrape (48/48
queries, 390 raw / 263 unique / 45 deterministic candidates) but a Windows
DNS outage mid-run made both LLM providers unavailable (every fallback
landed REVIEW "provider error") and failed the writer's URL read — which
FAIL-CLOSED (write_outcome=READ_FAILED, zero Sheet mutations). In-memory
verdicts were lost with the process. This runner implements the established
recovery policy (Phase 28/29.4 pattern): re-evaluate the SAME candidates
from the SAME scrape — the Apify dataset is immutable and free to re-read —
through the existing healthy chain, then gate / guarded-write / verify.

  1. Connectivity + Sheets preflight (reused phase29 helpers) + pre snapshot.
  2. ONE real Groq structured probe (SERVING required).
  3. Recover dataset 08RHZWNPWm2UvEz46 by ID (zero Apify spend); rebuild the
     exact funnel (dedup -> classifier) and CROSS-CHECK the counts against
     the crashed run's log (390/263/45/218/127). Mismatch -> STOP.
  4. Re-evaluate candidates through Verifier (existing chain, unchanged
     retry/cooldown/breaker). Verdicts persisted CACHE-FIRST to
     verdicts.jsonl (atomic replace) after EVERY candidate; restart-safe.
     First candidate-level provider unavailability -> persist + STOP.
  5. ACCEPTs -> unchanged production gate (route_production_post); gate-held
     ACCEPTs are recorded, never written. Eligible pairs -> enrichment ->
     guarded SheetsWriter.write_production (writer re-validates fail-closed).
  6. Independent post-write verification (reused phase32 verify_post_write)
     or zero-write tab verification if nothing was eligible.
  7. Artifacts in data/validation/phase32_final_production_run/.

No gate/taxonomy/verifier/retry/TLS/.env changes. No commits.

Usage: python tools/run_phase32_recovery.py
"""
from __future__ import annotations

import io
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# In-process production override (run_daily.py pattern) — before app.config.
os.environ["DRY_RUN"] = "false"

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
# reconfigure() mutates the existing wrapper in place — creating a second
# TextIOWrapper over sys.stdout.buffer here would leave the first wrapper
# unreferenced; its __del__ closes the SHARED buffer and every later print
# fails with "I/O operation on closed file" (observed live).
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace",
                           line_buffering=True)
    sys.stderr.reconfigure(encoding="utf-8", errors="replace",
                           line_buffering=True)
except Exception:
    pass

from app.classifier import DeterministicClassifier  # noqa: E402
from app.config import (  # noqa: E402
    GOOGLE_SHEET_DATA_WORKSHEET, GOOGLE_SHEET_WORKSHEET,
    GOOGLE_SHEETS_CREDENTIALS, GOOGLE_SHEETS_ID,
)
from app.enrichment import Enricher  # noqa: E402
from app.llm.schemas import GateResult  # noqa: E402
from app.llm.verifier import Verifier, enriched_post  # noqa: E402
from app.production_gate import route_production_post  # noqa: E402
from app.sheets_writer import EXPECTED_HEADERS, SheetsWriter  # noqa: E402
from app.sources import datadoping_source as dds  # noqa: E402
from tools.run_phase29_tranche import (  # noqa: E402
    probe_groq, sheets_preflight,
)
from tools.run_phase32_production import (  # noqa: E402  (established runner)
    snapshot_tabs, verify_post_write,
)

RUN_ID = "QoFA6qQmhCNZHZRe9"
DATASET_ID = "08RHZWNPWm2UvEz46"
# Funnel counts recorded by the crashed run's log — the dataset is immutable,
# so the rebuild must reproduce them exactly.
EXPECTED_FUNNEL = {"raw": 390, "unique": 263, "candidates": 45,
                   "excluded": 218, "duplicates": 127}

OUT_DIR = ROOT / "data" / "validation" / "phase32_final_production_run"
VERDICTS = OUT_DIR / "verdicts.jsonl"
UTC_NOW = datetime.now(timezone.utc).isoformat()


def _log(msg: str) -> None:
    print(msg, flush=True)


def _abort(reason: str) -> int:
    _log(f"ABORT: {reason}")
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "abort_reason.txt").write_text(
        f"{UTC_NOW}\nRECOVERY ABORT: {reason}\n", encoding="utf-8")
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


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    _log(f"=== PHASE 32 RECOVERY @ {UTC_NOW} ===")

    # ── connectivity + preflight ──────────────────────────────────────────
    import socket
    for host in ("sheets.googleapis.com", "oauth2.googleapis.com",
                 "api.groq.com"):
        try:
            socket.gethostbyname(host)
        except OSError as e:
            return _abort(f"network still down: {host}: {e}")

    w = SheetsWriter(GOOGLE_SHEETS_CREDENTIALS, GOOGLE_SHEETS_ID,
                     GOOGLE_SHEET_WORKSHEET, dry_run=False,
                     data_worksheet_name=GOOGLE_SHEET_DATA_WORKSHEET)
    if w.client is None:
        return _abort("Sheets auth failed")
    sheet = w.client.open_by_key(GOOGLE_SHEETS_ID)
    try:
        preflight = sheets_preflight(w, sheet)
    except Exception as e:
        return _abort(f"Sheets preflight failed: {e}")
    _log(f"preflight OK: {json.dumps(preflight)}")
    pre = snapshot_tabs(w, sheet)

    p_status, p_latency, p_note = probe_groq()
    _log(f"Groq probe: {p_status} ({p_latency:.1f}s) {p_note}")
    if p_status != "SERVING":
        return _abort(f"provider probe failed: {p_status} {p_note}")

    # ── recover dataset + rebuild funnel (zero Apify spend) ──────────────
    from apify_client import ApifyClient
    from app.config import APIFY_API_TOKEN
    dataset = ApifyClient(APIFY_API_TOKEN).dataset(DATASET_ID)
    items = list(dataset.iterate_items())
    # Established mapper-construction pattern (tools/gate_25_6s.py): the
    # mapper is instance-level; a dummy token is safe — no actor call is made
    # (the dataset is read by ID, which is free).
    mapper = dds.DataDopingSource("offline-replay-no-apify-call")
    raw_posts = []
    inband_errors = 0
    for item in items:
        if item.get("error"):
            inband_errors += 1
            continue
        post = mapper._to_raw_post(item)
        if post is not None:
            raw_posts.append(post)
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
              "inband_errors": inband_errors}
    _log(f"rebuilt funnel: {json.dumps(funnel)}")
    # Compare ONLY the five immutable funnel keys (inband_errors is a
    # recovery-time observation, not part of the crashed run's counters).
    if {k: funnel[k] for k in EXPECTED_FUNNEL} != EXPECTED_FUNNEL:
        return _abort(f"funnel mismatch vs crashed run: {funnel} != {EXPECTED_FUNNEL}")

    # sourcing-score order (same as orchestrator; order-only, no discards)
    try:
        from app.sourcing_score import score_raw_post
        candidates.sort(key=lambda pc: -score_raw_post(pc[0]))
    except Exception:
        pass

    # ── re-evaluate (cache-first, provider stop rule) ────────────────────
    verifier = Verifier()
    enricher = Enricher(contact_provider=None)
    verdicts = load_verdicts()
    results = []          # (post, classified, gate) for evaluated candidates
    stopped_reason = None

    # Phase 32 window-order policy: fresh candidates run FIRST so every
    # capacity burst harvests new verdicts; cached-ACCEPT re-verifications
    # (no stored gate yet) retry at the END of the window. A successful
    # re-verify persists gate_json, after which it routes offline forever.
    _deferred_urls = {
        p.post_url for p, c in candidates
        if p.post_url in verdicts
        and verdicts[p.post_url].get("decision") == "ACCEPT"
        and "gate_json" not in verdicts[p.post_url]
    }
    live_seq = ([(p, c) for p, c in candidates if p.post_url not in _deferred_urls]
                + [(p, c) for p, c in candidates if p.post_url in _deferred_urls])

    for i, (p, c) in enumerate(live_seq, 1):
        if p.post_url in verdicts:
            rec = verdicts[p.post_url]
            if rec["decision"] not in ("ACCEPT", "UNAVAILABLE"):
                # Cached REVIEW/REJECT: resolved — never re-evaluated.
                _log(f"[{i}/{len(candidates)}] cached: {rec['decision']} "
                     f"{rec['confidence']} {p.post_url[:70]}")
                results.append((p, c, rec))
                continue
            if rec["decision"] == "UNAVAILABLE":
                # Cached UNAVAILABLE: UNRESOLVED (recovery policy) —
                # re-evaluate in this window once providers are SERVING
                # again; overwrite the record below.
                _log(f"[{i}/{len(candidates)}] cached UNAVAILABLE -> re-evaluating "
                     f"{p.post_url[:60]}")
            if rec["decision"] == "ACCEPT" and "gate_json" in rec:
                # Phase 32 call-hygiene: a live-verified ACCEPT is persisted
                # with its GateResult and re-routes offline on restart —
                # no repeat verification, no wasted provider calls.
                gate = GateResult.model_validate(rec["gate_json"])
                _log(f"[{i}/{len(candidates)}] cached ACCEPT (verified) -> "
                     f"offline route {p.post_url[:55]}")
                results.append((p, c, gate))
                continue
            # Cached ACCEPT without a stored gate: re-verify this one
            # candidate live (once — a success persists gate_json).
            _log(f"[{i}/{len(candidates)}] cached ACCEPT -> re-verify live")
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
        # llm_status lives on the GateResult (not the LeadVerdict) — see
        # verifier.py fallback: GateResult(..., llm_status="UNAVAILABLE").
        llm_status = str(getattr(gate, "llm_status", "") or "")
        prev = verdicts.get(p.post_url)
        if (llm_status.upper() == "UNAVAILABLE" and prev
                and prev.get("decision") == "ACCEPT"):
            # Never downgrade a resolved ACCEPT with a transient provider
            # failure during re-verification. Preserve the cache; this
            # window cannot route without a live gate -> stop.
            stopped_reason = ("re-verify of cached ACCEPT hit provider "
                              "unavailability; cached ACCEPT preserved")
            break
        if llm_status.upper() == "UNAVAILABLE":
            verdicts[p.post_url] = {
                "post_url": p.post_url, "decision": "UNAVAILABLE",
                "confidence": 0.0, "llm_status": llm_status,
                "reasons": list(gate.reasons), "provider": "recovery-run",
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
            "provider": "recovery-run",
        }
        if gate.decision == "ACCEPT":
            # Full GateResult snapshot -> future restarts route this ACCEPT
            # offline (no re-verification calls).
            rec_new["gate_json"] = json.loads(gate.model_dump_json())
        verdicts[p.post_url] = rec_new
        persist_verdicts(verdicts)
        results.append((p, c, gate))
        _log(f"[{i}/{len(candidates)}] {gate.decision} "
             f"conf={getattr(gate.verdict, 'confidence', 0)} "
             f"{p.post_url[:70]}")
        # Modest pacing between heavy evaluations to reduce rate-limit
        # pressure on the free tier (retry policy itself untouched).
        time.sleep(3)
    _log(f"llm calls (recovery): {verifier.llm_calls}; "
         f"stop: {stopped_reason or 'all candidates evaluated'}")

    # ── gate ACCEPTs; collect eligible pairs ─────────────────────────────
    accepted_triples = []  # (merged, gate, classified) ELIGIBLE
    gate_blocked = []      # ACCEPT but gate-held
    llm_review, llm_reject = [], []
    for p, c, gate in results:
        if isinstance(gate, dict):  # cached non-ACCEPT verdict
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
    accepted_pairs = [(m, g) for m, g, _c in accepted_triples]  # writer shape

    # ── enrichment for eligible (Phase 6; never changes the decision) ────
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

    # ── controlled write (guarded writer; fail-closed URL read) ──────────
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
    elif stopped_reason:
        _log(f"no eligible pairs (stopped: {stopped_reason}); write skipped")
    else:
        _log("no eligible pairs; write skipped (0 writes is correct)")

    # ── post-write (or zero-write) verification ──────────────────────────
    post = snapshot_tabs(w, sheet)
    verification = verify_post_write(pre, post, write_summary["main"],
                                     write_summary["data"])
    if verification["failures"]:
        _log("VERIFICATION FAILED:\n  " +
             "\n  ".join(verification["failures"][:10]))
    else:
        _log("verification: ALL CHECKS PASS "
             f"(MAIN {verification['final_main']}, DATA {verification['final_data']})")

    # ── artifacts ─────────────────────────────────────────────────────────
    (OUT_DIR / "apify_run_record.json").write_text(json.dumps({
        "run_id": RUN_ID, "dataset_id": DATASET_ID,
        "status": "SUCCEEDED", "recovered_at": UTC_NOW,
        "original_run": {"utc": "2026-09-21T19:22Z",
                         "outcome": "scrape SUCCEEDED; LLM degraded by DNS outage; "
                                    "write fail-closed (READ_FAILED); 0 mutations"},
        "spend_usd_cycle_after_run": 3.6125,
        "recovery_apify_spend_usd": 0.0,
    }, indent=2), encoding="utf-8")

    funnel_doc = {**funnel, "llm": {
        "evaluated_this_run": sum(1 for _r, _c, g in results
                                  if not isinstance(g, dict)),
        "from_cache": sum(1 for _r, _c, g in results if isinstance(g, dict)),
        "accept": len(accepted_triples) + len(gate_blocked),
        "eligible": len(accepted_pairs), "gate_blocked": len(gate_blocked),
        "review": len(llm_review), "reject": len(llm_reject),
        "unavailable": 1 if stopped_reason and "unavailable" in stopped_reason else 0,
        "llm_calls": verifier.llm_calls,
        "stopped_reason": stopped_reason,
    }, "write": write_summary,
        "final": {"main": verification["final_main"],
                  "data": verification["final_data"],
                  "review": verification["final_review"],
                  "cross_tab_duplicates": verification["cross_tab_duplicates"]},
        "preflight": preflight}
    (OUT_DIR / "funnel_summary.json").write_text(
        json.dumps(funnel_doc, indent=2, default=str), encoding="utf-8")

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

    (OUT_DIR / "provider_summary.json").write_text(json.dumps({
        "chain": "Groq -> Gemini key1 -> Gemini key2 (unchanged)",
        "probe": {"status": p_status, "latency_s": round(p_latency, 1)},
        "llm_calls_recovery": verifier.llm_calls,
        "llm_calls_original_run": 162,
        "original_run_contamination": "DNS outage degraded all remaining "
                                      "verdicts to REVIEW 'provider error'; "
                                      "discarded per recovery policy",
        "throttles_errors": stopped_reason or "none",
    }, indent=2), encoding="utf-8")

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

    (OUT_DIR / "enrichment_counts.json").write_text(json.dumps({
        "eligible_total": len(accepted_pairs),
        "enrichment_results": len(enrichment),
        "hm_available": sum(1 for e in enrichment.values()
                            if getattr(e, "hiring_manager_linkedin", None)),
        "email_available": sum(1 for e in enrichment.values()
                               if getattr(e, "cold_email", None)),
        "apply_link_available": sum(1 for e in enrichment.values()
                                    if getattr(e, "apply_link", None)),
    }, indent=2), encoding="utf-8")

    _log("artifacts written")
    rc = 2 if verification["failures"] else 0
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
