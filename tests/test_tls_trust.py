"""
test_tls_trust.py — Phase 25.6A enterprise-TLS trust (verification always on).

Offline only (no network, no Sheets): pins that ensure_google_trust()
- is a no-op without the gateway CA (default behaviour preserved),
- injects OS-engine verification exactly once when the gateway CA is present,
- never raises (ImportError, backend errors -> None),
- is invoked by SheetsWriter construction.
"""
import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import app.tls_trust as tls


def _reset(monkeypatch):
    monkeypatch.setattr(tls, "_injected", False, raising=True)


def test_no_gateway_ca_is_noop(monkeypatch):
    _reset(monkeypatch)
    monkeypatch.setattr(tls, "_gateway_ca_present", lambda: False, raising=True)
    assert tls.ensure_google_trust() is None
    assert tls._injected is False


def test_gateway_ca_injects_once(monkeypatch):
    _reset(monkeypatch)
    monkeypatch.setattr(tls, "_gateway_ca_present", lambda: True, raising=True)
    fake = MagicMock()
    monkeypatch.setitem(sys.modules, "truststore", fake)
    assert tls.ensure_google_trust() is True
    assert tls.ensure_google_trust() is True  # idempotent
    assert fake.inject_into_ssl.call_count == 1


def test_missing_truststore_is_noop(monkeypatch):
    _reset(monkeypatch)
    monkeypatch.setattr(tls, "_gateway_ca_present", lambda: True, raising=True)
    monkeypatch.setitem(sys.modules, "truststore", None)
    assert tls.ensure_google_trust() is None
    assert tls._injected is False


def test_backend_error_never_raises(monkeypatch):
    _reset(monkeypatch)
    monkeypatch.setattr(tls, "_gateway_ca_present", lambda: True, raising=True)
    fake = MagicMock()
    fake.inject_into_ssl.side_effect = RuntimeError("backend down")
    monkeypatch.setitem(sys.modules, "truststore", fake)
    assert tls.ensure_google_trust() is None


def test_detector_never_raises(monkeypatch):
    monkeypatch.setattr(tls.ssl, "enum_certificates",
                        MagicMock(side_effect=OSError("no store")),
                        raising=True)
    assert tls._gateway_ca_present() is False


def test_sheets_writer_invokes_trust(monkeypatch):
    from app.sheets_writer import SheetsWriter
    called = []
    monkeypatch.setattr(tls, "ensure_google_trust",
                        lambda: called.append(1) or True, raising=True)
    w = SheetsWriter.__new__(SheetsWriter)
    # Nonexistent creds: forces dry mode, zero network.
    w.__init__("definitely_missing_creds.json", "mock", "mock", True)
    assert called == [1]
    assert w.dry_run is True
