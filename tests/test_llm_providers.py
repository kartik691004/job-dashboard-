"""
test_llm_providers.py — provider selection + Gemini/Groq transports.

No network: HTTP is mocked at the httpx boundary. Verifies the LLMProvider
contract, JSON parsing, error->LLMError mapping, and that API keys never leak
into exceptions or URLs.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import app.llm.gemini_provider as gem_mod
import httpx
from app.llm.base import LLMError
from app.llm.gemini_provider import GeminiProvider
from app.llm.groq_provider import GroqProvider


class FakeResponse:
    def __init__(self, status_code=200, json_body=None):
        self.status_code = status_code
        self._json = json_body if json_body is not None else {}

    def json(self):
        return self._json


def gemini_ok_body(text='{"decision": "ACCEPT", "confidence": 0.95}'):
    return {"candidates": [{"content": {"parts": [{"text": text}]}}]}


# ── Key handling ──────────────────────────────────────────────────────────────

def test_missing_key_raises_without_leaking():
    for cls in (GroqProvider, GeminiProvider):
        with pytest.raises(LLMError) as e:
            cls("", "some-model")
        assert "some-model" not in str(e.value)


def test_key_never_appears_in_transport_errors(monkeypatch):
    def boom(*a, **kw):
        raise httpx.ConnectError("network down")

    monkeypatch.setattr(gem_mod.httpx, "post", boom)
    prov = GeminiProvider("SECRET-KEY", "gemini-x")
    with pytest.raises(LLMError) as e:
        prov.complete_json("sys", "usr")
    assert "SECRET-KEY" not in str(e.value)


# ── Gemini happy path ─────────────────────────────────────────────────────────

def test_gemini_parses_json_content(monkeypatch):
    captured = {}

    def fake_post(url, json=None, headers=None, timeout=None):
        captured.update(url=url, headers=headers)
        return FakeResponse(200, gemini_ok_body())

    monkeypatch.setattr(gem_mod.httpx, "post", fake_post)
    out = GeminiProvider("K", "gemini-x").complete_json("SYS", "USR")

    assert out["decision"] == "ACCEPT"
    assert "gemini-x" in captured["url"]
    assert captured["headers"]["x-goog-api-key"] == "K"   # header, never query param
    # Request shape: system instruction + user content + JSON mime type.
    sent = captured.get("json") or {}
    # fake_post received kwargs; re-read via closure trick below in payload test.


def test_gemini_request_shape(monkeypatch):
    seen = {}

    def fake_post(url, json=None, headers=None, timeout=None):
        seen["json"] = json
        return FakeResponse(200, gemini_ok_body())

    monkeypatch.setattr(gem_mod.httpx, "post", fake_post)
    GeminiProvider("K", "gemini-x").complete_json("SYSTEM-PROMPT", "USER-DATA")
    body = seen["json"]
    assert body["systemInstruction"]["parts"][0]["text"] == "SYSTEM-PROMPT"
    assert body["contents"][0]["parts"][0]["text"] == "USER-DATA"
    assert body["generationConfig"]["responseMimeType"] == "application/json"
    assert body["generationConfig"]["temperature"] == 0


def test_gemini_non_object_json_rejected(monkeypatch):
    monkeypatch.setattr(gem_mod.httpx, "post",
                        lambda *a, **kw: FakeResponse(200, gemini_ok_body('["list"]')))
    with pytest.raises(LLMError):
        GeminiProvider("K", "m").complete_json("s", "u")


def test_gemini_http_error_maps_to_llm_error(monkeypatch):
    calls = {"n": 0}

    def fake_post(*a, **kw):
        calls["n"] += 1
        return FakeResponse(500)

    monkeypatch.setattr(gem_mod.httpx, "post", fake_post)
    with pytest.raises(LLMError, match="HTTP 500"):
        GeminiProvider("K", "m").complete_json("s", "u")
    assert calls["n"] == 2                 # one retry per max_retries=1


def test_gemini_429_retries_with_backoff(monkeypatch):
    calls = {"n": 0}
    sleeps = []

    def fake_post(*a, **kw):
        calls["n"] += 1
        return FakeResponse(429)

    monkeypatch.setattr(gem_mod.httpx, "post", fake_post)
    monkeypatch.setattr(gem_mod.time, "sleep", lambda s: sleeps.append(s))
    with pytest.raises(LLMError, match="429 rate limit"):
        GeminiProvider("K", "m").complete_json("s", "u")
    # RATE_LIMIT_MAX_ATTEMPTS=4 -> 1 initial + 4 retries = 5 calls
    assert calls["n"] == 5
    assert len(sleeps) == 4                # exponential backoff between them
    assert sleeps == [1.0, 2.0, 4.0, 8.0]  # 1 * 2**(i-1)


def test_groq_429_retries_with_backoff(monkeypatch):
    import app.llm.groq_provider as groq_mod
    calls = {"n": 0}
    sleeps = []

    def fake_post(*a, **kw):
        calls["n"] += 1
        return FakeResponse(429)

    monkeypatch.setattr(groq_mod.httpx, "post", fake_post)
    monkeypatch.setattr(groq_mod.time, "sleep", lambda s: sleeps.append(s))
    with pytest.raises(LLMError, match="429 rate limit"):
        groq_mod.GroqProvider("K", "m").complete_json("s", "u")
    assert calls["n"] == 5
    assert sleeps == [1.0, 2.0, 4.0, 8.0]


# ── Groq Structured Outputs (Phase-9 fix) ────────────────────────────────────
# The configured reasoning model openai/gpt-oss-120b requires json_schema, not
# the legacy json_object JSON mode. The LeadVerdict schema is the source of truth.

def test_groq_sends_json_schema_structured_outputs(monkeypatch):
    import app.llm.groq_provider as groq_mod
    from app.llm.schemas import LeadVerdict
    seen = {}

    def fake_post(url, json=None, headers=None, timeout=None, verify=None):
        seen["json"] = json
        return FakeResponse(200, {"choices": [{"message": {
            "content": '{"decision": "ACCEPT", "confidence": 0.95}'}}]})

    monkeypatch.setattr(groq_mod.httpx, "post", fake_post)
    groq_mod.GroqProvider("K", "openai/gpt-oss-120b").complete_json("s", "u")
    body = seen["json"]

    rf = body["response_format"]
    assert rf["type"] == "json_schema"
    assert rf["json_schema"]["name"] == "LeadVerdict"
    assert rf["json_schema"]["strict"] is False
    # schema is exactly the LeadVerdict Pydantic schema (source of truth)
    assert rf["json_schema"]["schema"] == LeadVerdict.model_json_schema()
    assert "json_object" != rf["type"]
    assert body["temperature"] == 0.6
    assert body["max_tokens"] == 4096


def test_groq_schema_is_source_of_truth_for_leadverdict():
    import app.llm.groq_provider as groq_mod
    from app.llm.schemas import LeadVerdict
    payload = groq_mod.GroqProvider._structured_output_payload()
    schema = payload["json_schema"]["schema"]
    # Every LeadVerdict field is present; contract was not redesigned.
    for field in LeadVerdict.model_fields:
        assert field in schema["properties"], f"missing field in schema: {field}"


# ── Provider selection (verifier._default_provider) ───────────────────────────

@pytest.fixture
def clean_config(monkeypatch):
    import app.config as cfg
    vals = dict(GROQ_API_KEY="", GEMINI_API_KEY="", GEMINI_MODEL="",
                GROQ_MODEL="groq-model", LLM_PROVIDER="auto",
                GROQ_TIMEOUT_S=5.0, GROQ_MAX_RETRIES=1)
    for k, v in vals.items():
        monkeypatch.setattr(cfg, k, v)
    return cfg


def test_selection_auto_prefers_groq(clean_config):
    from app.llm.verifier import Verifier
    clean_config.GROQ_API_KEY = "g"
    clean_config.GEMINI_API_KEY = "m"
    p = Verifier._default_provider()
    assert isinstance(p, GroqProvider)


def test_selection_auto_falls_back_to_gemini(clean_config):
    from app.llm.verifier import Verifier
    clean_config.GEMINI_API_KEY = "m"
    clean_config.GEMINI_MODEL = "gemini-x"
    p = Verifier._default_provider()
    assert isinstance(p, GeminiProvider)
    assert p.model == "gemini-x"


def test_selection_explicit_gemini(clean_config):
    from app.llm.verifier import Verifier
    clean_config.GROQ_API_KEY = "g"
    clean_config.GEMINI_API_KEY = "m"
    clean_config.GEMINI_MODEL = "gemini-x"
    clean_config.LLM_PROVIDER = "gemini"
    assert isinstance(Verifier._default_provider(), GeminiProvider)


def test_selection_no_keys_disables_gate(clean_config):
    from app.llm.verifier import Verifier
    assert Verifier._default_provider() is None


def test_selection_gemini_without_model_disables(clean_config):
    from app.llm.verifier import Verifier
    clean_config.GEMINI_API_KEY = "m"      # key but no model -> unset
    assert Verifier._default_provider() is None
