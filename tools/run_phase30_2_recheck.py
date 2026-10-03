"""Phase 30.2 — TARGETED RE-EVALUATION of the 3 REVIEW records whose company
evidence changed due to the Phase 30.1 resolver fix:

  1. Flipkart      (rajesh-fulvari)
  2. Battery Smart (zenrixi-hr-solutions)
  3. Supertails    (data-analyst-jobs-india)

Normal re-evaluation ONLY: same mapper, same Verifier, same prompt/schema,
same gate, same guarded writer; ledger persisted through the existing
recovery mechanism (cache-first atomic replace). No hypothetical ACCEPT, no
manual verdict/confidence alteration, no inference.

Mandatory preflights: read-only Sheets (unified Phase 28.4 TLS hook) and one
real structured Groq probe. On any provider unavailability: persist, stop.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from app.classifier import DeterministicClassifier              # noqa: E402
from app.config import (                                        # noqa: E402
    GOOGLE_SHEET_DATA_WORKSHEET, GOOGLE_SHEET_WORKSHEET,
    GOOGLE_SHEETS_CREDENTIALS, GOOGLE_SHEETS_ID,
)
from app.enrichment import Enricher                             # noqa: E402
from app.llm.verifier import Verifier, enriched_post            # noqa: E402
from app.production_gate import (                               # noqa: E402
    DATA_PRODUCTION, MAIN_PRODUCTION, route_production_post,
)
from app.sheets_writer import SheetsWriter                      # noqa: E402
from app.sources import datadoping_source as dds                # noqa: E402

OUT_DIR = ROOT / "data" / "validation" / "phase30_2_targeted_recheck"
RUN_DIR = ROOT / "data" / "validation" / "phase26_fresh_run"
RAW_JSON = RUN_DIR / "raw_dataset.json"
VERDICTS = RUN_DIR / "verdicts.jsonl"

TARGETS = (
    "rajesh-fulvari-081a61115_hiring-flipkart-dataanalytics",
    "zenrixi-hr-solutions-82a1b03bb_zenrixi-hiring-vacancy",
    "data-analyst-jobs-india_dataanalyst-sql-python",
)

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
    tmp = VERDICTS.with_suffix(".jsonl.tmp302")
    with tmp.open("w", encoding="utf-8", newline="\n") as f:
        for rec in records.values():
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    import os
    os.replace(tmp, VERDICTS)


def row_invariants(row: list, tab: str, strict: bool = False) -> list[str]:
    """Baseline invariant for existing rows; +25.7 barrier for NEW rows."""
    from app.sheets_writer import EXPECTED_HEADERS
    errs = []
    if len(row) != len(EXPECTED_HEADERS):
        return [f"{tab}: row has {len(row)} cols, expected {len(EXPECTED_HEADERS)}"]
    company = (row[0] or "").strip().lower()
    role = (row[2] or "").strip().lower()
    hm = (row[5] or "").strip().lower()
    src = (row[7] or "").strip()
    try:
        conf = float((row[9] or "").strip())
    except ValueError:
        conf = -1.0
    if (row[18] or "").strip().lower() != "new":
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
        errs.append(f"{tab}: confidence < 0.60")
    return errs


def sheets_preflight(w: SheetsWriter, sheet) -> dict:
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
    from app.sheets_writer import EXPECTED_HEADERS
    for name, rows in (("MAIN", main_rows), ("DATA", data_rows)):
        if not rows or rows[0] != EXPECTED_HEADERS:
            raise RuntimeError(f"{name}: header != 23-column EXPECTED_HEADERS")
    errs: list[str] = []
    for r in main_rows[1:]:
        errs.extend(row_invariants(r, "MAIN"))
    for r in data_rows[1:]:
        errs.extend(row_invariants(r, "DATA"))
    if errs:
        raise RuntimeError("production invariants failed: " + "; ".join(errs[:6]))
    main_urls = {r[7] for r in main_rows[1:] if r[7]}
    data_urls = {r[7] for r in data_rows[1:] if r[7]}
    if main_urls & data_urls:
        raise RuntimeError("cross-tab duplicates present")
    summary["cross_tab_duplicates"] = 0
    review_ws = sheet.worksheet(REVIEW_TAB)
    summary["review_rows_incl_header"] = len(review_ws.get_all_values())
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
    _log("=== PHASE 30.2 — targeted recheck (3 recovered-evidence records) ===")
    _log(f"[clock] UTC NOW: {started}")
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    # ── 1. Sheets preflight (read-only; unified TLS hook) ───────────────────
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
         f"headers 23-col OK; invariants OK; dupes=0; "
         f"GIRI/NEC/FourKites/Indecimal OK")

    # ── 2. Provider preflight ───────────────────────────────────────────────
    _log("[probe] Groq structured probe ...")
    p_status, p_latency, p_detail = probe_groq()
    _log(f"[probe] Groq: {p_status} ({p_latency:.1f}s) {p_detail}")
    if p_status != "SERVING":
        _log("[STOP] Groq probe not SERVING -> no candidates")
        return 1

    verifier = Verifier()
    chain = [type(p).__name__ for p in verifier.providers]
    _log(f"[chain] provider chain: {' -> '.join(chain)}")
    if chain != ["GroqProvider", "GeminiProvider"]:
        _log(f"[STOP] unexpected provider chain {chain}")
        return 1

    # ── 3. Normal re-evaluation of exactly the 3 targets ────────────────────
    records = load_verdicts()
    raw = json.loads(RAW_JSON.read_text(encoding="utf-8"))
    mapper = dds.DataDopingSource("phase30-2-targeted-recheck")
    raw_by_url: dict[str, object] = {}
    for it in raw["items"]:
        if isinstance(it, dict) and not it.get("error"):
            p = mapper._to_raw_post(it)
            if p is not None:
                raw_by_url.setdefault(p.post_url, p)

    classifier = DeterministicClassifier()
    enricher = Enricher(contact_provider=None)
    results: list[dict] = []
    evaluated_pairs: list[tuple] = []
    stop_reason = ""

    for target in TARGETS:
        url = next((u for u in records if target in u), None)
        if url is None:
            _log(f"[STOP] target not found in ledger: {target}")
            return 1
        old = records[url]
        label = url.rsplit("/", 1)[-1][:50]
        raw_post = raw_by_url.get(url)
        if raw_post is None:
            _log(f"[STOP] raw item missing for {label}")
            return 1
        classified = classifier.classify(raw_post)
        if not classified.is_valid:
            _log(f"[STOP] {label}: deterministic classification no longer passes")
            return 1

        _log(f"[candidate] re-evaluating {label} ...")
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
        records[url] = rec
        persist_verdicts_atomic(records)
        _log(f"[persisted] {label}: decision={gate.decision} "
             f"llm_status={gate.llm_status} conf={gate.confidence:.2f} "
             f"prov={gate.llm_provider} (was {old['decision']} "
             f"{old['confidence']:.2f})")

        results.append({
            "candidate": target.split("_")[0][:40],
            "company_after": gate.verdict.company,
            "decision_before": old["decision"], "confidence_before": old["confidence"],
            "decision_after": gate.decision, "confidence_after": gate.confidence,
            "llm_status": gate.llm_status, "provider": gate.llm_provider,
        })

        if gate.llm_status in ("UNAVAILABLE", "ERROR", "NOT_RUN"):
            stop_reason = f"provider unavailability on {label} ({gate.llm_status})"
            _log(f"[STOP] {stop_reason}")
            break
        if gate.decision == "ACCEPT":
            merged = enriched_post(classified, gate)
            enricher.enrich_from_classified(
                merged, gate.verdict,
                job_card_company=classified.job_card_company,
                job_card_employment_type=classified.job_card_employment_type,
                job_card_experience=classified.job_card_experience,
            )
            evaluated_pairs.append((classified, gate, merged))

    # ── 4/6. Production gate + controlled write ─────────────────────────────
    rows_written = {"MAIN": 0, "DATA": 0}
    gate_report: list[dict] = []
    if evaluated_pairs:
        for classified, gate, merged in evaluated_pairs:
            route = route_production_post(merged, gate)
            gate_report.append({
                "url": merged.source_link, "destination": route.destination,
                "category": route.category, "reasons": route.reasons[:3],
            })
            _log(f"[gate] {merged.source_link[-40:]} -> dest={route.destination} "
                 f"category={route.category}")
        live_pairs = [(c, g) for (c, g, _m), rep in zip(evaluated_pairs, gate_report)
                      if rep["destination"] in (MAIN_PRODUCTION, DATA_PRODUCTION)]
        if live_pairs:
            pre_rows = {"main": w.worksheet.get_all_values(),
                        "data": w._data_ws().get_all_values()}
            main_before = pre_rows["main"]
            data_before = pre_rows["data"]
            live_writer = SheetsWriter(
                GOOGLE_SHEETS_CREDENTIALS, GOOGLE_SHEETS_ID,
                GOOGLE_SHEET_WORKSHEET, dry_run=False,
                data_worksheet_name=GOOGLE_SHEET_DATA_WORKSHEET)
            result = live_writer.write_production(live_pairs)
            _log(f"[write] outcome={result.outcome} written={result.written} "
                 f"dropped={result.dropped} err={result.error[:120]}")
            rows_written["MAIN"] = result.destinations.get(GOOGLE_SHEET_WORKSHEET, 0)
            rows_written["DATA"] = result.destinations.get(GOOGLE_SHEET_DATA_WORKSHEET, 0)
            post_main = w.worksheet.get_all_values()
            post_data = w._data_ws().get_all_values()
            v_errs = []
            if len(post_main) != len(main_before) + rows_written["MAIN"]:
                v_errs.append(f"MAIN delta {len(post_main) - len(main_before)} "
                              f"!= {rows_written['MAIN']}")
            if len(post_data) != len(data_before) + rows_written["DATA"]:
                v_errs.append(f"DATA delta {len(post_data) - len(data_before)} "
                              f"!= {rows_written['DATA']}")
            for name, before_rows, after_rows in (("MAIN", main_before, post_main),
                                                  ("DATA", data_before, post_data)):
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
                 "existing rows, invariants, routing, dedup)")
        else:
            _log("[write] 0 eligible after gate -> no write (no manufactured leads)")
    else:
        _log("[gate] no ACCEPT among re-evaluated records -> 0 rows written")

    summary = {
        "phase": "30.2", "at": started,
        "probe": {"status": p_status, "latency_s": round(p_latency, 1)},
        "chain": chain,
        "results": results, "gate_report": gate_report,
        "rows_written": rows_written,
        "stop_reason": stop_reason or "all 3 targets evaluated",
    }
    (OUT_DIR / "phase30_2_summary.json").write_text(
        json.dumps(summary, indent=1, ensure_ascii=False), encoding="utf-8")
    _log(f"[done] targets=3 written={rows_written}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
