"""
groq_provider.py — Groq transport for the LLMProvider contract.

Uses httpx (already a project dependency) against Groq's OpenAI-compatible
chat/completions endpoint with Structured Outputs (json_schema). Transport
concerns only: retries, timeouts, JSON parsing. Semantics live in app.llm.verifier.

Phase-9 audit fix: the configured reasoning model `openai/gpt-oss-120b` requires
`response_format={"type":"json_schema", ...}` (Structured Outputs) — it does NOT
accept the old `{"type":"json_object"}` JSON Object Mode, which surfaced as
HTTP 400 `json_validate_failed`. The LeadVerdict Pydantic schema is the single
source of truth; we send it as best-effort (strict:false) so the existing schema
(optional fields, no additionalProperties:false) is used unchanged and the
verifier's own Pydantic validation remains authoritative + fail-closed. gpt-oss
does not support the `reasoning_format` param and performs best in the 0.5-0.7
temperature window, with reasoning counted against max_tokens.

Security: the API key never appears in exceptions, logs, or repr().
"""
import json
import time
from typing import Any, Dict

import httpx

from app.llm.base import LLMError
from app.llm.schemas import LeadVerdict

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"

# Transient (non-429) failures retry this many times (default 1 -> 2 total tries).
# Rate-limit (429) gets its own larger budget with exponential backoff, because
# Groq's free tier throttles bursts; riding out the window is better than
# degrading to REVIEW.
RATE_LIMIT_MAX_ATTEMPTS = 4
RATE_LIMIT_BACKOFF_S = 1.0
TRANSIENT_RETRY_DELAY_S = 1.0


class GroqProvider:
    def __init__(self, api_key: str, model: str, timeout_s: float = 30.0,
                 max_retries: int = 1):
        if not api_key:
            raise LLMError("Groq API key is not configured")
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

    @staticmethod
    def _structured_output_payload() -> Dict[str, Any]:
        """Build Groq's Structured Outputs response_format from LeadVerdict.

        strict:false = Groq best-effort mode. It accepts the existing schema
        (optional fields, no additionalProperties:false) without redesigning the
        verdict contract; the verifier's Pydantic validation is still the gate.
        gpt-oss models reject the legacy json_object JSON-mode route.
        """
        return {
            "type": "json_schema",
            "json_schema": {
                "name": "LeadVerdict",
                "strict": False,
                "schema": LeadVerdict.model_json_schema(),
            },
        }

    def complete_json(self, system: str, user: str) -> Dict[str, Any]:
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            # gpt-oss (reasoning) docs recommend 0.5-0.7; 0.6 keeps it
            # deterministic-friendly while avoiding temperature-0 instability.
            "temperature": 0.6,
            # Reasoning tokens count against max_tokens; give room so the full
            # schema-shaped JSON is not truncated (finish_reason='length').
            "max_tokens": 4096,
            "response_format": self._structured_output_payload(),
        }
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

        last_error: Exception = LLMError("Groq call did not run")
        transient_attempts = 0
        rate_attempts = 0
        rate_exhausted = False
        while True:
            try:
                resp = httpx.post(GROQ_URL, json=payload, headers=headers,
                                  timeout=self.timeout_s)
                if resp.status_code == 429:
                    rate_attempts += 1
                    if rate_attempts > RATE_LIMIT_MAX_ATTEMPTS:
                        rate_exhausted = True
                        break
                    self._backoff(resp, rate_attempts)
                    continue
                if resp.status_code != 200:
                    # Body may echo request details; log only the status code.
                    transient_attempts += 1
                    if transient_attempts > self.max_retries:
                        raise LLMError(f"Groq HTTP {resp.status_code}")
                    time.sleep(TRANSIENT_RETRY_DELAY_S)
                    continue
                content = resp.json()["choices"][0]["message"]["content"]
                parsed = json.loads(content)
                if not isinstance(parsed, dict):
                    raise LLMError("Groq returned JSON that is not an object")
                return parsed
            except (LLMError, httpx.HTTPError, KeyError, IndexError,
                    TypeError, ValueError) as e:
                last_error = e
                transient_attempts += 1
                if transient_attempts > self.max_retries:
                    break
                time.sleep(TRANSIENT_RETRY_DELAY_S)
        if rate_exhausted:
            raise LLMError("Groq HTTP 429 rate limit exceeded after retries")
        raise LLMError(f"Groq call failed after {transient_attempts} attempt(s): {last_error}")
