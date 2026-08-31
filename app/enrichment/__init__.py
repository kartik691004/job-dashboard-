"""
app.enrichment — Phase 6 LEAD ENRICHMENT layer.

Runs after the existing verification gates (ACCEPT). It makes an already-valid
lead more complete and actionable without ever fabricating information or
changing the vacancy decision. See enricher.py for the orchestrator and
schemas.py for the enriched data contract.
"""
from app.enrichment.enricher import Enricher
from app.enrichment.schemas import (
    EnrichedLead,
    EnrichmentStatus,
    EmailStatus,
)

__all__ = [
    "Enricher",
    "EnrichedLead",
    "EnrichmentStatus",
    "EmailStatus",
]
