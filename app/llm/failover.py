"""
failover.py — Phase 23 multi-provider availability failover (Groq -> Gemini).

Availability failover ONLY. This is NOT ensemble voting: the first provider
that returns a legitimate semantic result (ACCEPT / REVIEW / REJECT, including
a hard-gate REJECT) wins and no further provider is consulted. A weaker
provider can never override a valid result because it is never called once a
result exists.

Failover triggers ONLY on provider UNAVAILABILITY (transport / rate-limit /
server-side failures classified as failover-eligible). Legitimate semantic
results, AUTH failures, and malformed structured output NEVER trigger
failover.

Bounded by construction: each provider keeps its own internal retry budget,
the verifier makes at most 2 attempts per provider, and the chain length
bounds total calls (currently max 2 providers -> max 4 complete_json calls
per verify()). No infinite retries.

GPT / OpenAI is reserved in the ordering logic but NOT implemented in this
phase (per user instruction: Gemini only for now). An unimplemented or
unconfigured provider is silently skipped — never an error, never a call.
"""

from __future__ import annotations

import logging
from typing import List

logger = logging.getLogger("LLMFailover")

# ── Failure vocabulary (Step 6) ──────────────────────────────────────────────
SUCCESS = "SUCCESS"
RATE_LIMITED = "RATE_LIMITED"
DNS_FAILURE = "DNS_FAILURE"
CONNECTION_FAILURE = "CONNECTION_FAILURE"
TIMEOUT = "TIMEOUT"
TLS_FAILURE = "TLS_FAILURE"
AUTH_FAILURE = "AUTH_FAILURE"
PROVIDER_ERROR = "PROVIDER_ERROR"
MALFORMED_RESPONSE = "MALFORMED_RESPONSE"

# Only genuine availability/transport failures fail over. AUTH (config error,
# must surface — never mask by silently shifting load) and MALFORMED (schema
# failure — preserve fail-closed REVIEW, never convert to ACCEPT/REJECT) do
# NOT fail over.
FAILOVER_ELIGIBLE = frozenset({
    RATE_LIMITED,
    DNS_FAILURE,
    CONNECTION_FAILURE,
    TIMEOUT,
    TLS_FAILURE,
    PROVIDER_ERROR,
})

# Canonical chain order. GPT is last and currently unimplemented (skipped).
DEFAULT_ORDER = ("groq", "gemini", "openai")
KNOWN_PROVIDERS = ("groq", "gemini", "openai")


def classify_llm_error(err: BaseException) -> str:
    """Classify a provider transport failure for failover routing.

    Works on the wrapped LLMError message text (providers wrap httpx errors
    into LLMError with the original message nested) and — defensively — on
    raw httpx exception types if one ever bubbles up unwrapped.
    Never raises; unknown shapes map to PROVIDER_ERROR (eligible, bounded).
    """
    # Raw httpx types first (defensive; providers normally wrap these).
    try:
        import httpx  # local import: never a hard dependency of this module
        if isinstance(err, httpx.TimeoutException):
            return TIMEOUT
        if isinstance(err, httpx.ConnectError):
            blob = str(err).lower()
            if any(m in blob for m in (
                    "getaddrinfo", "dns", "name or service not known",
                    "temporary failure in name resolution",
                    "nodename nor servname", "no such host",
                    "name resolution")):
                return DNS_FAILURE
            return CONNECTION_FAILURE
        if isinstance(err, httpx.HTTPError):
            blob = str(err).lower()
            if "ssl" in blob or "tls" in blob or "certificate" in blob:
                return TLS_FAILURE
    except Exception:
        pass

    msg = " ".join(str(err or "").split()).lower()

    # AUTH first: a 401/403 or key-validity message is a config error even
    # when it also contains generic HTTP words. Bare "400" alone is NOT auth
    # (e.g. Groq json_validate_failed is a provider-side shape error).
    if any(m in msg for m in (
            "401", "403", "unauthorized", "forbidden",
            "invalid api key", "api key not valid", "api_key_invalid",
            "api key is invalid", "api key expired", "invalid gemini key",
            "permission denied", "authentication failed",
            "authentication error")):
        return AUTH_FAILURE
    if "api key" in msg and any(m in msg for m in (
            "invalid", "not valid", "expired", "missing", "required")):
        return AUTH_FAILURE

    if any(m in msg for m in (
            "429", "rate limit", "rate-limit", "rate_limited",
            "too many requests", "quota exceeded", "quota-exceeded",
            "resource exhausted")):
        return RATE_LIMITED

    if any(m in msg for m in (
            "getaddrinfo", "dns", "name or service not known",
            "temporary failure in name resolution",
            "nodename nor servname", "no such host", "name resolution",
            "errno 11001")):
        return DNS_FAILURE

    if any(m in msg for m in (
            "timeout", "timed out", "deadline exceeded",
            "readtimeout", "connecttimeout", "writetimeout", "pooltimeout")):
        return TIMEOUT

    if any(m in msg for m in (
            "tls", " ssl", "ssl ", "certificate", "handshake",
            "verify failed", "cert verify", "cert_validate")):
        return TLS_FAILURE

    return PROVIDER_ERROR


def should_failover(kind: str) -> bool:
    """True only for genuine availability/transport failures."""
    return kind in FAILOVER_ELIGIBLE


def short_name(provider) -> str:
    """Config-order name for a provider instance (no secrets, no models)."""
    cls = type(provider).__name__
    return {
        "GroqProvider": "groq",
        "GeminiProvider": "gemini",
        "ExplabsProvider": "explabs",
        "OpenAIProvider": "openai",
    }.get(cls, cls)


def describe_chain(chain) -> str:
    """One-line chain summary for logs/artifacts (names only, never keys)."""
    if not chain:
        return "none (disabled)"
    return "->".join(short_name(p) for p in chain)


def _parse_order(raw: str) -> List[str]:
    parts = [p.strip().lower() for p in str(raw or "").split(",")]
    order = [p for p in parts if p in KNOWN_PROVIDERS]
    # De-dupe, preserve first occurrence.
    seen: List[str] = []
    for p in order:
        if p not in seen:
            seen.append(p)
    return seen


def build_provider_chain():
    """Build the ordered provider chain (Groq -> Gemini; OpenAI reserved).

    Rules (back-compatible, explicit, configurable):
      - LLM_PROVIDER=explabs -> legacy single [ExplabsProvider] (no
        failover), preserving pre-Phase-23 behaviour for that path.
      - Otherwise the order is LLM_FAILOVER_ORDER when set to a non-empty
        known subset, else the default groq->gemini (+openai when
        implemented). LLM_PROVIDER=gemini puts gemini first (primary
        first); groq/auto/empty keep the default order.
      - Only CONFIGURED providers are included (Groq needs GROQ_API_KEY;
        Gemini needs GEMINI_API_KEY + GEMINI_MODEL). Unconfigured or
        unimplemented names (openai in this phase) are skipped silently.
      - Never raises: misconfiguration yields a shorter chain (possibly
        empty -> verifier DISABLED mode -> REVIEW/NOT_RUN).
    """
    from app import config as cfg

    def make_groq():
        key = getattr(cfg, "GROQ_API_KEY", "")
        if not key:
            return None
        from app.llm.groq_provider import GroqProvider
        model = getattr(cfg, "GROQ_MODEL", "") or "llama-3.3-70b-versatile"
        return GroqProvider(key, model,
                            getattr(cfg, "GROQ_TIMEOUT_S", 30.0),
                            getattr(cfg, "GROQ_MAX_RETRIES", 1))

    def make_gemini():
        from app.llm.gemini_provider import GeminiProvider, collect_gemini_keys
        keys = collect_gemini_keys(
            getattr(cfg, "GEMINI_API_KEY", ""),
            getattr(cfg, "GEMINI_API_KEY_2", ""),
            getattr(cfg, "GEMINI_API_KEYS", ""))
        model = getattr(cfg, "GEMINI_MODEL", "")
        if not keys or not model:
            return None
        return GeminiProvider(keys, model,
                              getattr(cfg, "GROQ_TIMEOUT_S", 30.0),
                              getattr(cfg, "GROQ_MAX_RETRIES", 1))

    def make_explabs():
        key = getattr(cfg, "GROQ_API_KEY", "")
        if not key:
            return None
        from app.llm.explabs_provider import ExplabsProvider
        model = getattr(cfg, "GROQ_MODEL", "") or "deepseek-v4-flash"
        return ExplabsProvider(key, model,
                               getattr(cfg, "GROQ_TIMEOUT_S", 30.0),
                               getattr(cfg, "GROQ_MAX_RETRIES", 1))

    llm_provider = str(getattr(cfg, "LLM_PROVIDER", "auto") or "auto").strip().lower()

    if llm_provider == "explabs":
        # Legacy path: single provider, no failover (pre-Phase-23 parity).
        p = make_explabs()
        return [p] if p is not None else []

    makers = {"groq": make_groq, "gemini": make_gemini, "openai": lambda: None}

    raw_order = str(getattr(cfg, "LLM_FAILOVER_ORDER", "") or "").strip().lower()
    order = _parse_order(raw_order)
    if not order:
        if llm_provider == "gemini":
            order = ["gemini", "groq", "openai"]
        else:
            order = list(DEFAULT_ORDER)

    chain = []
    for name in order:
        try:
            inst = makers[name]()
        except Exception as e:  # never let a bad provider break selection
            logger.warning("Failover: provider '%s' failed to construct: %s",
                           name, type(e).__name__)
            continue
        if inst is not None:
            chain.append(inst)
    return chain
