"""
test_gemini_key_rotation.py — multi-key Gemini rotation (offline only).

No network, no live keys. httpx.post is faked; key material is synthetic.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import app.llm.gemini_provider as gem_mod
from app.llm.base import LLMError
from app.llm.gemini_provider import GeminiProvider, collect_gemini_keys


class FakeResponse:
    def __init__(self, status, body=None):
        self.status_code = status
        self.headers = {}
        self._body = body if body is not None else {"candidates": [
            {"content": {"parts": [{"text": '{"decision": "REVIEW"}'}]}}]}

    def json(self):
        if isinstance(self._body, dict):
            return self._body
        raise ValueError("no json")


def test_collect_keys_order_dedupe():
    assert collect_gemini_keys("a", "b", "b, c,,a") == ["a", "b", "c"]
    assert collect_gemini_keys("", None, []) == []
    assert collect_gemini_keys(["x", "y"]) == ["x", "y"]


def test_empty_keys_rejected():
    with pytest.raises(LLMError):
        GeminiProvider("", "m")
    with pytest.raises(LLMError):
        GeminiProvider([], "m")


def test_single_key_uses_first_key_header(monkeypatch):
    seen = {}
    monkeypatch.setattr(gem_mod.httpx, "post",
                        lambda url, json=None, headers=None, timeout=None:
                        (seen.update(headers=headers),
                         FakeResponse(200))[1])
    GeminiProvider("K1", "m").complete_json("s", "u")
    assert seen["headers"]["x-goog-api-key"] == "K1"


def test_rotation_on_429_to_second_key(monkeypatch):
    calls = []

    def fake_post(url, json=None, headers=None, timeout=None):
        calls.append(headers["x-goog-api-key"])
        if headers["x-goog-api-key"] == "K1":
            return FakeResponse(429)
        return FakeResponse(200)

    monkeypatch.setattr(gem_mod.httpx, "post", fake_post)
    monkeypatch.setattr(gem_mod, "RATE_LIMIT_MAX_ATTEMPTS", 0)
    monkeypatch.setattr(gem_mod, "TRANSIENT_RETRY_DELAY_S", 0)
    out = GeminiProvider(["K1", "K2"], "m", max_retries=0).complete_json("s", "u")
    assert out == {"decision": "REVIEW"}
    assert calls[0] == "K1" and calls[-1] == "K2"
    assert "K2" in calls


def test_both_keys_429_raises_without_third_key(monkeypatch):
    monkeypatch.setattr(gem_mod.httpx, "post",
                        lambda *a, **kw: FakeResponse(429))
    monkeypatch.setattr(gem_mod, "RATE_LIMIT_MAX_ATTEMPTS", 0)
    monkeypatch.setattr(gem_mod, "TRANSIENT_RETRY_DELAY_S", 0)
    with pytest.raises(LLMError, match="429"):
        GeminiProvider(["K1", "K2"], "m", max_retries=0).complete_json("s", "u")


def test_non_rotatable_error_does_not_try_next_key(monkeypatch):
    calls = []

    def fake_post(*a, **kw):
        calls.append(1)
        return FakeResponse(500)

    monkeypatch.setattr(gem_mod.httpx, "post", fake_post)
    with pytest.raises(LLMError, match="HTTP 500"):
        GeminiProvider(["K1", "K2"], "m", max_retries=0).complete_json("s", "u")
    assert len(calls) == 1


def test_keys_never_leak_into_errors(monkeypatch):
    monkeypatch.setattr(gem_mod.httpx, "post",
                        lambda *a, **kw: FakeResponse(500))
    with pytest.raises(LLMError) as e:
        GeminiProvider(["SECRET-A", "SECRET-B"], "m",
                       max_retries=0).complete_json("s", "u")
    assert "SECRET-A" not in str(e.value)
    assert "SECRET-B" not in str(e.value)
