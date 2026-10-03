"""
recovery.py — Phase 18B controlled Groq recovery path (OFFLINE, no Sheets).

For posts whose LLM evaluation never succeeded (UNAVAILABLE / NOT_RUN / a
0.00 that is really "never evaluated"), re-run the CURRENT pipeline path on
the STORED text with the CURRENT Groq provider/model and emit an old-vs-new
comparison. Historical results are never overwritten — every recovered post
records old and new side by side plus per-field change reasons.

Hard rules (same as production, enforced here):
  - No scraping. Input text comes only from saved artifacts.
  - Deterministic classification is re-run for the record but never altered:
    deterministically-invalid posts get NO Groq call (cost discipline) and a
    REJECT disposition with the unchanged gate reason.
  - Enrichment runs only for ACCEPT (mirrors the orchestrator).
  - Nothing is written to Google Sheets (this module never imports it).
  - Gemini is never in the recovery path: callers pass an explicit
    GroqProvider-built Verifier; anything else raises.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from app.llm.schemas import LLM_NON_EVALUATED
from app.llm.verifier import Verifier

# Reason-text markers proving the LLM never evaluated the post. Deliberately
# narrow: prose like "deterministic fallback" must NOT match (it is not an
# LLM transport failure).
FAILURE_MARKERS = (
    "llm unavailable",
    "provider error",
    "http 429",
    "rate limit",
    "rate-limit",
    "invalid llm json structure",
    "llm verification unavailable",
    "transport failure",
)

# 15 comparison fields required by the phase brief (18B).
COMPARE_FIELDS = (
    "company", "role", "location", "employment_type", "experience",
    "hiring_manager", "apply_link", "google_form", "email",
)

INPUT_EXACT = "EXACT"
INPUT_DERIVED = "DESCRIPTION_DERIVED"

# ── Phase 20F: bounded recovery-attempt bookkeeping ──────────────────────────
# The free-tier 429 throttle cannot be fixed in code; what code CAN do is keep
# the retry queue disciplined: count attempts, timestamp them, never conflate
# throttling with rejection, and stop auto-eligibility after a small budget so
# a record like R2 (429 across three sessions) waits for an explicit rerun
# instead of joining every future batch automatically.
MAX_RECOVERY_ATTEMPTS = 3

# Transport-throttle markers only (a strict subset of FAILURE_MARKERS): a
# model REJECT carries none of these, so 429 is never read as a decision.
RATE_LIMIT_MARKERS = ("http 429", "rate limit", "rate-limit")


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def is_rate_limited_error(text: str) -> bool:
    """True when an error string is a transport throttle, never a verdict."""
    blob = " ".join(str(text or "").split()).lower()
    return any(m in blob for m in RATE_LIMIT_MARKERS)


def should_retry_recovery(prior_result: Dict[str, Any]) -> bool:
    """Queue semantics: is another recovery attempt for this record useful?

    Pure function over a prior recover_record output. True ONLY for
    fail-closed HOLDs whose LLM never evaluated, that still have input text,
    and that are inside the attempt budget. Evaluated outcomes (ACCEPT /
    REVIEW / REJECT), deterministic rejects, text-less records, and exhausted
    records all return False — so a runner skips them instead of burning
    Groq calls or duplicating work.
    """
    if not isinstance(prior_result, dict):
        return False
    if prior_result.get("disposition") != "HOLD":
        return False
    rec = prior_result.get("recovery", {}) or {}
    if rec.get("llm_status") not in LLM_NON_EVALUATED:
        return False
    if "no stored text" in str(prior_result.get("disposition_reason", "")):
        return False
    try:
        attempts = int(prior_result.get("attempt_count", 0))
    except (TypeError, ValueError):
        attempts = 0
    return attempts < MAX_RECOVERY_ATTEMPTS


def recovery_availability(result: Dict[str, Any]) -> str:
    """One-token availability state for queue/display use (no behavior).

    EVALUATED_* — the model decided (never a throttle). HOLD_RATE_LIMITED —
    transport throttle, safe to rerun later inside budget. HOLD_NO_INPUT —
    nothing to recover from. HOLD_OTHER — any remaining hold.
    """
    if not isinstance(result, dict):
        return "HOLD_OTHER"
    if result.get("disposition") != "HOLD":
        return "EVALUATED_" + str(result.get("disposition", "UNKNOWN"))
    reason = str(result.get("disposition_reason", ""))
    if "no stored text" in reason:
        return "HOLD_NO_INPUT"
    rec = result.get("recovery", {}) or {}
    if is_rate_limited_error(
            str(rec.get("llm_error", "")) + " " + reason):
        return "HOLD_RATE_LIMITED"
    return "HOLD_OTHER"


def _failure_hit(*texts: str) -> str:
    blob = " ".join(t or "" for t in texts).lower()
    for marker in FAILURE_MARKERS:
        if marker in blob:
            return marker
    return ""


def _provider_hint(*texts: str) -> str:
    blob = " ".join(t or "" for t in texts).lower()
    if "gemini" in blob:
        return "gemini"
    if "groq" in blob or "gsk_" in blob:
        return "groq"
    if "explabs" in blob:
        return "explabs"
    return "unknown"


def classify_old_record(rec: Dict[str, Any]) -> Dict[str, Any]:
    """Map a saved record to its old LLM execution status (18A vocabulary).

    Accepts e2e-style, sheet-row-style, pipeline-verifier-style, and
    HEAD-cache-style records. NEVER assumes confidence==0 alone is a failure:
    deterministic rejects (no llm_called field, gate reason present) map to
    evaluated/not-applicable, not UNAVAILABLE.
    """
    reasons: List[str] = []
    for key in ("reasons", "final_reasons"):
        val = rec.get(key)
        if isinstance(val, list):
            reasons.extend(str(x) for x in val)
    text_bits = [
        rec.get("reason", ""), rec.get("llm_reason", ""),
        rec.get("rejection_review_reason", ""),
        rec.get("Classification Reason", ""), " ".join(reasons),
    ]
    hit = _failure_hit(*text_bits)
    provider = _provider_hint(*text_bits)
    raw_conf = rec.get("confidence", rec.get("llm_confidence",
                       rec.get("Confidence Score", rec.get("confidence_score"))))
    try:
        old_conf = float(raw_conf) if raw_conf not in (None, "") else None
    except (TypeError, ValueError):
        old_conf = None
    old_class = str(rec.get("final_decision", rec.get("decision",
                        rec.get("verification_status", rec.get("Status", "")))) or "")

    if hit:
        return {"llm_status": "UNAVAILABLE", "provider": provider,
                "confidence": old_conf, "classification": old_class or "REVIEW",
                "failure_text": hit,
                "note": "stored failure marker; 0.00 here means never-evaluated"}
    called = rec.get("llm_called", rec.get("llm_api_failures", "n/a"))
    if called is False:
        return {"llm_status": "NOT_RUN", "provider": "none",
                "confidence": old_conf, "classification": old_class or "REVIEW",
                "failure_text": "",
                "note": "LLM never called for this record"}
    if old_class in ("ACCEPT", "REJECT", "REVIEW", "New", "Review"):
        norm = {"New": "ACCEPT", "Review": "REVIEW"}.get(old_class, old_class)
        # A stored REJECT with deterministic-origin markers and no evaluated
        # LLM call is NOT a model outcome: the CSV/e2e deterministic rejects
        # carry confidence 0.0 as the invalid-post default (18J: never assume
        # 0 == LLM failure without checking).
        deterministic_origin = (
            "rejection_review_reason" in rec
            or rec.get("final_decision") == "DETERMINISTIC_REJECT"
            or any(k.startswith("deterministic_") for k in rec))
        if (norm == "REJECT" and deterministic_origin
                and rec.get("llm_called") is not True and not hit):
            return {"llm_status": "NOT_RUN", "provider": "none",
                    "confidence": old_conf, "classification": "REJECT",
                    "failure_text": "",
                    "note": "deterministic reject on record; confidence 0.0 "
                            "is the invalid-post default, not an LLM score"}
        status = {"ACCEPT": "SUCCESS", "REJECT": "REJECT",
                  "REVIEW": "REVIEW"}.get(norm, "REVIEW")
        return {"llm_status": status, "provider": provider,
                "confidence": old_conf, "classification": norm,
                "failure_text": "",
                "note": "stored evaluated outcome (not a failure)"}
    # Deterministic-only records (e2e DETERMINISTIC_REJECT, CSV rejects):
    # confidence 0.0 is the invalid-post default, not an LLM score.
    return {"llm_status": "NOT_RUN", "provider": "none",
            "confidence": old_conf, "classification": old_class or "REJECT",
            "failure_text": "",
            "note": "no LLM evaluation on record; deterministic outcome only"}


def _require_groq_verifier(verifier: Verifier) -> str:
    name = type(verifier.provider).__name__ if verifier.provider else "NONE"
    # Phase 23: failover chains must not enter recovery — recovery is the
    # controlled Groq-only path. Require a SINGLE-provider Groq verifier.
    chain_len = len(getattr(verifier, "providers", [verifier.provider]))
    if name != "GroqProvider" or chain_len != 1:
        raise ValueError(
            "recovery requires an explicit GroqProvider verifier, got %s "
            "(Gemini must not become the recovery provider)" % name)
    return name


def _snapshot_post(classified) -> Dict[str, Any]:
    return {
        "company": getattr(classified, "company_name", "Unclear"),
        "role": getattr(classified, "exact_role", "Unclear"),
        "location": getattr(classified, "location", "Not Specified"),
        "employment_type": getattr(classified, "employment_type", "Unclear"),
        "experience": getattr(classified, "experience_requirement", "Not Specified"),
        "hiring_manager": getattr(classified, "hiring_manager_name", "Unclear"),
        "hm_evidence": getattr(classified, "hiring_manager_evidence", ""),
        "apply_link": getattr(classified, "apply_link", ""),
        "google_form": getattr(classified, "apply_google_form", ""),
        "email": getattr(classified, "cold_email", "Not Available"),
        "confidence": getattr(classified, "confidence", 0.0),
        "classification": ("CANDIDATE" if getattr(classified, "is_valid", False)
                           else "DETERMINISTIC_REJECT"),
        "reason": getattr(classified, "classification_reason", ""),
    }


def _snapshot_merged(merged, gate) -> Dict[str, Any]:
    snap = _snapshot_post(merged)
    snap.update({
        "confidence": gate.llm_confidence,
        "classification": gate.decision,
        "reason": getattr(merged, "classification_reason", ""),
    })
    return snap


def _diff_fields(old: Dict[str, Any], new: Dict[str, Any],
                 resolutions: Dict[str, Any]) -> List[Dict[str, str]]:
    changes = []
    for field in COMPARE_FIELDS:
        o, n = str(old.get(field, "")), str(new.get(field, ""))
        if o == n:
            continue
        res = (resolutions or {}).get(
            {"company": "company", "role": "role", "location": "location",
             "employment_type": "employment_type", "experience": "experience",
             "hiring_manager": "hiring_manager"}.get(field, ""), {})
        why = res.get("evidence", "") if isinstance(res, dict) else ""
        if field in ("apply_link", "google_form", "email") and not why:
            why = ("deterministic literal extraction on recovery input"
                   if n not in ("", "Not Available") else
                   "absent from recovery input")
        changes.append({"field": field, "old": o, "new": n,
                        "reason": why[:200]})
    if str(old.get("confidence", "")) != str(new.get("confidence", "")):
        changes.append({"field": "confidence",
                        "old": str(old.get("confidence", "")),
                        "new": str(new.get("confidence", "")),
                        "reason": ("model evaluated on recovery (was never-evaluated)"
                                   if new.get("confidence") is not None else
                                   "recovery did not evaluate")})
    return changes


def recover_record(record: Dict[str, Any], verifier: Verifier,
                   enricher=None) -> Dict[str, Any]:
    """Recover one failed record. Returns the full old-vs-new comparison.

    `record` keys: source_url, text, author_name, author_profile_url,
    job_card_location/company/employment/experience, post_date,
    input_fidelity (EXACT|DESCRIPTION_DERIVED), old (classify_old_record
    output + original stored fields). No Sheets, no scraping.
    """
    from app.classifier import DeterministicClassifier
    from app.llm.verifier import enriched_post
    from app.models import RawPost

    provider_name = None  # resolved after the no-text short-circuit
    try:
        prior_attempts = int(record.get("prior_attempts", 0))
    except (TypeError, ValueError):
        prior_attempts = 0
    out: Dict[str, Any] = {
        "source_url": record.get("source_url", ""),
        "input_fidelity": record.get("input_fidelity", INPUT_EXACT),
        "old": record.get("old", {}),
        "recovery_provider": "groq",
        "recovery_model": getattr(verifier.provider, "model", ""),
        "groq_calls": 0,
        # Phase 20F: attempt ledger (artifact-only; never a Sheet column,
        # never a confidence, never a decision input).
        "attempt_count": prior_attempts,
        "last_attempt_at": "",
        "retry_eligible": False,
        "deterministic": {}, "recovery": {}, "changed_fields": [],
        "enrichment": None, "resolutions": {},
        "disposition": "HOLD", "disposition_reason": "",
    }
    if not (record.get("text") or "").strip():
        out["disposition_reason"] = "HOLD: no stored text to recover from"
        out["retry_eligible"] = should_retry_recovery(out)
        return out
    provider_name = _require_groq_verifier(verifier)

    clf = DeterministicClassifier()
    post = RawPost(
        post_url=record.get("source_url", "recovery://unknown"),
        post_date=record.get("post_date", ""),
        text=record["text"],
        author_name=record.get("author_name", ""),
        author_profile_url=record.get("author_profile_url", ""),
        company=record.get("job_card_company", "Unclear"),
        job_card_location=record.get("job_card_location", ""),
        job_card_company=record.get("job_card_company", "Unclear"),
        job_card_employment_type=record.get("job_card_employment_type", "Unclear"),
        job_card_experience=record.get("job_card_experience", "Unclear"),
    )
    try:
        classified = clf.classify(post)
    except Exception as e:  # belt-and-braces; classifier never raises by design
        out["deterministic"] = {"error": str(e)[:200]}
        out["disposition_reason"] = "HOLD: classifier error during recovery"
        out["retry_eligible"] = should_retry_recovery(out)
        return out
    out["deterministic"] = _snapshot_post(classified)

    if not classified.is_valid:
        out["recovery"] = dict(out["deterministic"])
        out["disposition"] = "REJECT"
        out["disposition_reason"] = (
            "deterministic gate unchanged (%s); no Groq call made"
            % classified.classification_reason[:150])
        out["retry_eligible"] = should_retry_recovery(out)
        return out

    # Phase 20F: a verify attempt is being made — ledger it. (Explicit caller
    # choice; the QUEUE skips exhausted records via should_retry_recovery.)
    out["attempt_count"] = prior_attempts + 1
    out["last_attempt_at"] = _utc_now_iso()
    try:
        gate = verifier.verify(
            post_text=classified.text, post_url=classified.post_url,
            author_name=classified.author_name,
            author_profile_url=classified.author_profile_url,
            job_card_location=classified.job_card_location,
            job_card_company=classified.job_card_company,
            job_card_employment_type=classified.job_card_employment_type,
            job_card_experience=classified.job_card_experience)
    except Exception as e:  # verifier never raises by design; ERROR state if it does
        out["recovery"] = {"llm_status": "ERROR",
                           "llm_error": str(e)[:200]}
        out["disposition_reason"] = "HOLD: verifier raised during recovery"
        out["retry_eligible"] = should_retry_recovery(out)
        return out
    out["groq_calls"] = 1
    out["recovery"] = {
        "llm_status": gate.llm_status,
        "llm_provider": gate.llm_provider or provider_name,
        "llm_error": gate.llm_error,
        "llm_confidence": gate.llm_confidence,
        "decision": gate.decision,
        "status": gate.status,
        "reasons": list(gate.reasons),
    }
    if gate.llm_status in LLM_NON_EVALUATED:
        out["recovery"].update(_snapshot_post(classified))
        out["disposition"] = "HOLD"
        out["disposition_reason"] = (
            "recovery LLM %s (%s); retry later, keep original REVIEW"
            % (gate.llm_status, (gate.llm_error or "no detail")[:120]))
        out["retry_eligible"] = should_retry_recovery(out)
        return out

    merged = enriched_post(classified, gate)
    snap = _snapshot_merged(merged, gate)
    out["recovery"].update(snap)

    from app.resolution import audit_post_fields
    out["resolutions"] = audit_post_fields(merged)
    out["changed_fields"] = _diff_fields(
        _old_snapshot(record.get("old_stored", {})), snap, out["resolutions"])

    enriched = None
    if gate.decision == "ACCEPT" and enricher is not None:
        try:
            enriched = enricher.enrich_from_classified(
                merged, gate.verdict,
                job_card_company=classified.job_card_company,
                job_card_employment_type=classified.job_card_employment_type,
                job_card_experience=classified.job_card_experience)
        except Exception as e:  # must never break the audit
            enriched = {"enrichment_error": str(e)[:200]}
    if enriched is not None:
        out["enrichment"] = (enriched.model_dump()
                             if hasattr(enriched, "model_dump") else enriched)

    if gate.decision == "ACCEPT":
        if out["input_fidelity"] != INPUT_EXACT:
            out["disposition"] = "REVIEW"
            out["disposition_reason"] = (
                "model ACCEPTed but input is derived/redacted, not the exact "
                "post; confirm on original before any use")
        else:
            out["disposition"] = "ACCEPT"
            out["disposition_reason"] = "model ACCEPT on exact stored text"
    elif gate.decision == "REVIEW":
        out["disposition"] = "REVIEW"
        out["disposition_reason"] = "model held for review: %s" % (
            "; ".join(gate.reasons)[:200])
    else:
        out["disposition"] = "REJECT"
        out["disposition_reason"] = "model rejected: %s" % (
            "; ".join(gate.reasons)[:200])
    out["retry_eligible"] = should_retry_recovery(out)
    return out


def _old_snapshot(stored: Dict[str, Any]) -> Dict[str, Any]:
    get = stored.get
    return {
        "company": get("company", get("merged_company", "Unclear")),
        "role": get("role", get("exact_role", get("merged_exact_role", "Unclear"))),
        "location": get("location", get("merged_location", "Not Specified")),
        "employment_type": get("employment_type",
                               get("merged_employment_type", "Unclear")),
        "experience": get("experience",
                          get("experience_required",
                              get("experience_requirement", "Not Specified"))),
        "hiring_manager": get("hiring_manager",
                              get("hiring_manager_name",
                                  get("merged_hiring_manager", "Unclear"))),
        "apply_link": get("apply_link", ""),
        "google_form": get("google_form", get("apply_google_form", "")),
        "email": get("email", get("cold_email",
                                 get("merged_cold_email", "Not Available"))),
        "confidence": get("confidence", get("confidence_score")),
        "classification": get("classification", get("decision",
                              get("verification_status", get("Status", "")))),
    }


def run_recovery(records: List[Dict[str, Any]], verifier: Verifier,
                 enricher=None) -> List[Dict[str, Any]]:
    """Batch entry point. Sequential Groq calls; returns one comparison dict
    per input record, in order. Raises on non-Groq verifier (fail-closed)."""
    _require_groq_verifier(verifier)
    return [recover_record(rec, verifier, enricher) for rec in records]
