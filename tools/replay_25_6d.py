"""
Phase 25.6D — REPLAY of the preserved Phase 25.6 dataset (NO APIFY).

Reuses data/validation/phase25_6_live_run/raw_dataset.json (382 posts, run
PwuSCPs7DwFut2Yos / dataset q77CFLM0vIR6wdxFC) through the FROZEN pipeline:

  RAW -> DEDUP -> SOURCING ORDER -> DETERMINISTIC CLASSIFY -> LLM VERIFY
      -> ENRICHMENT -> PRODUCTION GATE -> FINAL CATEGORY -> TWO-TAB ROUTING

Rules honored by construction:
  - No Apify call anywhere in this file. The dataset is mapped to RawPost
    objects with the exact DataDopingSource._to_raw_post mapping the live
    actor used.
  - The truncated 25.6 live-run logs and the 25.6B partial recovery are NOT
    authoritative: every candidate is re-evaluated fresh (a small resumable
    LLM cache stores only EVALUATED gate results so an interrupted run can
    resume; UNAVAILABLE results are never cached and are always retried).
  - No gate/taxonomy/prompt/threshold changes; no candidates discarded by
    sourcing score (ordering only, mirroring the orchestrator).
  - LLM chain: Groq -> Gemini from current config (LLM_PROVIDER=groq),
    availability failover only, bounded retries, fail-closed REVIEW.

Subcommands:
  verify   run deterministic funnel + LLM verification + gate + routing,
           persist evidence + eligible pairs (NO Sheet write)
  report   print the §10 pre-write tables and §18 query-impact report
  write    §11 final assertions -> authorized controlled production write
           (in-process DRY_RUN=false override; .env stays DRY_RUN=true)
           -> §15 post-write verification
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

try:  # Windows consoles are cp1252; keep emoji-bearing post text printable.
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

# Driver-level pacing (source constants stay frozen): a 429 aborts a provider
# immediately instead of burning ~90s of in-process backoff per attempt. The
# replay then retries on its own settle cadence; fail-closed semantics and
# bounded attempts are unchanged.
import app.llm.groq_provider as _gq  # noqa: E402
import app.llm.gemini_provider as _gm  # noqa: E402
_gq.RATE_LIMIT_MAX_ATTEMPTS = 1
_gm.RATE_LIMIT_MAX_ATTEMPTS = 1

from app.config import (  # noqa: E402
    APIFY_API_TOKEN,  # used ONLY to construct the mapper (never calls Apify)
    DRY_RUN,
    GOOGLE_SHEETS_CREDENTIALS,
    GOOGLE_SHEETS_ID,
    GOOGLE_SHEET_WORKSHEET,
    GOOGLE_SHEET_DATA_WORKSHEET,
    LLM_PROVIDER,
    PRODUCTION_GATE,
    SEARCH_QUERIES,
)
from app.models import RawPost  # noqa: E402
from app.classifier import DeterministicClassifier  # noqa: E402
from app.sheets_writer import SheetsWriter  # noqa: E402
from app.sourcing_score import score_raw_post  # noqa: E402
from app.llm.schemas import GateResult, LeadVerdict, LLM_NON_EVALUATED  # noqa: E402
from app.llm.verifier import Verifier, enriched_post  # noqa: E402
from app.enrichment import Enricher  # noqa: E402
from app.production_gate import (  # noqa: E402
    DATA_PRODUCTION,
    MAIN_PRODUCTION,
    route_production_post,
)
from app.sources import datadoping_source as dds  # noqa: E402

RUN_DIR = ROOT / "data" / "validation" / "phase25_6_live_run"
RAW_JSON = RUN_DIR / "raw_dataset.json"
IDENTITY_JSON = RUN_DIR / "dataset_identity_25_6b.json"
EVIDENCE_JSON = RUN_DIR / "replay_25_6d_evidence.json"
PAIRS_JSON = RUN_DIR / "replay_25_6d_eligible_pairs.json"
CACHE_JSON = RUN_DIR / "llm_cache_25_6d.json"

EXPECTED_RUN_ID = "PwuSCPs7DwFut2Yos"
EXPECTED_DATASET_ID = "q77CFLM0vIR6wdxFC"
HS_QUERIES = [
    '"founder\'s office" "my team"',
    '"chief of staff" "my team"',
    '"founder\'s office" "join our team"',
    '"chief of staff" "join our team"',
    '"founder associate" "looking for"',
    '"office of the founder" "looking for"',
    '"data analyst" "my team"',
    '"data scientist" "my team"',
    '"data analyst" "join our team"',
    '"data scientist" "join our team"',
]


def _log(msg: str) -> None:
    print(msg, flush=True)


def _missing(v) -> bool:
    return (v or "").strip().lower() in {"", "unclear", "none", "not available"}


# ── Dataset ────────────────────────────────────────────────────────────────

def load_dataset() -> tuple[list[RawPost], dict[str, str]]:
    """Map raw_dataset.json to RawPosts with the live actor's mapping.

    Returns (raw_posts_in_file_order, post_url -> source query). Never calls
    Apify; identity is asserted against the persisted identity artifact.
    """
    ident = json.loads(IDENTITY_JSON.read_text(encoding="utf-8"))
    assert ident.get("identity_verified") is True, "dataset identity artifact not verified"
    assert ident.get("local_items") == 382, "identity artifact items != 382"
    assert ident.get("run_id") == EXPECTED_RUN_ID, "run id mismatch"
    assert ident.get("dataset_id") == EXPECTED_DATASET_ID, "dataset id mismatch"

    items = json.loads(RAW_JSON.read_text(encoding="utf-8"))
    assert isinstance(items, list) and len(items) == 382, "raw_dataset.json is not the 382-post dataset"

    # Constructing DataDopingSource performs NO network I/O; we reuse its
    # exact RawPost mapping (_to_raw_post) so the replay sees byte-identical
    # inputs to the live run. No actor/dataset call is ever made.
    mapper = dds.DataDopingSource(APIFY_API_TOKEN)
    posts: list[RawPost] = []
    query_by_url: dict[str, str] = {}
    unmapped = 0
    for item in items:
        if not isinstance(item, dict) or item.get("error"):
            unmapped += 1
            continue
        post = mapper._to_raw_post(item)
        if post is None:
            unmapped += 1
            continue
        posts.append(post)
        q = (item.get("input") or "").strip()
        if q and post.post_url not in query_by_url:
            query_by_url[post.post_url] = q
    _log(f"[dataset] mapped {len(posts)} RawPosts (unmapped/error records: {unmapped})")
    return posts, query_by_url


def dedup_and_order(posts: list[RawPost]) -> tuple[list[RawPost], int]:
    """Level-1 dedup (first occurrence wins) + Phase 25.5 sourcing order."""
    seen: set[str] = set()
    unique: list[RawPost] = []
    for p in posts:
        if p.post_url not in seen:
            seen.add(p.post_url)
            unique.append(p)
    duplicates = len(posts) - len(unique)
    unique.sort(key=lambda p: -score_raw_post(p))  # ordering only, no discards
    return unique, duplicates


# ── LLM cache (evaluated results only; UNAVAILABLE always retried) ─────────

def _cache_key(p: RawPost) -> str:
    aid = dds.activity_id(p.post_url) or p.post_url
    return aid


def _load_cache() -> dict:
    if CACHE_JSON.exists():
        return json.loads(CACHE_JSON.read_text(encoding="utf-8"))
    return {}


def verify_all(unique: list[RawPost]) -> tuple[list[tuple[RawPost, GateResult]], dict]:
    """Fresh LLM verification with Groq->Gemini, resumable via cache."""
    classifier = DeterministicClassifier()
    verifier = Verifier()  # chain from config: LLM_PROVIDER=groq -> [Groq, Gemini]
    chain_names = [type(p).__name__ for p in verifier.providers]
    _log(f"[llm] provider chain: {' -> '.join(chain_names)} (LLM_PROVIDER={LLM_PROVIDER})")
    assert chain_names == ["GroqProvider", "GeminiProvider"], "unexpected provider chain"

    cache = _load_cache()
    counts = {
        "candidates": 0, "llm_verifies": 0,
        "groq_attempted": 0, "groq_success": 0, "groq_rate_limited": 0, "groq_other_errors": 0,
        "gemini_attempted": 0, "gemini_success": 0, "gemini_rate_limited": 0, "gemini_other_errors": 0,
        "ACCEPT": 0, "REVIEW": 0, "REJECT": 0, "UNAVAILABLE": 0,
        "cache_hits": 0, "fresh_calls": 0,
    }
    pairs: list[tuple[RawPost, GateResult]] = []
    rejected_samples: list[dict] = []

    for idx, p in enumerate(unique, 1):
        try:
            classified = classifier.classify(p)
        except Exception as e:
            _log(f"[classify-error] {p.post_url}: {e}")
            continue
        if not classified.is_valid:
            continue
        counts["candidates"] += 1
        key = _cache_key(p)

        gate: GateResult | None = None
        if key in cache and cache[key].get("llm_status") not in LLM_NON_EVALUATED:
            d = cache[key]
            gate = GateResult(
                verdict=LeadVerdict(**d["verdict"]),
                decision=d["decision"], status=d["status"], reasons=d["reasons"],
                llm_called=True, llm_status=d["llm_status"], llm_error=d.get("llm_error", ""),
                llm_provider=d.get("llm_provider", ""),
            )
            counts["cache_hits"] += 1
        else:
            counts["fresh_calls"] += 1
            t0 = time.time()
            _UNAVAILABLE_SETTLE_S = 80  # driver-level pacing only (no pipeline change)
            gate = verifier.verify(
                post_text=classified.text,
                post_url=classified.post_url,
                author_name=classified.author_name,
                author_profile_url=classified.author_profile_url,
                job_card_location=classified.job_card_location,
                job_card_company=classified.job_card_company,
                job_card_employment_type=classified.job_card_employment_type,
                job_card_experience=classified.job_card_experience,
            )
            counts["llm_verifies"] += 1
            # Per-provider accounting for fresh calls (chain = Groq -> Gemini).
            counts["groq_attempted"] += 1  # every verify() reaches Groq first
            if gate.llm_status not in LLM_NON_EVALUATED:
                prov = (gate.llm_provider or "").lower()
                if prov.startswith("groq"):
                    counts["groq_success"] += 1
                elif prov.startswith("gemini"):
                    counts["gemini_success"] += 1
                err = (gate.llm_error or "")
                if prov.startswith("gemini") and ("429" in err or "rate limit" in err.lower()):
                    counts["gemini_rate_limited"] += 1
            else:
                err = (gate.llm_error or "")
                if "Groq" in err:
                    if "429" in err or "rate limit" in err.lower():
                        counts["groq_rate_limited"] += 1
                    else:
                        counts["groq_other_errors"] += 1
                if "Gemini" in err:
                    counts["gemini_attempted"] += 1
                    if "429" in err or "rate limit" in err.lower():
                        counts["gemini_rate_limited"] += 1
                    else:
                        counts["gemini_other_errors"] += 1
            _log(f"[llm {idx}/{len(unique)}] {gate.decision}/{gate.llm_status} "
                 f"conf={gate.confidence:.2f} prov={gate.llm_provider} {time.time()-t0:.1f}s")
            if gate.llm_status in LLM_NON_EVALUATED:
                # Both providers just spent their retry budgets on this post;
                # let the rate-limit window settle instead of burning the
                # next candidate's budget inside their backoffs.
                time.sleep(_UNAVAILABLE_SETTLE_S)
            # Cache ONLY evaluated results; UNAVAILABLE is retried on resume.
            if gate.llm_status not in LLM_NON_EVALUATED:
                cache[key] = {
                    "decision": gate.decision, "status": gate.status,
                    "reasons": gate.reasons, "llm_status": gate.llm_status,
                    "llm_error": gate.llm_error, "llm_provider": gate.llm_provider,
                    "verdict": gate.verdict.model_dump(),
                }
                CACHE_JSON.write_text(json.dumps(cache, ensure_ascii=False, indent=1),
                                      encoding="utf-8")

        counts[gate.llm_status if gate.llm_status in LLM_NON_EVALUATED else gate.decision] += 1
        if gate.decision == "REJECT":
            rejected_samples.append({
                "source_link": p.post_url, "reasons": gate.reasons,
                "snippet": classified.text[:120],
            })
            continue
        pairs.append((p, classified, gate))

    _log("[llm] verification complete: " + json.dumps(
        {k: v for k, v in counts.items()}, ensure_ascii=False))
    return pairs, {"counts": counts, "rejected_samples": rejected_samples,
                   "chain": chain_names}


# ── verify subcommand ──────────────────────────────────────────────────────

def cmd_verify() -> None:
    posts, query_by_url = load_dataset()
    unique, duplicates = dedup_and_order(posts)
    _log(f"[funnel] RAW={len(posts)} UNIQUE={len(unique)} DUPLICATES={duplicates}")

    llm_pairs, llm_meta = verify_all(unique)
    classifier = DeterministicClassifier()
    enricher = Enricher(contact_provider=None)  # offline, fail-closed (CONTACT_PROVIDER=null)

    excluded = len(unique) - llm_meta["counts"]["candidates"]
    enriched_count = 0
    gate_rows = {"MAIN": [], "DATA": [], "HOLD": [], "REJECT": []}
    eligible_pairs: list[dict] = []

    for p, classified, gate in llm_pairs:
        merged = enriched_post(classified, gate)
        if gate.decision == "ACCEPT":
            try:
                enricher.enrich_from_classified(
                    merged, gate.verdict,
                    job_card_company=classified.job_card_company,
                    job_card_employment_type=classified.job_card_employment_type,
                    job_card_experience=classified.job_card_experience,
                )
                enriched_count += 1
            except Exception as e:
                _log(f"[enrich-error] {merged.source_link}: {e}")
        route = route_production_post(merged, gate)
        if route.destination == MAIN_PRODUCTION:
            bucket = "MAIN"
        elif route.destination == DATA_PRODUCTION:
            bucket = "DATA"
        elif route.destination == "REJECT":
            bucket = "REJECT"
        else:
            bucket = "HOLD"
        gate_rows[bucket].append({
            "source_link": merged.source_link,
            "company": merged.company_name,
            "exact_role": merged.exact_role,
            "category": route.category or merged.major_category,
            "hm": merged.hiring_manager_name,
            "hm_evidence": merged.hiring_manager_evidence,
            "location": merged.location,
            "employment": merged.employment_type,
            "experience": merged.experience_requirement,
            "confidence": round(gate.confidence, 2),
            "llm_status": gate.llm_status,
            "decision": gate.decision,
            "reasons": route.reasons or gate.reasons,
            "query": query_by_url.get(merged.source_link, ""),
        })
        if bucket in ("MAIN", "DATA"):
            eligible_pairs.append({
                "bucket": bucket,
                "post": {
                    "post_url": classified.post_url, "post_date": classified.post_date,
                    "text": classified.text, "author_name": classified.author_name,
                    "author_profile_url": classified.author_profile_url,
                    "company": classified.company,
                    "job_card_location": classified.job_card_location,
                    "job_card_company": classified.job_card_company,
                    "job_card_employment_type": classified.job_card_employment_type,
                    "job_card_experience": classified.job_card_experience,
                },
                "gate": {
                    "decision": gate.decision, "status": gate.status,
                    "reasons": gate.reasons, "llm_status": gate.llm_status,
                    "llm_error": gate.llm_error, "llm_provider": gate.llm_provider,
                    "verdict": gate.verdict.model_dump(),
                },
                "query": query_by_url.get(merged.source_link, ""),
            })

    evidence = {
        "phase": "25.6D",
        "apify_run_id": EXPECTED_RUN_ID,
        "apify_dataset_id": EXPECTED_DATASET_ID,
        "raw": len(posts), "unique": len(unique), "duplicates": duplicates,
        "deterministic_excluded": excluded,
        "deterministic_candidates": llm_meta["counts"]["candidates"],
        "llm": llm_meta["counts"],
        "chain": llm_meta["chain"],
        "enriched_accepts": enriched_count,
        "routing": {k: len(v) for k, v in gate_rows.items()},
        "rows": gate_rows,
        "rejected_samples": llm_meta["rejected_samples"],
    }
    EVIDENCE_JSON.write_text(json.dumps(evidence, ensure_ascii=False, indent=1), encoding="utf-8")
    PAIRS_JSON.write_text(json.dumps(eligible_pairs, ensure_ascii=False, indent=1), encoding="utf-8")
    _log(f"[evidence] {evidence['routing']} -> {EVIDENCE_JSON.name}")
    _log("[verify] DONE — no Sheet writes performed")


# ── §18 query impact + §10 tables ──────────────────────────────────────────

def cmd_report() -> None:
    ev = json.loads(EVIDENCE_JSON.read_text(encoding="utf-8"))
    rows = ev["rows"]
    all_rows = rows["MAIN"] + rows["DATA"] + rows["HOLD"] + rows["REJECT"]

    hs = set(HS_QUERIES)
    orig = {"cand": 0, "company": 0, "hm": 0, "accept": 0, "eligible": 0}
    new = {"cand": 0, "company": 0, "hm": 0, "accept": 0, "eligible": 0}

    def bucket_for(q: str) -> dict | None:
        if not q:
            return None
        return new if q in hs else orig

    for r in all_rows:
        b = bucket_for(r.get("query", ""))
        if b is None:
            continue
        b["cand"] += 1
        if not _missing(r.get("company")):
            b["company"] += 1
        if not _missing(r.get("hm")):
            b["hm"] += 1
        if r.get("decision") == "ACCEPT":
            b["accept"] += 1
        if r.get("llm_status") == "SUCCESS" and r.get("decision") == "ACCEPT":
            b["eligible"] += 1

    _log("\n=== §18 QUERY IMPACT (deterministic-candidate cohort only) ===")
    for name, b in (("Original 38", orig), ("New 10 HS", new)):
        cand = b["cand"] or 1
        _log(f"{name}: candidates={b['cand']} company-known={b['company']} "
             f"({b['company']/cand:.0%}) HM-known={b['hm']} ({b['hm']/cand:.0%}) "
             f"ACCEPT={b['accept']} production-eligible={b['eligible']} "
             f"({b['eligible']/cand:.0%})")
    unattr = sum(1 for r in all_rows if not r.get("query"))
    if unattr:
        _log(f"(unattributed rows with no query field: {unattr} — excluded from both cohorts)")

    _log(f"\nLLM accounting: {json.dumps(ev['llm'], ensure_ascii=False)}")

    for section in ("MAIN", "DATA"):
        _log(f"\n=== {section} ELIGIBLE ({len(rows[section])}) ===")
        for i, r in enumerate(rows[section], 1):
            _log(f"{i:>2}. {r['company']} | {r['exact_role']} | {r['category']} -> {section} | "
                 f"HM={r['hm'] or '-'} | evidence={(r['hm_evidence'] or '-')[:60]} | "
                 f"{r['location']} | {r['employment']} | exp={r['experience']} | "
                 f"conf={r['confidence']} | gate=ELIGIBLE")
    _log(f"\n=== HELD ({len(rows['HOLD'])}) ===")
    for i, r in enumerate(rows["HOLD"], 1):
        _log(f"{i:>2}. {r['company']} | {r['exact_role']} | conf={r['confidence']} | "
             f"reasons={'; '.join(r['reasons'])[:160]}")
    _log(f"\n=== REJECTED ({len(rows['REJECT'])}) ===")
    for i, r in enumerate(rows["REJECT"], 1):
        _log(f"{i:>2}. {r['company']} | {r['exact_role']} | reasons={'; '.join(r['reasons'])[:160]}")


# ── §11-§15: assertions + controlled write + post-write verification ───────

def cmd_write() -> None:
    assert DRY_RUN is True, ".env must keep DRY_RUN=true; the live override is in-process only"

    pairs_raw = json.loads(PAIRS_JSON.read_text(encoding="utf-8"))
    _log(f"[write] eligible pairs loaded: {len(pairs_raw)}")

    # Rebuild (ClassifiedPost, GateResult) pairs exactly as the orchestrator
    # would hold them (deterministic classify re-run is byte-stable).
    classifier = DeterministicClassifier()
    from app.models import RawPost as _RP
    from app.llm.verifier import enriched_post

    records: list[tuple] = []
    for pr in pairs_raw:
        post = _RP(**pr["post"])
        classified = classifier.classify(post)
        assert classified.is_valid, "eligible pair no longer passes deterministic classification"
        assert classified.post_url == pr["post"]["post_url"]
        g = pr["gate"]
        gate = GateResult(
            verdict=LeadVerdict(**g["verdict"]), decision=g["decision"], status=g["status"],
            reasons=g["reasons"], llm_called=True, llm_status=g["llm_status"],
            llm_error=g.get("llm_error", ""), llm_provider=g.get("llm_provider", ""),
        )
        merged = enriched_post(classified, gate)
        route = route_production_post(merged, gate)
        assert route.destination in (MAIN_PRODUCTION, DATA_PRODUCTION), \
            f"pair no longer routes to production: {route.destination}"
        records.append((merged, gate))

    writer = SheetsWriter(GOOGLE_SHEETS_CREDENTIALS, GOOGLE_SHEETS_ID,
                          GOOGLE_SHEET_WORKSHEET, DRY_RUN,
                          data_worksheet_name=GOOGLE_SHEET_DATA_WORKSHEET)

    # Pre-write snapshot for §15 "existing rows unchanged".
    pre_main = writer.worksheet.get_all_values()
    pre_data = (writer._data_ws().get_all_values()
                if writer._data_ws() is not None else [])

    # §12: combined production dedup set (fail-closed read).
    existing = writer.get_existing_urls()

    # §11 final assertions.
    batch_urls: set[str] = set()
    for merged, gate in records:
        assert route_production_post(merged, gate).destination in (MAIN_PRODUCTION, DATA_PRODUCTION)
        assert gate.decision == "ACCEPT" and gate.llm_status == "SUCCESS"
        assert gate.confidence >= 0.60
        assert (merged.company_name or "").strip() and merged.company_name.strip().lower() != "unclear"
        assert (merged.exact_role or "").strip()
        assert (merged.hiring_manager_name or "").strip() and \
            merged.hiring_manager_name.strip().lower() not in {"unclear", "not available"}
        assert (merged.source_link or "").startswith("https://")
        assert merged.source_link not in batch_urls, "duplicate within batch"
        batch_urls.add(merged.source_link)
        assert merged.source_link not in existing, f"sheet duplicate: {merged.source_link}"
    # Cross-tab exclusivity within the batch is enforced structurally by the
    # writer; assert the batch against the live tabs too.
    main_urls_now = {r[7] for r in pre_main[1:] if len(r) > 7 and r[7]}
    data_urls_now = {r[7] for r in pre_data[1:] if len(r) > 7 and r[7]}
    assert not (batch_urls & main_urls_now) and not (batch_urls & data_urls_now)
    assert not (main_urls_now & data_urls_now), "historical cross-tab overlap detected"

    if not records:
        _log("[write] zero eligible rows — EMPTY outcome, no Sheet mutation")
        return

    # §13: authorized in-process dry-run override (".env stays DRY_RUN=true").
    writer.dry_run = False
    result = writer.write_production(records)
    _log(f"[write] outcome={result.outcome} written={result.written} "
         f"dropped={result.dropped} routing_dropped={result.routing_dropped} "
         f"destinations={result.destinations} error={result.error!r}")

    if result.outcome == "EMPTY":
        _log("[write] EMPTY: confirm zero eligible rows reached the tabs; no mutation")
        return
    if result.outcome in ("FAILED", "UNKNOWN"):
        _log(f"[write] {result.outcome} — STOP; no automatic retry")
        return
    assert result.outcome == "SUCCESS", f"unexpected outcome {result.outcome}"

    # §15 post-write verification (fresh reads).
    post_main = writer.worksheet.get_all_values()
    post_data = writer._data_ws().get_all_values()
    i_cat, i_role, i_url, i_conf, i_comp = 1, 2, 7, 9, 0
    new_main = [r for r in post_main[len(pre_main):]]
    new_data = [r for r in post_data[len(pre_data):]]
    _log(f"[verify] MAIN {len(pre_main)-1} -> {len(post_main)-1} (+{len(new_main)}); "
         f"DATA {len(pre_data)-1} -> {len(post_data)-1} (+{len(new_data)})")
    for r in new_main:
        assert r[i_cat] in ("Founder's Office", "Chief of Staff"), f"MAIN category violation: {r[i_cat]}"
        assert r[i_cat] not in ("Data Analyst", "Data Scientist")
    for r in new_data:
        assert r[i_cat] in ("Data Analyst", "Data Scientist"), f"DATA category violation: {r[i_cat]}"
    m_urls = {r[i_url] for r in post_main[1:] if len(r) > i_url and r[i_url]}
    d_urls = {r[i_url] for r in post_data[1:] if len(r) > i_url and r[i_url]}
    assert not (m_urls & d_urls), "cross-tab duplicate after write"
    # Historical rows unchanged (rows are append-only; prefix must match).
    assert post_main[:len(pre_main)] == pre_main, "historical MAIN rows changed!"
    assert post_data[:len(pre_data)] == pre_data, "historical DATA rows changed!"
    _log("[verify] routing violations=0 cross-tab duplicates=0 historical rows unchanged")

    def _show(title: str, rows: list[list[str]]) -> None:
        _log("\n" + "=" * 40)
        _log(title)
        _log("=" * 40)
        for r in rows:
            g = lambda idx: (r[idx] if len(r) > idx else "")
            _log(f"Company: {g(0)}\n  Exact Role: {g(2)}\n  HM: {g(5)}\n  Location: {g(10)}\n"
                 f"  Employment: {g(11)}\n  Experience: {g(12)}\n  CTC: {g(3)}\n"
                 f"  Confidence: {g(9)}\n  Apply Link: {g(21)}\n  Cold Email: {g(4)}\n"
                 f"  Source: {g(7)}\n  Classification Reason: {g(17)[:200]}")

    _show("NEW LINKEDIN HIRING LEADS", new_main)
    _show("NEW DATA ANALYST & DATA SCIENTIST", new_data)


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "verify"
    if cmd == "verify":
        cmd_verify()
    elif cmd == "report":
        cmd_report()
    elif cmd == "write":
        cmd_write()
    else:
        _log(f"unknown subcommand {cmd!r}; use verify | report | write")
        sys.exit(2)
