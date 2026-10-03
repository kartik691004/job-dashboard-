
# ── Phase 25.6O: provider-specific availability ──────────────────────────
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.resume_engine_25_6i import (
    RATE_LIMITED,
    STATUS_HOLD,
    STATUS_UNAVAILABLE,
    eligible_for_provider,
    record_throttle,
    select_next_for_provider,
)

NOW = datetime(2026, 9, 20, 12, 0, 0, tzinfo=timezone.utc)


def _entry(**over):
    base = {"key": "k", "status": STATUS_UNAVAILABLE, "attempt_count": 0}
    base.update(over)
    return base


def test_no_provider_block_without_probe():
    e = _entry(key="a")
    assert eligible_for_provider(e, "groq", NOW, probe_ok=False) is False
    assert eligible_for_provider(e, "gemini", NOW, probe_ok=False) is False


def test_absent_provider_field_means_live_probe_decides():
    e = _entry(key="a", retry_eligible_at=(NOW + timedelta(hours=5)).isoformat())
    # Stale global timestamp does not block a provider with no recorded block.
    assert eligible_for_provider(e, "groq", NOW, probe_ok=True) is True


def test_present_provider_timestamp_blocks_until_expiry():
    e = _entry(key="a",
               next_gemini_retry_at=(NOW + timedelta(hours=5)).isoformat())
    assert eligible_for_provider(e, "gemini", NOW, probe_ok=True) is False
    assert eligible_for_provider(e, "gemini", NOW + timedelta(hours=6),
                                 probe_ok=True) is True


def test_manual_provider_block_stays_blocked():
    e = _entry(key="a", next_groq_retry_at=None)
    assert eligible_for_provider(e, "groq", NOW, probe_ok=True) is False


def test_throttle_blocks_only_failing_provider():
    ledger = {}
    record_throttle(ledger, "k", provider="GeminiProvider",
                    error_class=RATE_LIMITED, now=NOW)
    assert "next_gemini_retry_at" in ledger["k"]
    assert "next_groq_retry_at" not in ledger["k"]
    assert eligible_for_provider(ledger["k"], "groq", NOW, probe_ok=True) is True
    assert eligible_for_provider(ledger["k"], "gemini", NOW, probe_ok=True) is False
    # Legacy global field still maintained for compatibility.
    assert ledger["k"]["retry_eligible_at"] == ledger["k"]["next_gemini_retry_at"]


def test_provider_selection_respects_order_and_cap():
    ordered = [f"k{i}" for i in range(10)]
    ledger = {k: _entry(key=k) for k in ordered}
    picked = select_next_for_provider(ordered, ledger, {"k0"}, NOW, "groq",
                                      probe_ok=True, max_n=3)
    assert picked == ["k1", "k2", "k3"]


def test_provider_selection_skips_evaluated_and_blocked():
    ordered = ["a", "b", "c", "d"]
    ledger = {
        "a": _entry(key="a"),
        "b": _entry(key="b", next_groq_retry_at=(NOW + timedelta(hours=1)).isoformat()),
        "c": _entry(key="c"),
        "d": _entry(key="d", status=STATUS_HOLD),
    }
    picked = select_next_for_provider(ordered, ledger, set(), NOW, "groq",
                                      probe_ok=True, max_n=3)
    assert picked == ["a", "c"]
