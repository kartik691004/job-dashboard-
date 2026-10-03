"""
explabs_provider.py — Experiential Labs transport for the LLMProvider contract.

Uses httpx (already a project dependency) against Experiential Labs's OpenAI-compatible
chat/completions endpoint with Structured Outputs (json_schema). Transport
concerns only: retries, timeouts, JSON parsing. Semantics live in app.llm.verifier.
"""
import json
import time
from typing import Any, Dict

import httpx

from app.llm.base import LLMError
from app.llm.schemas import LeadVerdict

EXPLABS_URL = "https://api.experientiallabs.ai/v1/chat/completions"

RATE_LIMIT_MAX_ATTEMPTS = 4
RATE_LIMIT_BACKOFF_S = 1.0
TRANSIENT_RETRY_DELAY_S = 1.0


class ExplabsProvider:
    def __init__(self, api_key: str, model: str, timeout_s: float = 30.0,
                 max_retries: int = 1):
        if not api_key:
            raise LLMError("Experiential Labs API key is not configured")
        self._api_key = api_key
        self.model = model
        self.timeout_s = timeout_s
        self.max_retries = max(0, max_retries)

    @staticmethod
    def _backoff(resp, attempt: int) -> None:
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
            "temperature": 0.6,
            "max_tokens": 4096,
            "response_format": self._structured_output_payload(),
        }
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

        last_error: Exception = LLMError("Experiential Labs call did not run")
        transient_attempts = 0
        rate_attempts = 0
        rate_exhausted = False
        while True:
            try:
                resp = httpx.post(EXPLABS_URL, json=payload, headers=headers,
                                  timeout=self.timeout_s)
                if resp.status_code == 429:
                    rate_attempts += 1
                    if rate_attempts > RATE_LIMIT_MAX_ATTEMPTS:
                        rate_exhausted = True
                        break
                    self._backoff(resp, rate_attempts)
                    continue
                if resp.status_code != 200:
                    transient_attempts += 1
                    if transient_attempts > self.max_retries:
                        raise LLMError(f"Experiential Labs HTTP {resp.status_code}")
                    time.sleep(TRANSIENT_RETRY_DELAY_S)
                    continue
                content = resp.json()["choices"][0]["message"]["content"]
                parsed = json.loads(content)
                if not isinstance(parsed, dict):
                    raise LLMError("Experiential Labs returned JSON that is not an object")
                return parsed
            except (LLMError, httpx.HTTPError, KeyError, IndexError,
                    TypeError, ValueError) as e:
                last_error = e
                transient_attempts += 1
                if transient_attempts > self.max_retries:
                    break
                time.sleep(TRANSIENT_RETRY_DELAY_S)
        if rate_exhausted:
            raise LLMError("Experiential Labs HTTP 429 rate limit exceeded after retries")
        raise LLMError(f"Experiential Labs call failed after {transient_attempts} attempt(s): {last_error}")
