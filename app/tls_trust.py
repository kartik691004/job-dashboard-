"""
tls_trust.py — Phase 25.6A enterprise-TLS trust for the Google Sheets stack.

Same principle as the Groq transport (`app/llm/groq_provider.py::_ssl_context`,
SYSTEM TRUST -> DEFAULT FALLBACK): verification is ALWAYS on — this only
selects *which* verifier builds the chain.

Problem: this machine sits behind a Sophos TLS-inspecting gateway whose CA
lives in the Windows OS store but NOT in Mozilla's bundle, so the Google
client chain (gspread -> google-auth -> requests -> urllib3 -> OpenSSL +
certifi) fails. Additionally, OpenSSL 3.x strictly rejects the gateway CA
itself ("Basic Constraints ... not marked critical"), so merely appending
the CA to a bundle file can NEVER work here — chain building must go through
the OS engine, exactly like the proven Groq path does via `truststore`.

Fix: `truststore.inject_into_ssl()` routes stdlib TLS verification through
the Windows chain engine (which properly trusts the installed enterprise
CA), keeping hostname checks, expiry, and chain validation fully on. It is
applied ONLY when the gateway CA is actually present in the OS store;
everywhere else (and if `truststore` is missing) behaviour is byte-identical
to before. Idempotent per process. Never raises into callers.
"""
from __future__ import annotations

import os
import ssl
import tempfile
import warnings

# Name fragment matching the enterprise TLS-inspection CA already trusted by
# the OS store. Never a secret — CA certificates are public by design.
_GATEWAY_CA_MARKERS = ("sophos",)

_injected = False


def _gateway_ca_present() -> bool:
    """True when the enterprise gateway CA is in the Windows OS store."""
    try:
        stores = []
        for name in ("ROOT", "CA"):
            try:
                stores.extend(ssl.enum_certificates(name))
            except Exception:
                continue
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            from cryptography import x509
            for cert_bytes, _encoding, _trust in stores:
                if not cert_bytes:
                    continue
                try:
                    cert = x509.load_der_x509_certificate(cert_bytes)
                except Exception:
                    continue
                blob = (cert.issuer.rfc4514_string() + " "
                        + cert.subject.rfc4514_string()).lower()
                if any(m in blob for m in _GATEWAY_CA_MARKERS):
                    return True
    except Exception:
        pass
    return False


def ensure_google_trust() -> bool | None:
    """Route stdlib TLS verification through the OS chain engine.

    Returns True when OS-engine verification was enabled, None when nothing
    needed changing. Never raises: any failure leaves default verification
    behaviour untouched (fail-closed downstream as before).
    """
    global _injected
    try:
        if _injected:
            return True
        if not _gateway_ca_present():
            return None  # no enterprise CA: keep default trust as-is
        import truststore
        truststore.inject_into_ssl()
        _injected = True
        return True
    except Exception:
        return None


def _bundle_path() -> str:
    return os.path.join(tempfile.gettempdir(),
                        "jobdash_google_ca_bundle.pem")
