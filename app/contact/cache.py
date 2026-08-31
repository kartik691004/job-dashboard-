"""
contact/cache.py — verified-contact discovery cache (Phase 8).

Prevents repeated lookups for the same company (perf/cost: "Do NOT perform
external searches for every scraped post... reuse the cached result"). Holds
ContactEvidence keyed by a normalised company name with a TTL.

Optional JSON file persistence lets a scheduled pipeline reuse yesterday's
discoveries across runs (CONTACT_CACHE_PATH). The cache is a pure store — it
never fabricates, and writes only what a provider actually discovered.
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Dict, Optional, Tuple

from app.contact.schemas import ContactEvidence

DEFAULT_TTL_S = 7 * 24 * 3600  # 7 days


def cache_key(company: str) -> str:
    return re.sub(r"\s+", " ", (company or "").strip().lower())


class ContactDiscoveryCache:
    """In-memory (+ optional JSON) cache of ContactEvidence per company."""

    def __init__(self, ttl_s: float = DEFAULT_TTL_S, path: str = ""):
        self._ttl = float(ttl_s)
        self._path = path or ""
        self._store: Dict[str, Tuple[float, ContactEvidence]] = {}
        self.hits = 0
        self.misses = 0
        self._load()

    # ── persistence ──────────────────────────────────────────────────────────
    def _load(self) -> None:
        if not self._path:
            return
        p = Path(self._path)
        if not p.exists():
            return
        try:
            raw = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return
        now = time.time()
        for k, entry in (raw or {}).items():
            try:
                expires = float(entry.get("expires", 0.0))
                ev = ContactEvidence(**entry.get("evidence", {}))
            except Exception:
                continue  # tolerate a corrupt entry
            if expires and expires <= now:
                continue  # expired on disk
            self._store[k] = (expires, ev)

    def _save(self) -> None:
        if not self._path:
            return
        try:
            p = Path(self._path)
            p.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                k: {"expires": exp, "evidence": ev.model_dump()}
                for k, (exp, ev) in self._store.items()
            }
            p.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        except Exception:
            pass  # a cache write failure must never break the pipeline

    # ── API ──────────────────────────────────────────────────────────────────
    def get(self, company: str) -> Optional[ContactEvidence]:
        key = cache_key(company)
        entry = self._store.get(key)
        now = time.time()
        if entry is not None and entry[0] and entry[0] <= now:
            self._store.pop(key, None)
            entry = None
        if entry is None:
            self.misses += 1
            return None
        self.hits += 1
        return entry[1].model_copy(deep=True)

    def set(self, company: str, evidence: ContactEvidence) -> ContactEvidence:
        key = cache_key(company)
        expires = time.time() + self._ttl if self._ttl > 0 else 0.0
        self._store[key] = (expires, evidence)
        self._save()
        return evidence

    def keys(self) -> list:
        return sorted(self._store.keys())

    def clear(self) -> None:
        self._store.clear()
        self.hits = 0
        self.misses = 0