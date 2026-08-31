"""LLM bridge for semantic lead verification (Groq today, swappable tomorrow)."""
from app.llm.base import LLMError, LLMProvider
from app.llm.schemas import LeadVerdict
from app.llm.verifier import Verifier

__all__ = ["LLMError", "LLMProvider", "LeadVerdict", "Verifier"]
