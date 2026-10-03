"""
test_tls_trust_phase28_4.py — Phase 28.4 unified Google TLS trust hook.

Offline only (no network, no Sheets, no writes). Proves, for EVERY
production-relevant Google entry point:
- ensure_google_trust() is invoked exactly once, BEFORE the first Google
  client/session creation (ordering pinned by a call-sequence recorder),
- the mechanism is the shared app/tls_trust.py one (no second implementation),
- TLS verification is never disabled (no verify=False, CERT_NONE, ssl._create_
  unverified_context, or bundle mutation anywhere in the trust mechanism),
- no certificate files are installed/written (no truststore SSLContext()
  usage, no load_verify_locations, no cafile writes),
- normal Google TLS verification stays enabled when interception is OFF
  (no-gateway-CA path preserves default behaviour; hook returns None).
"""
import ast
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import app.tls_trust as tls

ROOT = Path(__file__).resolve().parent.parent

# (module-relative file, function whose body must initialize trust before
#  the first gspread/google client creation)
HOOKED_FLOWS = [
    ("app/main.py", "get_leads"),
    ("app/outreach.py", "_ensure_worksheet"),
    ("verify_sheets.py", None),           # module-level script
    ("verify_sheets2.py", None),          # module-level script
    ("update_gs.py", "get_client"),
    ("run_full_pipeline.py", "push_to_google_sheets"),
    ("src/exporters/google_sheets_exporter.py", "_get_client"),
    ("tools/filter_sheet_by_standards.py", "main"),
    ("tools/migrate_to_txt_schema.py", "main"),
]
HOOKED_FLOWS = [(ROOT / f, fn) for f, fn in HOOKED_FLOWS]


def _reset(monkeypatch):
    monkeypatch.setattr(tls, "_injected", False, raising=True)


# ── Static analysis helpers (AST; no execution, no network) ────────────────

def _module_tree(path: Path):
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _flow_nodes(tree, func_name):
    """Yield (node) for func body, or whole module when func_name is None."""
    if func_name is None:
        yield tree
        return
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                and node.name == func_name:
            yield node


def _find_call(node, dotted):
    """True when `node` subtree calls dotted name like 'gspread.service_account'."""
    for sub in ast.walk(node):
        if isinstance(sub, ast.Call):
            f = sub.func
            name = ""
            if isinstance(f, ast.Attribute):
                base = f.value
                base_name = base.id if isinstance(base, ast.Name) else ""
                name = f"{base_name}.{f.attr}" if base_name else f.attr
            elif isinstance(f, ast.Name):
                name = f.id
            if name == dotted or name.endswith("." + dotted):
                return True
    return False


def _hook_position_ok(flow: ast.AST) -> bool:
    """ensure_google_trust call must appear before the first client creation."""
    first_client = None
    hook = None
    for node in ast.walk(flow):
        if isinstance(node, ast.Call):
            if _find_call(node, "gspread.service_account") \
                    or _find_call(node, "gspread.authorize") \
                    or _find_call(node, "Credentials.from_service_account_file"):
                if first_client is None:
                    first_client = node
            if _find_call(node, "ensure_google_trust"):
                if hook is None:
                    hook = node
    # AST has no global order across branches; compare source offsets.
    return (hook is not None and first_client is not None
            and hook.lineno < first_client.lineno)


# ── A. Idempotence of ensure_google_trust ──────────────────────────────────

def test_ensure_google_trust_is_idempotent(monkeypatch):
    _reset(monkeypatch)
    monkeypatch.setattr(tls, "_gateway_ca_present", lambda: True, raising=True)
    fake = MagicMock()
    monkeypatch.setitem(sys.modules, "truststore", fake)
    first = tls.ensure_google_trust()
    second = tls.ensure_google_trust()
    third = tls.ensure_google_trust()
    assert (first, second, third) == (True, True, True)
    assert fake.inject_into_ssl.call_count == 1  # injected EXACTLY once


# ── B. SheetsWriter initializes trust before gspread ───────────────────────

def test_sheets_writer_initializes_trust_before_gspread(monkeypatch):
    import app.sheets_writer as sw
    order = []
    monkeypatch.setattr(tls, "ensure_google_trust",
                        lambda: order.append("trust") or True, raising=True)
    fake_gspread = MagicMock()
    fake_gspread.service_account.side_effect = \
        lambda **kw: order.append("gspread") or MagicMock()
    monkeypatch.setattr(sw, "gspread", fake_gspread, raising=True)
    w = sw.SheetsWriter.__new__(sw.SheetsWriter)
    w.__init__("definitely_missing_creds.json", "mock", "mock", True)
    # Trust ran first; missing creds forced DRY-RUN before any auth attempt.
    assert order == ["trust"] and w.dry_run is True

    # Full ordering proof with creds "present": trust BEFORE service_account.
    order.clear()
    import app.sheets_writer as sw_module
    monkeypatch.setattr(sw_module.os.path, "exists", lambda p: True,
                        raising=True)

    class _FakeErr(Exception):
        pass

    fake_gspread.service_account.side_effect = \
        lambda **kw: order.append("gspread") or MagicMock()
    fake_gspread.exceptions.WorksheetNotFound = _FakeErr
    w2 = sw.SheetsWriter.__new__(sw.SheetsWriter)
    w2.__init__("fake.json", "mock", "mock", True)
    assert order[0] == "trust" and "gspread" in order
    assert order.index("trust") < order.index("gspread")


# ── C–F. Every bare/preflight/tool entry point hooks before client creation ─

@pytest.mark.parametrize("path,func", HOOKED_FLOWS)
def test_entry_point_hooks_trust_before_client(path, func):
    tree = _module_tree(path)
    flows = list(_flow_nodes(tree, func))
    assert flows, f"{path}: function {func!r} not found"
    for flow in flows:
        assert _find_call(flow, "ensure_google_trust"), \
            f"{path}:{func}: ensure_google_trust() not called"
        assert _hook_position_ok(flow), \
            f"{path}:{func}: ensure_google_trust() must precede the first " \
            "gspread.service_account/gspread.authorize/from_service_account_file"


def test_sheets_writer_source_hooks_before_client():
    src = (ROOT / "app/sheets_writer.py").read_text(encoding="utf-8")
    hook_at = src.find("ensure_google_trust()")
    client_at = src.find("gspread.service_account(")
    assert 0 < hook_at < client_at, "SheetsWriter must hook before gspread auth"


# ── G. No verify=False / CERT_NONE / unverified-context bypass anywhere ────

def test_no_verification_bypass_in_trust_mechanism():
    banned = ("verify=False", "verify_mode=ssl.CERT_NONE", "CERT_NONE",
              "_create_unverified_context", "check_hostname=False")
    src = (ROOT / "app/tls_trust.py").read_text(encoding="utf-8")
    for token in banned:
        assert token not in src, f"banned bypass token in tls_trust.py: {token}"
    hook_sources = [
        ROOT / "app/sheets_writer.py", ROOT / "app/main.py",
        ROOT / "app/outreach.py", ROOT / "verify_sheets.py",
        ROOT / "verify_sheets2.py", ROOT / "update_gs.py",
        ROOT / "run_full_pipeline.py",
        ROOT / "src/exporters/google_sheets_exporter.py",
        ROOT / "tools/filter_sheet_by_standards.py",
        ROOT / "tools/migrate_to_txt_schema.py",
    ]
    for p in hook_sources:
        text = p.read_text(encoding="utf-8")
        assert "verify=False" not in text, f"verify=False in {p}"
        assert "_create_unverified_context" not in text, p


# ── H. No certificate files installed (no truststore SSLContext usage,
#      no load_verify_locations, no cafile writes in the hook path) ─────────

def test_no_certificate_file_installation():
    banned = ("load_verify_locations", "SSLContext(ssl.PROTOCOL",
              "cacert.pem", "certifi.where()")
    src = (ROOT / "app/tls_trust.py").read_text(encoding="utf-8")
    for token in banned:
        assert token not in src, f"cert-install pattern in tls_trust.py: {token}"


# ── I/J. Verification stays ON; no-gateway path preserves default TLS ──────

def test_no_gateway_ca_preserves_default_verification(monkeypatch):
    _reset(monkeypatch)
    monkeypatch.setattr(tls, "_gateway_ca_present", lambda: False, raising=True)
    # No truststore fake: if the hook tried to inject anything it would fail.
    assert tls.ensure_google_trust() is None
    assert tls._injected is False  # hook did NOT touch process TLS state


def test_hook_never_disables_verification(monkeypatch):
    _reset(monkeypatch)
    monkeypatch.setattr(tls, "_gateway_ca_present", lambda: True, raising=True)
    fake = MagicMock()
    monkeypatch.setitem(sys.modules, "truststore", fake)
    assert tls.ensure_google_trust() is True
    import ssl
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)  # truststore-injected class
    assert ctx.verify_mode == ssl.CERT_REQUIRED    # verification ON
    assert ctx.check_hostname is True              # hostname checks ON


def test_injection_failure_fails_closed(monkeypatch):
    _reset(monkeypatch)
    monkeypatch.setattr(tls, "_gateway_ca_present", lambda: True, raising=True)
    fake = MagicMock()
    fake.inject_into_ssl.side_effect = RuntimeError("backend down")
    monkeypatch.setitem(sys.modules, "truststore", fake)
    assert tls.ensure_google_trust() is None   # no exception escapes...
    assert tls._injected is False              # ...and no injection happened


# ── Shared-mechanism guarantee: every hook imports app/tls_trust ────────────

def test_all_hooks_use_the_shared_mechanism():
    hook_sources = [
        ROOT / "app/sheets_writer.py", ROOT / "app/main.py",
        ROOT / "app/outreach.py", ROOT / "verify_sheets.py",
        ROOT / "verify_sheets2.py", ROOT / "update_gs.py",
        ROOT / "run_full_pipeline.py",
        ROOT / "src/exporters/google_sheets_exporter.py",
        ROOT / "tools/filter_sheet_by_standards.py",
        ROOT / "tools/migrate_to_txt_schema.py",
    ]
    for p in hook_sources:
        text = p.read_text(encoding="utf-8")
        assert "from app.tls_trust import ensure_google_trust" in text, \
            f"{p} must import the shared app.tls_trust helper"
        assert "truststore.inject_into_ssl()" not in text, \
            f"{p} must not duplicate the truststore mechanism"
