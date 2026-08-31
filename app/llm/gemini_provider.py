"""
gemini_provider.py — Google Gemini transport for the LLMProvider contract.

Same semantics as GroqProvider (retry once, timeout, JSON-only output, key
never logged) so app.llm.verifier stays provider-agnostic. Uses plain httpx
against the generativelanguage REST API — no new dependencies.

The API key travels in the x-goog-api-key HEADER (never a query parameter),
so it cannot leak into URLs or access logs.
"""
import json
import time
from typing import Any, Dict

import httpx

from app.llm.base import LLMError

GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

# Rate-limit (429) gets its own larger budget with exponential backoff.
RATE_LIMIT_MAX_ATTEMPTS = 4
RATE_LIMIT_BACKOFF_S = 1.0
TRANSIENT_RETRY_DELAY_S = 1.0


class GeminiProvider:
    def __init__(self, api_key: str, model: str, timeout_s: float = 30.0,
                 max_retries: int = 1):
        if not api_key:
            raise LLMError("Gemini API key is not configured")
        self._api_key = api_key
        self.model = model
        self.timeout_s = timeout_s
        self.max_retries = max(0, max_retries)

    @staticmethod
    def _backoff(resp, attempt: int) -> None:
        """Sleep before retrying a 429. Honors Retry-After; else exponential."""
        retry_after = ""
        try:
            retry_after = (resp.headers or {}).get("retry-after", "")
        except Exception:
            retry_after = ""
        if retry_after and str(retry_after).isdigit():
            delay = float(retry_after)
        else:
            delay = RATE_LIMIT_BACKOFF_S * (2 ** (attempt - 1))
        time.sleep(min(delay, 30.0))

    def complete_json(self, system: str, user: str) -> Dict[str, Any]:
        url = GEMINI_URL.format(model=self.model)
        payload = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": user}]}],
            "generationConfig": {
                "temperature": 0,
                "maxOutputTokens": 1200,
                "responseMimeType": "application/json",
            },
        }
        headers = {"x-goog-api-key": self._api_key,
                   "Content-Type": "application/json"}

        last_error: Exception = LLMError("Gemini call did not run")
        transient_attempts = 0
        rate_attempts = 0
        rate_exhausted = False
        while True:
            try:
                resp = httpx.post(url, json=payload, headers=headers,
                                  timeout=self.timeout_s)
                if resp.status_code == 429:
                    rate_attempts += 1
                    if rate_attempts > RATE_LIMIT_MAX_ATTEMPTS:
                        rate_exhausted = True
                        break
                    self._backoff(resp, rate_attempts)
                    continue
                if resp.status_code != 200:
                    # Surface a short, key-safe error reason for diagnosis.
                    reason = ""
                    try:
                        err = resp.json().get("error", {})
                        reason = str(err.get("message", "") or err.get("status", ""))
                    except Exception:
                        reason = ""
                    # Guard: never echo the API key back in the error text.
                    if self._api_key in reason:
                        reason = "<redacted>"
                    raise LLMError(f"Gemini HTTP {resp.status_code} {reason}")
                body = resp.json()
                parts = body["candidates"][0]["content"]["parts"]
                text = "".join(p.get("text", "") for p in parts)
                parsed = json.loads(text)
                if not isinstance(parsed, dict):
                    raise LLMError("Gemini returned JSON that is not an object")
                return parsed
            except (LLMError, httpx.HTTPError, KeyError, IndexError, TypeError,
                    ValueError) as e:
                last_error = e
                transient_attempts += 1
                if transient_attempts > self.max_retries:
                    break
                time.sleep(TRANSIENT_RETRY_DELAY_S)
        if rate_exhausted:
            raise LLMError("Gemini HTTP 429 rate limit exceeded after retries")
        raise LLMError(f"Gemini call failed after {transient_attempts} attempt(s): {last_error}")
