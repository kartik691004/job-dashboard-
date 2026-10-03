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
from typing import Any, Dict, List, Union

import httpx

from app.llm.base import LLMError

GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

# Rate-limit (429) gets its own larger budget with exponential backoff.
RATE_LIMIT_MAX_ATTEMPTS = 4
RATE_LIMIT_BACKOFF_S = 1.0
TRANSIENT_RETRY_DELAY_S = 1.0


def collect_gemini_keys(*values: Any) -> List[str]:
    """Collect ordered unique Gemini API keys from str or list values.

    Pure helper: accepts individual key strings and/or comma-separated /
    list values, strips whitespace, drops empties, dedupes preserving
    first-seen order. Never logs or raises on key material.
    """
    keys: List[str] = []
    for value in values:
        if not value:
            continue
        items = value if isinstance(value, (list, tuple)) else str(value).split(",")
        for item in items:
            key = item.strip() if isinstance(item, str) else ""
            if key and key not in keys:
                keys.append(key)
    return keys


class GeminiProvider:
    def __init__(self, api_key: Union[str, List[str]], model: str,
                 timeout_s: float = 30.0, max_retries: int = 1):
        keys = collect_gemini_keys(api_key)
        if not keys:
            raise LLMError("Gemini API key is not configured")
        self._api_keys = keys
        self._api_key = keys[0]  # primary key (backward-compatible accessor)
        self.model = model
        self.timeout_s = timeout_s
        self.max_retries = max(0, max_retries)

    @property
    def api_key_count(self) -> int:
        """Number of configured Gemini keys available for rotation."""
        return len(self._api_keys)

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
        """Structured call with in-provider key rotation.

        Keys are tried in configured order. A key whose retry budget is
        exhausted by 429s, or which is rejected with 401/403, yields to the
        next key (bounded: one pass over the key list, existing per-key
        budgets unchanged). Any other failure raises immediately. With a
        single key the behaviour is byte-identical to the previous version.
        Key material never appears in errors or logs.
        """
        last_error: Exception = LLMError("Gemini call did not run")
        for index, key in enumerate(self._api_keys):
            try:
                return self._complete_with_key(key, system, user)
            except LLMError as e:
                last_error = e
                message = str(e)
                rotatable = ("429" in message
                             or "rate limit" in message.lower()
                             or "HTTP 401" in message
                             or "HTTP 403" in message)
                if rotatable and index + 1 < len(self._api_keys):
                    continue
                raise
        raise last_error

    def _complete_with_key(self, api_key: str, system: str,
                           user: str) -> Dict[str, Any]:
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
        headers = {"x-goog-api-key": api_key,
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
                    # Guard: never echo any API key back in the error text.
                    if any(k and k in reason for k in self._api_keys):
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
