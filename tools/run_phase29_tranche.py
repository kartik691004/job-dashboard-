"""
Phase 29 — Resume existing 21-candidate backlog (tranche 1: max 3).

Strictly bounded by the Phase 29 spec:
  1. MANDATORY read-only Sheets preflight (MAIN/DATA/Review counts, 23-col
     headers, production invariants, cross-tab dedup, GIRI/NEC/FourKites/
     Indecimal presence). Any failure -> STOP before candidates.
  2. ONE real structured Groq probe (existing provider, unchanged retry
     policy). Not SERVING -> STOP per Phase 28 recovery policy.
  3. Max 3 unresolved candidates in stable candidates.json order from the
     EXISTING Phase26 dataset (zero Apify). Verdict persisted cache-first
     to verdicts.jsonl via atomic per-record replace; never re-evaluates
     resolved candidates.
  4. FIRST candidate-level provider unavailability -> persist UNAVAILABLE,
     stop the tranche, no further candidates.
  5. Every ACCEPT runs the UNCHANGED production gate + write barrier.
  6. Controlled write only via the current guarded SheetsWriter
     (write_production), followed by full post-write verification.
No gate/taxonomy/query/verifier/retry/TLS/.env changes; no commits.

Usage: python tools/run_phase29_tranche.py
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from app.classifier import DeterministicClassifier  # noqa: E402
from app.config import (  # noqa: E402
    GOOGLE_SHEET_DATA_WORKSHEET, GOOGLE_SHEET_WORKSHEET,
    GOOGLE_SHEETS_CREDENTIALS, GOOGLE_SHEETS_ID,
)
from app.enrichment import Enricher  # noqa: E402
from app.llm.verifier import Verifier, enriched_post  # noqa: E402
from app.production_gate import (  # noqa: E402
    DATA_PRODUCTION, MAIN_PRODUCTION, route_production_post,
)
from app.sheets_writer import EXPECTED_HEADERS, SheetsWriter  # noqa: E402
from app.sources import datadoping_source as dds  # noqa: E402

RUN_DIR = ROOT / "data" / "validation" / "phase26_fresh_run"
RAW_JSON = RUN_DIR / "raw_dataset.json"
VERDICTS = RUN_DIR / "verdicts.jsonl"
OUT_DIR = ROOT / "data" / "validation" / "phase29_backlog_tranche1"

MAX_CANDIDATES = 3
REVIEW_TAB = "Review / Held"

_BARRIER_MISSING = {"unclear", "n/a", "na", "none", "unknown", "",
                    "undisclosed", "our client", "confidential", "stealth"}


def _log(msg: str) -> None:
    print(msg, flush=True)


def load_verdicts() -> dict[str, dict]:
    out: dict[str, dict] = {}
    for line in VERDICTS.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            rec = json.loads(line)
            out[rec["post_url"]] = rec
    return out


def persist_verdicts_atomic(records: dict[str, dict]) -> None:
    """Cache-first persistence: atomic whole-file replace (33+ records)."""
    tmp = VERDICTS.with_suffix(".jsonl.tmp29")
    with tmp.open("w", encoding="utf-8", newline="\n") as f:
        for rec in records.values():
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    os.replace(tmp, VERDICTS)


def row_invariants(row: list, tab: str, strict: bool = False) -> list[str]:
    """Production invariant checks on ONE 23-col row.

    strict=False (EXISTING rows): the baseline invariant that has governed
    the production tabs since Phase 25.6/26 (company/role present, real
    confidence >= 0.60, status New, valid source URL). Legacy rows written
    before the Phase 25.7 barrier legitimately carry HM_Name='Unclear' with
    HM_LinkedIn resolved; flagging those here would misread the baseline.

    strict=True (NEW rows written by THIS phase): additionally applies the
    Phase 25.7 barrier requirements (HM name must be resolved).
    """
    errs = []
    if len(row) != len(EXPECTED_HEADERS):
        return [f"{tab}: row has {len(row)} cols, expected {len(EXPECTED_HEADERS)}"]
    company = (row[0] or "").strip().lower()
    role = (row[2] or "").strip().lower()
    hm = (row[5] or "").strip().lower()
    src = (row[7] or "").strip()
    conf_raw = (row[9] or "").strip()
    status = (row[18] or "").strip().lower()
    try:
        conf = float(conf_raw)
    except ValueError:
        conf = -1.0
    if status != "new":
        errs.append(f"{tab}: status={row[18]!r}")
    if company in _BARRIER_MISSING:
        errs.append(f"{tab}: company missing/unclear")
    if role in _BARRIER_MISSING:
        errs.append(f"{tab}: exact_role missing/unclear")
    if strict and hm in _BARRIER_MISSING:
        errs.append(f"{tab}: hm_name missing/unclear (25.7 barrier)")
    if not src.startswith("http"):
        errs.append(f"{tab}: source_link invalid")
    if conf < 0.60:
        errs.append(f"{tab}: confidence {conf_raw!r} < 0.60")
    return errs


def sheets_preflight(w: SheetsWriter, sheet) -> dict:
    """Section 1 — read-only. Returns summary; raises on any failure."""
    summary: dict = {}
    if w.client is None:
        raise RuntimeError("SheetsWriter client unavailable (auth failed)")
    main_ws = w.worksheet
    data_ws = w._data_ws()
    if main_ws is None or data_ws is None:
        raise RuntimeError("production tabs unresolved")

    main_rows = main_ws.get_all_values()
    data_rows = data_ws.get_all_values()
    summary["main_rows_incl_header"] = len(main_rows)
    summary["data_rows_incl_header"] = len(data_rows)

    for name, rows in (("MAIN", main_rows), ("DATA", data_rows)):
        if not rows or rows[0] != EXPECTED_HEADERS:
            raise RuntimeError(f"{name}: header != 23-column EXPECTED_HEADERS")
    summary["headers_23col"] = True

    errs: list[str] = []
    for r in main_rows[1:]:
        errs.extend(row_invariants(r, "MAIN"))
    for r in data_rows[1:]:
        errs.extend(row_invariants(r, "DATA"))
    if errs:
        raise RuntimeError("production invariants failed: " + "; ".join(errs[:6]))
    summary["invariants_pass"] = True

    main_urls = {r[7] for r in main_rows[1:] if r[7]}
    data_urls = {r[7] for r in data_rows[1:] if r[7]}
    dupes = main_urls & data_urls
    if dupes:
        raise RuntimeError(f"cross-tab duplicates: {sorted(dupes)[:3]}")
    summary["cross_tab_duplicates"] = 0

    review_ws = sheet.worksheet(REVIEW_TAB)
    review_rows = review_ws.get_all_values()
    summary["review_rows_incl_header"] = len(review_rows)

    main_companies = {(r[0] or "").strip() for r in main_rows[1:]}
    data_companies = {(r[0] or "").strip() for r in data_rows[1:]}
    if "GIRI CONSULTANCY" not in main_companies:
        raise RuntimeError("MAIN: GIRI CONSULTANCY row missing")
    if "Indecimal" not in main_companies:
        raise RuntimeError("MAIN: Indecimal row missing")
    if "NEC Corporation India" not in data_companies:
        raise RuntimeError("DATA: NEC Corporation India row missing")
    if "FourKites" not in data_companies:
        raise RuntimeError("DATA: FourKites row missing")
    summary["giri_nec_fourkites_indecimal"] = True
    return summary


def probe_groq() -> tuple[str, float, str]:
    """ONE real structured Groq probe (Phase 25.6T pattern, unchanged)."""
    from app.config import GROQ_API_KEY, GROQ_MAX_RETRIES, GROQ_MODEL, GROQ_TIMEOUT_S
    from app.llm.groq_provider import GroqProvider

    probe_system = (
        "You are a capacity probe. Return strict JSON with keys: decision, "
        "confidence, reason."
    )
    probe_user = json.dumps({"probe": "capacity", "respond": {
        "decision": "REVIEW", "confidence": 0.5, "reason": "capacity probe"}})
    provider = GroqProvider(GROQ_API_KEY, GROQ_MODEL or "openai/gpt-oss-120b",
                            GROQ_TIMEOUT_S, GROQ_MAX_RETRIES)
    t0 = time.time()
    try:
        payload = provider.complete_json(probe_system, probe_user)
        latency = time.time() - t0
        if not isinstance(payload, dict):
            return "OTHER", latency, "probe returned non-dict JSON"
        ok = ("decision" in payload) and ("confidence" in payload)
        return ("SERVING" if ok else "OTHER"), latency, \
            "" if ok else "probe JSON missing LeadVerdict fields"
    except Exception as e:
        return "PROBE_ERROR", time.time() - t0, str(e)[:200]


def main() -> int:
    started = time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime())
    _log(f"=== PHASE 29 — backlog tranche (max {MAX_CANDIDATES}) ===")
    _log(f"[clock] UTC NOW: {started}")

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    # ── Section 1: MANDATORY Sheets preflight (read-only) ────────────────────
    _log("[preflight] Sheets (read-only; unified TLS hook active) ...")
    try:
        w = SheetsWriter(GOOGLE_SHEETS_CREDENTIALS, GOOGLE_SHEETS_ID,
                         GOOGLE_SHEET_WORKSHEET, dry_run=True,
                         data_worksheet_name=GOOGLE_SHEET_DATA_WORKSHEET)
        sheet = w.client.open_by_key(GOOGLE_SHEETS_ID)
        pre = sheets_preflight(w, sheet)
    except Exception as e:
        _log(f"[STOP] Sheets preflight FAILED: {e}")
        return 1
    _log(f"[preflight] MAIN={pre['main_rows_incl_header'] - 1} rows, "
         f"DATA={pre['data_rows_incl_header'] - 1} rows, "
         f"Review/Held={pre['review_rows_incl_header'] - 1} rows; "
         f"headers 23-col OK; invariants OK; dupes="
         f"{pre['cross_tab_duplicates']}; GIRI/NEC/FourKites/Indecimal OK")

    main_before = pre["main_rows_incl_header"]
    data_before = pre["data_rows_incl_header"]

    # ── Section 2: provider preflight (ONE structured Groq probe) ────────────
    _log("[probe] Groq structured probe ...")
    p_status, p_latency, p_detail = probe_groq()
    _log(f"[probe] Groq: {p_status} ({p_latency:.1f}s) {p_detail}")
    if p_status != "SERVING":
        _log("[STOP] Groq probe not SERVING -> no candidates (Phase28 policy)")
        (OUT_DIR / "stop_report.json").write_text(json.dumps({
            "phase": "29", "at": started, "preflight": pre,
            "probe": {"status": p_status, "latency_s": round(p_latency, 1),
                      "detail": p_detail},
            "candidates_attempted": 0, "rows_written": 0,
        }, indent=1), encoding="utf-8")
        return 1

    verifier = Verifier()
    chain = [type(p).__name__ for p in verifier.providers]
    _log(f"[chain] provider chain: {' -> '.join(chain)}")
    if chain != ["GroqProvider", "GeminiProvider"]:
        _log(f"[STOP] unexpected provider chain {chain}")
        return 1

    # ── Section 3: resolve up to 3 unresolved candidates (stable order) ──────
    records = load_verdicts()
    unresolved = [r["post_url"] for r in records.values()
                  if r.get("llm_status") == "UNAVAILABLE"]
    _log(f"[state] ledger={len(records)} unresolved={len(unresolved)}")

    raw = json.loads(RAW_JSON.read_text(encoding="utf-8"))
    mapper = dds.DataDopingSource("phase29-backlog-replay-no-apify-call")
    raw_by_url: dict[str, object] = {}
    for it in raw["items"]:
        if not isinstance(it, dict) or it.get("error"):
            continue
        p = mapper._to_raw_post(it)
        if p is not None:
            raw_by_url.setdefault(p.post_url, p)
    if not raw_by_url:
        _log("[STOP] dataset mapping produced zero RawPosts")
        return 1
    _log(f"[dataset] mapped {len(raw_by_url)} unique RawPosts (zero Apify)")

    classifier = DeterministicClassifier()
    attempted: list[dict] = []
    evaluated_pairs: list[tuple] = []
    stop_reason = ""

    for url in unresolved[:MAX_CANDIDATES]:
        raw_post = raw_by_url.get(url)
        if raw_post is None:
            _log(f"[skip] {url}: raw item not in preserved dataset")
            continue
        label = url.rsplit("/", 1)[-1][:50]
        try:
            classified = classifier.classify(raw_post)
        except Exception as e:
            _log(f"[classify-error] {label}: {e}")
            continue
        if not classified.is_valid:
            _log(f"[skip] {label}: deterministic classification no longer passes")
            continue

        _log(f"[candidate] evaluating {label} ...")
        gate = verifier.verify(
            post_text=raw_post.text, post_url=raw_post.post_url,
            author_name=raw_post.author_name,
            author_profile_url=raw_post.author_profile_url,
            job_card_location=raw_post.job_card_location,
            job_card_company=raw_post.job_card_company,
            job_card_employment_type=raw_post.job_card_employment_type,
            job_card_experience=raw_post.job_card_experience,
        )
        rec = {
            "post_url": url,
            "decision": gate.decision, "status": gate.status,
            "llm_status": gate.llm_status, "confidence": gate.confidence,
            "llm_confidence": gate.llm_confidence,
            "reasons": gate.reasons,
            "verdict": gate.verdict.model_dump(),
            "llm_error": gate.llm_error, "llm_provider": gate.llm_provider,
        }
        # cache-first + atomic ledger update, immediately after evaluation
        records[url] = rec
        persist_verdicts_atomic(records)
        _log(f"[persisted] {label}: decision={gate.decision} "
             f"llm_status={gate.llm_status} conf={gate.confidence:.2f} "
             f"prov={gate.llm_provider}")

        attempted.append({"post_url": url, "decision": gate.decision,
                          "llm_status": gate.llm_status,
                          "confidence": gate.confidence,
                          "provider": gate.llm_provider})

        if gate.llm_status in ("UNAVAILABLE", "ERROR", "NOT_RUN"):
            stop_reason = f"provider unavailability on {label} ({gate.llm_status})"
            _log(f"[STOP] {stop_reason} -> tranche ends (no further candidates)")
            break
        if gate.decision == "ACCEPT":
            evaluated_pairs.append((classified, gate))

    # ── Section 5/6: production gate + controlled write ──────────────────────
    rows_written = {"MAIN": 0, "DATA": 0}
    eligible_report: list[dict] = []
    if evaluated_pairs:
        for classified, gate in evaluated_pairs:
            merged = enriched_post(classified, gate)
            enricher = Enricher(contact_provider=None)  # offline, fail-closed
            enricher.enrich_from_classified(
                merged, gate.verdict,
                job_card_company=classified.job_card_company,
                job_card_employment_type=classified.job_card_employment_type,
                job_card_experience=classified.job_card_experience,
            )
            route = route_production_post(merged, gate)
            eligible_report.append({
                "url": merged.source_link, "destination": route.destination,
                "category": route.category,
                "decision": gate.decision, "confidence": gate.confidence,
            })
            _log(f"[gate] {merged.source_link[-40:]} -> dest={route.destination} "
                 f"category={route.category}")
        live_pairs = [(c, g) for (c, g), rep in zip(evaluated_pairs, eligible_report)
                      if rep["destination"] in (MAIN_PRODUCTION, DATA_PRODUCTION)]
        if live_pairs:
            pre_rows = {
                "main": w.worksheet.get_all_values(),
                "data": w._data_ws().get_all_values(),
            }
            live_writer = SheetsWriter(
                GOOGLE_SHEETS_CREDENTIALS, GOOGLE_SHEETS_ID,
                GOOGLE_SHEET_WORKSHEET, dry_run=False,
                data_worksheet_name=GOOGLE_SHEET_DATA_WORKSHEET)
            result = live_writer.write_production(live_pairs)
            _log(f"[write] outcome={result.outcome} written={result.written} "
                 f"dropped={result.dropped} err={result.error[:120]}")
            rows_written["MAIN"] = result.destinations.get(
                GOOGLE_SHEET_WORKSHEET, 0)
            rows_written["DATA"] = result.destinations.get(
                GOOGLE_SHEET_DATA_WORKSHEET, 0)
            # ── Post-write verification (read-only) ─────────────────────────
            post_main = w.worksheet.get_all_values()
            post_data = w._data_ws().get_all_values()
            v_errs = []
            if len(post_main) != main_before + rows_written["MAIN"]:
                v_errs.append(f"MAIN delta {len(post_main) - main_before} "
                              f"!= written {rows_written['MAIN']}")
            if len(post_data) != data_before + rows_written["DATA"]:
                v_errs.append(f"DATA delta {len(post_data) - data_before} "
                              f"!= written {rows_written['DATA']}")
            for name, before_rows, after_rows in (
                    ("MAIN", pre_rows["main"], post_main),
                    ("DATA", pre_rows["data"], post_data)):
                if after_rows[:len(before_rows)] != before_rows:
                    v_errs.append(f"{name}: pre-existing rows changed")
                for r in after_rows[len(before_rows):]:
                    v_errs.extend(row_invariants(r, name, strict=True))
            m_urls = {r[7] for r in post_main[1:] if r[7]}
            d_urls = {r[7] for r in post_data[1:] if r[7]}
            if m_urls & d_urls:
                v_errs.append("cross-tab duplicate after write")
            if v_errs:
                _log("[VERIFY-FAIL] " + "; ".join(v_errs))
                return 1
            _log("[verify] post-write checks ALL PASS (delta, byte-identical "
                 "existing rows, invariants, routing tab split, dedup)")
        else:
            _log("[write] 0 eligible after gate -> no write (no manufactured leads)")
    else:
        _log("[gate] no ACCEPT among evaluated candidates -> 0 rows written")

    unresolved_left = sum(1 for r in records.values()
                          if r.get("llm_status") == "UNAVAILABLE")
    summary = {
        "phase": "29", "at": started,
        "preflight": pre,
        "probe": {"status": p_status, "latency_s": round(p_latency, 1)},
        "chain": chain,
        "candidates_attempted": len(attempted), "attempted": attempted,
        "unresolved_before": len(unresolved), "unresolved_after": unresolved_left,
        "stop_reason": stop_reason or ("max-3 reached" if len(attempted) == MAX_CANDIDATES
                                       else "backlog exhausted"),
        "eligible": eligible_report,
        "rows_written": rows_written,
        "final_main_rows": None, "final_data_rows": None,
    }
    (OUT_DIR / "phase29_tranche_summary.json").write_text(
        json.dumps(summary, indent=1, ensure_ascii=False), encoding="utf-8")
    _log(f"[done] attempted={len(attempted)} written={rows_written} "
         f"unresolved={unresolved_left}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
