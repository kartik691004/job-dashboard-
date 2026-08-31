"""
base.py — provider-agnostic LLM contract.

The pipeline depends on `LLMProvider`, never on Groq directly, so the provider
can be replaced (OpenAI, local model, ...) without touching orchestrator or
verifier. A provider's ONLY job is: system+user prompt in, parsed JSON dict out.
All semantic validation happens in app.llm.verifier, all transport errors are
surfaced as LLMError so the pipeline can degrade to REVIEW instead of crashing.
"""
from typing import Any, Dict, Protocol, runtime_checkable


class LLMError(Exception):
    """Raised when the provider cannot return a usable JSON object."""


@runtime_checkable
class LLMProvider(Protocol):
    def complete_json(self, system: str, user: str) -> Dict[str, Any]:
        """Send one chat completion and return the parsed JSON object.

        Implementations MUST raise LLMError on: HTTP/network failure, timeout,
        non-JSON body, or an API-level refusal. They must never leak the API
        key into exception messages or logs.
        """
        ...
