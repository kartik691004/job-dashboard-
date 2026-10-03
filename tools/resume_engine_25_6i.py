"""
resume_engine_25_6i.py — Durable quota-aware resume for the Phase 25.6 replay.

Scope: the 25.6 dataset replay only (raw_dataset.json + llm_cache_25_6d.json).
No Apify, no Sheets writes, no gate/taxonomy/query/routing changes here.

What this module does:
  - pure retry-eligibility policy (no I/O, no sleeping, no network)
  - provider-error classification (pure)
  - ledger load/save + success/throttle recording (ledger stores POINTERS,
    full LLM verdicts live only in llm_cache_25_6d.json)
  - batch selection: max 3, sourcing-score order, skips EVALUATED,
    skips cooldown-ineligible, stops at first throttle (caller-enforced)

What it does NOT do:
  - never calls any provider (the single live probe lives in the phase
    runner script, exactly once per phase)
  - never sleeps for hours (runner EXITS cleanly when unavailable)
  - never caches UNAVAILABLE as a successful evaluation
  - never reprocesses cache keys
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

LEDGER_VERSION = "25.6I-v1"
MAX_AUTO_ATTEMPTS = 3  # 3rd throttle -> manual (explicit future run only)
COOLDOWN_FIRST_S = 6 * 3600  # first throttle -> future quota window
COOLDOWN_SECOND_S = 24 * 3600  # second throttle -> increased cooldown
BATCH_MAX = 3

RATE_LIMITED = "RATE_LIMITED"
AUTH_FAILURE = "AUTH_FAILURE"
NETWORK_FAILURE = "NETWORK_FAILURE"
MALFORMED = "MALFORMED"
OTHER_ERROR = "OTHER_ERROR"

STATUS_UNAVAILABLE = "UNAVAILABLE"
STATUS_EVALUATED = "EVALUATED"
STATUS_READY = "READY_FOR_PRODUCTION"
STATUS_HOLD = "HOLD"
STATUS_REJECT = "REJECT"
ALLOWED_STATUSES = {
    STATUS_UNAVAILABLE, STATUS_EVALUATED, STATUS_READY, STATUS_HOLD, STATUS_REJECT,
}


def classify_provider_error(exc: Exception) -> str:
    """Pure classification of a provider failure (no network)."""
    s = str(exc)
    sl = s.lower()
    if "429" in s or "rate limit" in sl or "rate_limited" in sl:
        return RATE_LIMITED
    if "401" in s or "403" in s or "auth" in sl or "api key" in sl or "permission" in sl:
        return AUTH_FAILURE
    name = type(exc).__name__.lower()
    if ("timeout" in sl or "timed out" in sl or "connect" in sl
            or "network" in sl or "503" in s or "502" in s
            or "timeout" in name or "connect" in name):
        return NETWORK_FAILURE
    if "malformed" in sl or ("json" in sl and "parse" in sl) or "not an object" in sl:
        return MALFORMED
    return OTHER_ERROR


def cooldown_for_attempt(attempt_count: int, error_class: str) -> Optional[timedelta]:
    """Pure cooldown ladder. None => manual (explicit future run only)."""
    if error_class == AUTH_FAILURE:
        return None
    if attempt_count <= 0:
        return timedelta(seconds=COOLDOWN_FIRST_S)
    if attempt_count == 1:
        return timedelta(seconds=COOLDOWN_FIRST_S)
    if attempt_count == 2:
        return timedelta(seconds=COOLDOWN_SECOND_S)
    return None  # 3rd throttle and beyond -> manual


def next_retry_eligible(
    last_attempt_at: datetime, attempt_count: int, error_class: str
) -> Optional[str]:
    """Pure: ISO timestamp when a retry becomes eligible, or None (manual)."""
    cd = cooldown_for_attempt(attempt_count, error_class)
    if cd is None:
        return None
    if last_attempt_at.tzinfo is None:
        last_attempt_at = last_attempt_at.replace(tzinfo=timezone.utc)
    return (last_attempt_at + cd).isoformat()


def _parse_time(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (ValueError, TypeError):
        return None


def is_retry_eligible(entry: dict[str, Any], now: datetime) -> bool:
    """Pure: True only for UNAVAILABLE entries whose cooldown has passed."""
    if entry.get("status") != STATUS_UNAVAILABLE:
        return False
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    eligible_at = _parse_time(entry.get("retry_eligible_at"))
    if eligible_at is None:
        # None means manual-only (explicit future run); a missing field on a
        # fresh UNAVAILABLE entry means "eligible now".
        if "retry_eligible_at" in entry:
            return False
        return True
    return now >= eligible_at


def select_next(
    ordered_keys: list[str],
    ledger_entries: dict[str, dict[str, Any]],
    cache_keys: set[str],
    now: datetime,
    max_n: int = BATCH_MAX,
    include_manual: bool = False,
) -> list[str]:
    """Pure: next up-to-max_n keys in stable order.

    Skips anything already EVALUATED (cache hit), anything not UNAVAILABLE,
    and anything still in cooldown. Manual-only entries (retry_eligible_at
    None after 3 throttles / AUTH) are skipped unless include_manual=True,
    which only an explicit future phase run may pass after its quota gate
    passes. Never reorders, never cherry-picks.
    """
    picked: list[str] = []
    for key in ordered_keys:
        if len(picked) >= max_n:
            break
        if key in cache_keys:
            continue
        entry = ledger_entries.get(key)
        if entry is None:
            continue
        if entry.get("retry_eligible_at") is None and "retry_eligible_at" in entry:
            if not include_manual:
                continue
            if entry.get("status") != STATUS_UNAVAILABLE:
                continue
        elif not is_retry_eligible(entry, now):
            continue
        picked.append(key)
    return picked


def record_success(
    ledger_entries: dict[str, dict[str, Any]],
    cache: dict[str, Any],
    key: str,
    *,
    provider: str,
    confidence: float,
    decision: str,
    llm_status: str,
    verdict: dict[str, Any],
    timestamp: str,
) -> None:
    """Record a successful LLM evaluation immediately (in-memory; caller saves).

    Ledger keeps POINTER fields only (no verdict duplication); the full
    verdict lives in llm_cache_25_6d.json.
    """
    cache[key] = {
        "decision": decision,
        "status": "New" if decision == "ACCEPT" else ("Review" if decision == "REVIEW" else ""),
        "reasons": [],
        "llm_status": llm_status,
        "llm_error": "",
        "llm_provider": provider,
        "verdict": verdict,
        "timestamp": timestamp,
        "attempts": 1,
    }
    entry = ledger_entries.get(key, {"key": key, "status": STATUS_UNAVAILABLE})
    if decision == "ACCEPT" and llm_status == "SUCCESS":
        status = STATUS_READY  # gate ELIGIBLE still verified downstream; never assumed
    elif decision == "REJECT":
        status = STATUS_REJECT
    elif decision in ("ACCEPT", "REVIEW"):
        status = STATUS_HOLD if decision == "REVIEW" else STATUS_EVALUATED
    else:
        status = STATUS_EVALUATED
    entry.update({
        "status": status,
        "last_provider": provider,
        "last_error_class": None,
        "last_success_at": timestamp,
        "confidence": confidence,
        "decision": decision,
    })
    ledger_entries[key] = entry


def record_throttle(
    ledger_entries: dict[str, dict[str, Any]],
    key: str,
    *,
    provider: str,
    error_class: str,
    now: datetime,
) -> dict[str, Any]:
    """Record a provider failure WITHOUT touching the LLM cache.

    Increments attempt_count, stamps last_attempt_at, recomputes
    retry_eligible_at. Returns the updated entry. Caller must STOP the batch.
    """
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    entry = ledger_entries.get(key, {"key": key, "status": STATUS_UNAVAILABLE,
                                     "attempt_count": 0})
    attempts = int(entry.get("attempt_count", 0) or 0) + 1
    eligible_at = next_retry_eligible(now, attempts, error_class)
    entry.update({
        "status": STATUS_UNAVAILABLE,
        "attempt_count": attempts,
        "last_attempt_at": now.isoformat(),
        "last_provider": provider,
        "last_error_class": error_class,
        "retry_eligible_at": eligible_at,
    })
    # Provider-specific block (25.6O): only the failing provider is blocked;
    # the other provider's field is left untouched.
    short = _short_provider(provider)
    if short and short in _PROVIDER_FIELDS:
        entry[_PROVIDER_FIELDS[short]] = eligible_at
    ledger_entries[key] = entry
    return entry


# ── Phase 25.6P: adaptive provider circuit breaker ─────────────────────
# Execution-window runtime state ONLY (never persisted, no secrets).
# A provider that 429s mid-window is disabled for the rest of the window so
# the next candidate goes straight to the healthy provider instead of
# burning a call on the known-throttled one. A provider that serves both
# Gemini keys into the same model-level 503 is marked model-unavailable.
# Configured chain order (Groq -> Gemini) is never changed; this only
# routes within one execution window.

CB_SERVING = "SERVING"
CB_RATE_LIMITED = "RATE_LIMITED"
CB_UNAVAILABLE = "UNAVAILABLE"


class ProviderCircuit:
    """In-memory per-provider health for one execution window."""

    def __init__(self, window_id: str = "window"):
        self.window_id = window_id
        self.state = {
            "groq": {"status": CB_SERVING, "last_success_at": None,
                     "last_failure_at": None, "consecutive_throttles": 0,
                     "cooldown_until": None, "last_error_class": None,
                     "calls": 0, "calls_avoided": 0},
            "gemini": {"status": CB_SERVING, "last_success_at": None,
                       "last_failure_at": None, "consecutive_throttles": 0,
                       "cooldown_until": None, "last_error_class": None,
                       "calls": 0, "calls_avoided": 0},
        }

    def report_probe(self, provider: str, ok: bool,
                     error_class: str | None = None,
                     now: str | None = None) -> None:
        st = self.state[provider]
        if ok:
            st["status"] = CB_SERVING
            st["consecutive_throttles"] = 0
        else:
            st["status"] = CB_RATE_LIMITED if error_class == RATE_LIMITED else CB_UNAVAILABLE
            st["last_failure_at"] = now
            st["last_error_class"] = error_class

    def record_success(self, provider: str, now: str | None = None) -> None:
        st = self.state[provider]
        st["calls"] += 1
        st["status"] = CB_SERVING
        st["consecutive_throttles"] = 0
        st["last_success_at"] = now
        st["last_error_class"] = None

    def record_throttle(self, provider: str, error_class: str,
                        now: str | None = None) -> None:
        """Mark provider down for the rest of this window.

        429/quota -> RATE_LIMITED; model-level 503 (both Gemini keys failing
        the same way) -> UNAVAILABLE. Either way the breaker is open: no
        further calls to this provider until a new window.
        """
        st = self.state[provider]
        st["calls"] += 1
        st["consecutive_throttles"] += 1
        st["last_failure_at"] = now
        st["last_error_class"] = error_class
        st["status"] = CB_UNAVAILABLE if error_class == NETWORK_FAILURE else CB_RATE_LIMITED

    def mark_avoided(self, provider: str) -> None:
        """Count a call saved by the open breaker (efficiency metric)."""
        self.state[provider]["calls_avoided"] += 1

    def available(self, provider: str) -> bool:
        return self.state[provider]["status"] == CB_SERVING

    def active(self) -> str | None:
        """Best current provider: recent success > probe-ok-no-throttle.

        Groq keeps configured priority on ties; never returns a broken
        provider; None means the window is exhausted.
        """
        scored = []
        for name in ("groq", "gemini"):
            st = self.state[name]
            if st["status"] != CB_SERVING:
                continue
            scored.append((st["last_success_at"] is not None,
                           name == "groq", name))
        if not scored:
            return None
        scored.sort(reverse=True)
        return scored[0][2]


def load_ledger(path: Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def save_ledger(path: Path, ledger: dict[str, Any]) -> None:
    Path(path).write_text(json.dumps(ledger, ensure_ascii=False, indent=1), encoding="utf-8")


# ── Phase 25.6O: provider-specific availability ──────────────────────────
# A global retry timestamp must not block a healthy provider. Each ledger
# entry may carry per-provider blocks ("next_groq_retry_at" /
# "next_gemini_retry_at"). Absent field = no block recorded for that
# provider (live preflight decides). Present-None = explicit manual block.
# The legacy global "retry_eligible_at" is retained for compatibility and
# is used only when no per-provider field exists for the active provider.

_PROVIDER_FIELDS = {"groq": "next_groq_retry_at", "gemini": "next_gemini_retry_at"}


def _short_provider(name: str) -> str:
    low = (name or "").lower()
    if "groq" in low:
        return "groq"
    if "gemini" in low:
        return "gemini"
    return ""


def eligible_for_provider(entry: dict[str, Any], provider: str,
                          now: datetime, probe_ok: bool) -> bool:
    """Pure: may this UNAVAILABLE entry be attempted via `provider`?"""
    if not probe_ok:
        return False
    if entry.get("status") != STATUS_UNAVAILABLE:
        return False
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    field = _PROVIDER_FIELDS.get(provider, "")
    if field and field in entry:
        ts = _parse_time(entry.get(field))
        if ts is None:
            return False
        return now >= ts
    # No block recorded for this provider: the live preflight decides.
    # A legacy global timestamp never blocks a provider with no recorded
    # block of its own (25.6O: stale global cooldowns must not freeze a
    # healthy path; the global field is retained as audit trail for the
    # legacy global selector only).
    return True


def select_next_for_provider(
    ordered_keys: list[str],
    ledger_entries: dict[str, dict[str, Any]],
    cache_keys: set[str],
    now: datetime,
    provider: str,
    probe_ok: bool,
    max_n: int = BATCH_MAX,
) -> list[str]:
    """Pure: next up-to-max_n keys eligible via `provider`, stable order."""
    picked: list[str] = []
    for key in ordered_keys:
        if len(picked) >= max_n:
            break
        if key in cache_keys:
            continue
        entry = ledger_entries.get(key)
        if entry is None:
            continue
        if not eligible_for_provider(entry, provider, now, probe_ok):
            continue
        picked.append(key)
    return picked
