"""
test_phase23_failover.py — Phase 23 multi-provider availability failover.

Offline only: no Apify, no Sheets, no network. Providers are scripted fakes;
chain-construction tests monkeypatch app.config (never .env, never secrets).

Precision contract pinned here:
  - failover ONLY on availability/transport failures
  - legitimate ACCEPT/REVIEW/REJECT NEVER advances the chain
  - AUTH and MALFORMED never fail over, never become ACCEPT
  - all-unavailable -> REVIEW/UNAVAILABLE with null confidence, never REJECT
  - bounded calls, provenance recorded, no credential leakage
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import DRY_RUN
from app.llm.base import LLMError
from app.llm.failover import (
    AUTH_FAILURE,
    DNS_FAILURE,
    MALFORMED_RESPONSE,
    PROVIDER_ERROR,
    RATE_LIMITED,
    TIMEOUT,
    build_provider_chain,
    classify_llm_error,
    describe_chain,
    should_failover,
)
from app.llm.verifier import SYSTEM_PROMPT, Verifier, _sanitize_error


# ── Scripted providers ───────────────────────────────────────────────────────

class ScriptedProvider:
    """LLMProvider stub: fails with `error` or returns `payload`."""

    def __init__(self, payload=None, error=None):
        self.payload = payload
        self.error = error
        self.calls = 0

    def complete_json(self, system, user):
        self.calls += 1
        if self.error is not None:
            raise LLMError(self.error)
        return dict(self.payload or {})


def named_provider(payload=None, error=None, name="ScriptedProvider"):
    """ScriptedProvider with a distinct class name (per-provider provenance)."""
    cls = type(name, (ScriptedProvider,), {})
    return cls(payload=payload, error=error)


def groq_named(inner, name="GroqProvider"):
    inner.__class__ = type(name, (ScriptedProvider,), {})
    return inner


def gemini_named(inner, name="GeminiProvider"):
    inner.__class__ = type(name, (ScriptedProvider,), {})
    return inner


def accept_payload(**over):
    base = {
        "decision": "ACCEPT", "confidence": 0.95,
        "is_current_job": True, "is_genuine_hiring": True,
        "is_target_role": True, "major_category": "Founder's Office",
        "actual_role": "Founder's Office Associate",
        "exact_role": "Founder's Office Associate",
        "company": "Acme Robotics", "india_relevance": "India",
        "location": "Mumbai", "employment_type": "Full-time",
        "reason": "genuine current vacancy",
    }
    base.update(over)
    return base


def chain_verifier(*providers):
    """Verifier with an explicit ordered chain (bypasses config)."""
    v = Verifier(provider=providers[0])
    v.providers = list(providers)
    v.provider = providers[0]
    return v


# ── Failure classification ───────────────────────────────────────────────────

def test_classify_rate_limited():
    assert classify_llm_error(LLMError("Groq HTTP 429 rate limit exceeded")) == RATE_LIMITED
    assert classify_llm_error(LLMError("Gemini HTTP 429 blah")) == RATE_LIMITED


def test_classify_dns():
    assert classify_llm_error(
        LLMError("Groq call failed: [Errno 11001] getaddrinfo failed")) == DNS_FAILURE
    assert classify_llm_error(LLMError("Temporary failure in name resolution")) == DNS_FAILURE


def test_classify_timeout():
    assert classify_llm_error(LLMError("Read timed out")) == TIMEOUT
    assert classify_llm_error(LLMError("httpx ConnectTimeout")) == TIMEOUT


def test_classify_auth_never_failover():
    assert classify_llm_error(LLMError("Gemini HTTP 401 invalid api key")) == AUTH_FAILURE
    assert classify_llm_error(LLMError("Groq HTTP 403 forbidden")) == AUTH_FAILURE
    assert not should_failover(AUTH_FAILURE)


def test_failover_eligible_set():
    assert should_failover(RATE_LIMITED)
    assert should_failover(DNS_FAILURE)
    assert should_failover(TIMEOUT)
    assert should_failover(PROVIDER_ERROR)
    assert not should_failover(AUTH_FAILURE)
    assert not should_failover(MALFORMED_RESPONSE)


# ── Semantic results never fail over (1-4) ───────────────────────────────────

def test_groq_success_no_fallback():
    groq = groq_named(ScriptedProvider(payload=accept_payload()))
    gemini = gemini_named(ScriptedProvider(payload=accept_payload(company="Other")))
    gate = chain_verifier(groq, gemini).verify(post_text="We are hiring")
    assert gate.decision == "ACCEPT"
    assert gemini.calls == 0
    assert gate.llm_provider == "GroqProvider"


def test_groq_legitimate_reject_no_fallback():
    groq = groq_named(ScriptedProvider(payload={
        "decision": "REJECT", "confidence": 0.98, "is_target_role": False,
        "major_category": "None", "actual_role": "Software Engineer",
        "reason": "wrong role"}))
    gemini = gemini_named(ScriptedProvider(payload=accept_payload()))
    gate = chain_verifier(groq, gemini).verify(post_text="x")
    assert gate.decision == "REJECT"
    assert gate.llm_status == "REJECT"
    assert gemini.calls == 0  # weaker provider never overrides


def test_groq_legitimate_review_no_fallback():
    groq = groq_named(ScriptedProvider(payload=accept_payload(
        decision="REVIEW", confidence=0.82,
        reason="borderline, needs human check")))
    gemini = gemini_named(ScriptedProvider(payload=accept_payload()))
    gate = chain_verifier(groq, gemini).verify(post_text="x")
    assert gate.decision == "REVIEW"
    assert gemini.calls == 0


def test_groq_legitimate_accept_no_fallback():
    groq = groq_named(ScriptedProvider(payload=accept_payload(confidence=0.97)))
    gemini = gemini_named(ScriptedProvider(payload=accept_payload()))
    gate = chain_verifier(groq, gemini).verify(post_text="x")
    assert gate.decision == "ACCEPT"
    assert gate.llm_status == "SUCCESS"
    assert gemini.calls == 0


# ── Transport failures fail over (5-8) ───────────────────────────────────────

def test_groq_429_falls_back_to_gemini():
    groq = groq_named(ScriptedProvider(error="Groq HTTP 429 rate limit exceeded after retries"))
    gemini = gemini_named(ScriptedProvider(payload=accept_payload()))
    gate = chain_verifier(groq, gemini).verify(post_text="x")
    assert gate.decision == "ACCEPT"
    assert gate.llm_provider == "GeminiProvider"
    assert gemini.calls >= 1


def test_groq_dns_falls_back_to_gemini():
    groq = groq_named(ScriptedProvider(error="[Errno 11001] getaddrinfo failed"))
    gemini = gemini_named(ScriptedProvider(payload=accept_payload()))
    gate = chain_verifier(groq, gemini).verify(post_text="x")
    assert gate.decision == "ACCEPT"
    assert gate.llm_provider == "GeminiProvider"


def test_groq_timeout_falls_back_to_gemini():
    groq = groq_named(ScriptedProvider(error="Groq call failed: Read timed out"))
    gemini = gemini_named(ScriptedProvider(payload=accept_payload()))
    gate = chain_verifier(groq, gemini).verify(post_text="x")
    assert gate.decision == "ACCEPT"
    assert gate.llm_provider == "GeminiProvider"


def test_three_provider_chain_extensible_for_gpt():
    """Groq + Gemini down -> third provider (GPT slot) serves.

    GPT is not implemented in Phase 23, so the third hop is a stub proving
    the chain generalizes beyond two providers without code changes.
    """
    groq = groq_named(ScriptedProvider(error="Groq HTTP 503 overloaded"))
    gemini = gemini_named(ScriptedProvider(error="Gemini HTTP 503 overloaded"))
    gpt = named_provider(payload=accept_payload(), name="OpenAIProvider")
    gate = chain_verifier(groq, gemini, gpt).verify(post_text="x")
    assert gate.decision == "ACCEPT"
    assert gate.llm_provider == "OpenAIProvider"


# ── Exhaustion / safety (9-14) ───────────────────────────────────────────────

def test_all_unavailable_gives_review_null_confidence():
    groq = groq_named(ScriptedProvider(error="Groq HTTP 429 rate limit exceeded"))
    gemini = gemini_named(ScriptedProvider(error="Gemini HTTP 503 overloaded"))
    gate = chain_verifier(groq, gemini).verify(post_text="x")
    assert gate.decision == "REVIEW"
    assert gate.status == "Review"
    assert gate.llm_status == "UNAVAILABLE"
    assert gate.llm_confidence is None  # never 0.00, never REJECT
    assert gate.decision not in ("ACCEPT", "REJECT")


def test_fallback_success_preserves_hard_gates():
    """Schema/gates unchanged: fallback ACCEPT with internship still REJECTs."""
    groq = groq_named(ScriptedProvider(error="Groq HTTP 429"))
    gemini = gemini_named(ScriptedProvider(payload=accept_payload(
        employment_type="Internship", confidence=0.99)))
    gate = chain_verifier(groq, gemini).verify(post_text="x")
    assert gate.decision == "REJECT"
    assert any("internship" in r.lower() for r in gate.reasons)


def test_provenance_records_serving_provider():
    groq = groq_named(ScriptedProvider(error="Groq HTTP 429"))
    gemini = gemini_named(ScriptedProvider(payload=accept_payload()))
    gate = chain_verifier(groq, gemini).verify(post_text="x")
    assert gate.llm_provider == "GeminiProvider"
    assert gate.llm_status == "SUCCESS"


def test_no_infinite_retries_bounded_calls():
    groq = groq_named(ScriptedProvider(error="Groq HTTP 503"))
    gemini = gemini_named(ScriptedProvider(error="Gemini HTTP 503"))
    v = chain_verifier(groq, gemini)
    gate = v.verify(post_text="x")
    assert gate.llm_status == "UNAVAILABLE"
    # max 2 attempts per provider (pre-Phase-23 bound) x 2 providers.
    assert groq.calls == 2
    assert gemini.calls == 2
    assert v.llm_calls == 4


def test_auth_failure_does_not_failover():
    groq = groq_named(ScriptedProvider(error="Groq HTTP 401 invalid api key"))
    gemini = gemini_named(ScriptedProvider(payload=accept_payload()))
    gate = chain_verifier(groq, gemini).verify(post_text="x")
    assert gate.llm_status == "UNAVAILABLE"
    assert gate.decision == "REVIEW"
    assert gemini.calls == 0  # bad credentials surface, never masked


def test_malformed_response_does_not_failover_or_accept():
    MalformedGroq = type("GroqProvider", (ScriptedProvider,), {
        "complete_json": lambda self, system, user: (
            setattr(self, "calls", self.calls + 1),
            {"decision": 5},
        )[1],
    })
    bad = MalformedGroq()
    gemini = gemini_named(ScriptedProvider(payload=accept_payload()))
    v = chain_verifier(bad, gemini)
    gate = v.verify(post_text="x")
    assert gate.decision == "REVIEW"
    assert gate.llm_status == "UNAVAILABLE"
    assert gemini.calls == 0
    assert bad.calls == 2  # same-provider retry bound preserved


def test_no_credential_leakage_in_failover_errors():
    groq = groq_named(ScriptedProvider(error="Groq HTTP 503"))
    gemini = gemini_named(ScriptedProvider(error="Gemini HTTP 503"))
    gate = chain_verifier(groq, gemini).verify(post_text="x")
    blob = gate.llm_error + " " + " ".join(gate.reasons)
    assert "gsk_" not in blob and "sk-" not in blob and "AQ." not in blob
    assert "Bearer" not in blob
    # Sanitizer pins (unit-level).
    assert "[redacted]" in _sanitize_error("Bearer gsk_abc123 boom")
    assert "[redacted]" in _sanitize_error("key AQ.Ab8XYZ boom")


def test_dry_run_safe_and_no_external_imports():
    assert DRY_RUN is True
    import pathlib
    for rel in ("app/llm/failover.py", "app/llm/verifier.py"):
        src = pathlib.Path(rel).read_text(encoding="utf-8")
        for line in src.splitlines():
            s = line.strip()
            if s.startswith("import ") or s.startswith("from "):
                assert "gspread" not in s and "sheets_writer" not in s
                assert "apify" not in s.lower()


def test_disabled_verifier_still_not_run():
    gate = Verifier(None).verify(post_text="x")
    assert gate.decision == "REVIEW"
    assert gate.llm_status == "NOT_RUN"
    assert gate.llm_confidence is None


# ── Chain construction ───────────────────────────────────────────────────────

@pytest.fixture
def clean_config(monkeypatch):
    import app.config as cfg
    vals = dict(GROQ_API_KEY="", GROQ_MODEL="groq-m", GEMINI_API_KEY="",
                GEMINI_MODEL="", LLM_PROVIDER="auto", LLM_FAILOVER_ORDER="",
                GROQ_TIMEOUT_S=5.0, GROQ_MAX_RETRIES=0,
                OPENAI_API_KEY="", OPENAI_MODEL="")
    for k, v in vals.items():
        monkeypatch.setattr(cfg, k, v)
    return cfg


def test_chain_auto_prefers_groq_then_gemini(clean_config):
    from app.llm.gemini_provider import GeminiProvider
    from app.llm.groq_provider import GroqProvider
    clean_config.GROQ_API_KEY = "g"
    clean_config.GEMINI_API_KEY = "m"
    clean_config.GEMINI_MODEL = "gem-x"
    chain = build_provider_chain()
    assert isinstance(chain[0], GroqProvider)
    assert isinstance(chain[1], GeminiProvider)
    assert describe_chain(chain) == "groq->gemini"


def test_chain_gemini_only_when_groq_missing(clean_config):
    from app.llm.gemini_provider import GeminiProvider
    clean_config.GEMINI_API_KEY = "m"
    clean_config.GEMINI_MODEL = "gem-x"
    chain = build_provider_chain()
    assert len(chain) == 1 and isinstance(chain[0], GeminiProvider)


def test_chain_explicit_gemini_puts_gemini_first(clean_config):
    from app.llm.gemini_provider import GeminiProvider
    from app.llm.groq_provider import GroqProvider
    clean_config.GROQ_API_KEY = "g"
    clean_config.GEMINI_API_KEY = "m"
    clean_config.GEMINI_MODEL = "gem-x"
    clean_config.LLM_PROVIDER = "gemini"
    chain = build_provider_chain()
    assert isinstance(chain[0], GeminiProvider)
    assert isinstance(chain[1], GroqProvider)


def test_chain_order_configurable(clean_config):
    from app.llm.gemini_provider import GeminiProvider
    clean_config.GROQ_API_KEY = "g"
    clean_config.GEMINI_API_KEY = "m"
    clean_config.GEMINI_MODEL = "gem-x"
    clean_config.LLM_FAILOVER_ORDER = "gemini,groq"
    chain = build_provider_chain()
    assert isinstance(chain[0], GeminiProvider)


def test_chain_openai_skipped_until_implemented(clean_config):
    clean_config.GROQ_API_KEY = "g"
    clean_config.GEMINI_API_KEY = "m"
    clean_config.GEMINI_MODEL = "gem-x"
    clean_config.OPENAI_API_KEY = "sk-test"
    clean_config.OPENAI_MODEL = "gpt-x"
    clean_config.LLM_FAILOVER_ORDER = "groq,gemini,openai"
    chain = build_provider_chain()
    assert len(chain) == 2  # openai reserved, never constructed/called
    assert describe_chain(chain) == "groq->gemini"


def test_prompt_unchanged():
    # Acceptance bars / rejection rules / employment / location policy live in
    # the prompt + _gate; failover must not weaken them.
    assert "confidence >= 0.90" in SYSTEM_PROMPT
    assert "SINGLE-EMPLOYER MULTI-ROLE" in SYSTEM_PROMPT
    assert "FULL-TIME ONLY" in SYSTEM_PROMPT
    assert "INDIA RULE" in SYSTEM_PROMPT
