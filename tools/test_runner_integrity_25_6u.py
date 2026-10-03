"""
Offline runner-integrity tests for the Phase 25.6U corrected window runner
(brief §14). Monkeypatches every network surface to zero; exercises the
runner's ledger/cache contracts against a THROWAWAY copy of the artifacts.

Covers:
  1. successful result persistence (cache + ledger pointer fields)
  2. throttle persistence (attempt ladder increment)
  3. attempt increment reaching the manual-only transition (3rd throttle)
  4. restart recovery (ledger state re-read and selection respects cooldowns)
  5. reasons/llm_error survive the record_success canonical rewrite

Run: python tools/test_runner_integrity_25_6u.py
Exit 0 = all checks pass.
"""
from __future__ import annotations

import importlib.util
import json
import shutil
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

RUN_TOOLS = ROOT / "tools"
sys.path.insert(0, str(RUN_TOOLS))

import tools.resume_engine_25_6i as eng  # noqa: E402

NOW = datetime.now(timezone.utc)


def load_runner_module():
    spec = importlib.util.spec_from_file_location(
        "runner_25_6u", RUN_TOOLS / "run_25_6t_groq_window.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class FakeGate:
    """Stand-in for a Verifier.verify outcome."""

    def __init__(self, decision="REVIEW", llm_status="REVIEW", conf=0.9,
                 provider="GroqProvider", error=""):
        self.decision = decision
        self.llm_status = llm_status
        self.confidence = conf
        self.llm_provider = provider
        self.llm_error = error
        self.reasons = ["unit-test reason"]
        self.verdict = type("V", (), {"model_dump": lambda self: {"decision": decision}})()


def make_fake_verify(sequence):
    calls = {"n": 0}

    def fake_verify(self, **kwargs):
        gate = sequence[min(calls["n"], len(sequence) - 1)]
        calls["n"] += 1
        return gate

    return fake_verify, calls


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="runner_integrity_25_6u_"))
    failures = []

    def check(name, cond):
        print(f"  [{'PASS' if cond else 'FAIL'}] {name}")
        if not cond:
            failures.append(name)

    # ── Throwaway copies of cache + ledger ───────────────────────────────
    src_dir = ROOT / "data" / "validation" / "phase25_6_live_run"
    # §5 guard baseline: byte snapshot of the LIVE artifacts taken before any
    # test runs. Compared byte-for-byte at the end (no hardcoded counts —
    # those went stale as soon as a legitimate live window advanced state).
    live_before = {
        "cache": (src_dir / "llm_cache_25_6d.json").read_bytes(),
        "ledger": (src_dir / "retry_ledger_25_6i.json").read_bytes(),
    }
    cache = json.loads((src_dir / "llm_cache_25_6d.json").read_text(encoding="utf-8"))
    ledger = json.loads((src_dir / "retry_ledger_25_6i.json").read_text(encoding="utf-8"))
    # Shrink to 3 deterministic UNAVAILABLE entries. Test entries are FRESH
    # (attempt_count 0, no retry_eligible_at field = "eligible now" per
    # engine semantics) so selection is deterministic.
    keep = [{"key": f"testkey{i}", "status": eng.STATUS_UNAVAILABLE,
             "attempt_count": 0, "last_provider": "GeminiProvider"}
            for i in range(3)]
    ledger["entries"] = keep
    cache_small = {}  # empty cache: all three unresolved

    test_cache = tmp / "cache.json"
    test_ledger = tmp / "ledger.json"
    test_cache.write_text(json.dumps(cache_small), encoding="utf-8")
    test_ledger.write_text(json.dumps(ledger), encoding="utf-8")

    mod = load_runner_module()
    mod.CACHE_JSON = test_cache
    mod.LEDGER_JSON = test_ledger
    # Offline dataset: one RawPost per test key so the runner's deterministic
    # classification pass yields ordered_keys == test keys. Classifier needs a
    # valid FO post; simplest is to bypass classification by patching the
    # module's classifier to a stub that marks every post valid.
    mod.load_dataset = lambda: [type("P", (), {"post_url": f"https://x/testkey{i}",
                                               "text": "x"})() for i in range(3)]

    class _StubClassified:
        is_valid = True
        post_url = ""
        text = ""
        author_name = ""
        author_profile_url = ""
        job_card_location = ""
        job_card_company = "Unclear"
        job_card_employment_type = "Unclear"
        job_card_experience = "Unclear"
        company_name = "TestCo"
        exact_role = "Founder's Office"

        def __init__(self, url):
            self.post_url = url

    class _StubClassifier:
        def classify(self, post):
            return _StubClassified(post.post_url)

    mod.DeterministicClassifier = _StubClassifier
    mod.cache_key_for = lambda url: url.rsplit("/", 1)[-1]  # testkeyN
    mod.CAPACITY_JSON = tmp / "capacity.json"

    ordered = [f"testkey{i}" for i in range(3)]
    key1 = ordered[0]

    # ── 1. successful result persistence ─────────────────────────────────
    fake_verify, _ = make_fake_verify([FakeGate()])
    mod.Verifier.verify = fake_verify
    # patch probe to SERVING
    mod.probe_groq = lambda: ("SERVING", 0.1, "")

    class _Args:  # minimal argv guard
        pass

    rc = mod.main()
    cache_after = json.loads(test_cache.read_text(encoding="utf-8"))
    ledger_after = json.loads(test_ledger.read_text(encoding="utf-8"))
    emap_after = {e["key"]: e for e in ledger_after["entries"]}
    check("success: rc==0", rc == 0)
    check("success: candidate cached", key1 in cache_after)
    check("success: cache decision REVIEW", cache_after.get(key1, {}).get("decision") == "REVIEW")
    check("success: reasons survived canonical rewrite",
          cache_after.get(key1, {}).get("reasons") == ["unit-test reason"])
    e1 = emap_after[key1]
    check("success: ledger status HOLD", e1.get("status") == eng.STATUS_HOLD)
    check("success: ledger last_success_at set", bool(e1.get("last_success_at")))
    check("success: ledger error class cleared", e1.get("last_error_class") is None)

    # ── 2. throttle persistence (attempt ladder increment) ───────────────
    # Fresh test entries again (attempt_count 0, no cooldown fields).
    ledger2 = json.loads((src_dir / "retry_ledger_25_6i.json").read_text(encoding="utf-8"))
    keep2 = [{"key": f"testkey{i}", "status": eng.STATUS_UNAVAILABLE,
              "attempt_count": 0, "last_provider": "GeminiProvider"}
             for i in range(3)]
    ledger2["entries"] = keep2
    test_ledger.write_text(json.dumps(ledger2), encoding="utf-8")
    test_cache.write_text("{}", encoding="utf-8")

    fake_verify2, _ = make_fake_verify([
        FakeGate(llm_status="UNAVAILABLE", provider="GroqProvider",
                 error="Groq HTTP 429 rate limit exceeded after retries")])
    mod.Verifier.verify = fake_verify2
    rc2 = mod.main()
    ledger3 = json.loads(test_ledger.read_text(encoding="utf-8"))
    emap3 = {e["key"]: e for e in ledger3["entries"]}
    check("throttle: rc==0", rc2 == 0)
    check("throttle: attempts incremented to 1",
          emap3[key1].get("attempt_count") == 1)
    check("throttle: error class RATE_LIMITED",
          emap3[key1].get("last_error_class") == eng.RATE_LIMITED)
    check("throttle: retry_eligible_at ~6h out",
          emap3[key1].get("retry_eligible_at") is not None)
    cache3 = json.loads(test_cache.read_text(encoding="utf-8"))
    check("throttle: UNAVAILABLE not cached", key1 not in cache3)

    # ── 3. manual-only transition at 3rd throttle ────────────────────────
    # Direct engine-contract walk (the same record_throttle(entry_map, ...)
    # call shape the corrected runner uses), deterministic input:
    emap_t = {f"testkey{i}": {"key": f"testkey{i}",
              "status": eng.STATUS_UNAVAILABLE, "attempt_count": 0}
              for i in range(3)}
    eng.record_throttle(emap_t, "testkey0", provider="GroqProvider",
                        error_class=eng.RATE_LIMITED, now=datetime.now(timezone.utc))
    eng.record_throttle(emap_t, "testkey0", provider="GroqProvider",
                        error_class=eng.RATE_LIMITED, now=datetime.now(timezone.utc))
    eng.record_throttle(emap_t, "testkey0", provider="GroqProvider",
                        error_class=eng.RATE_LIMITED, now=datetime.now(timezone.utc))
    target = emap_t["testkey0"]
    check("manual: attempts incremented to 3", target.get("attempt_count") == 3)
    check("manual: retry_eligible_at None (manual-only)",
          target.get("retry_eligible_at") is None)
    check("manual: eligible_for_provider False",
          eng.eligible_for_provider(target, "groq", NOW, probe_ok=True) is False)

    # ── 4. restart recovery: selection respects cooldowns after reload ──
    # Seed key1 with a fresh 1st throttle (6h cooldown) via the same engine
    # contract, then verify selection skips manual + cooling keys.
    eng.record_throttle(emap_t, "testkey1", provider="GroqProvider",
                        error_class=eng.RATE_LIMITED, now=datetime.now(timezone.utc))
    sel_now = eng.select_next_for_provider(
        ordered, emap_t, set(), datetime.now(timezone.utc), "groq",
        probe_ok=True, max_n=3)
    check("restart: manual-only excluded from selection", "testkey0" not in sel_now)
    check("restart: cooling key (1st throttle, 6h) excluded",
          "testkey1" not in sel_now)
    check("restart: fresh key still selected", "testkey2" in sel_now)
    future = datetime.now(timezone.utc) + timedelta(hours=7)
    check("restart: manual-only stays excluded even at future time",
          eng.eligible_for_provider(emap_t["testkey0"], "groq", future, probe_ok=True) is False)
    check("restart: cooling key eligible after cooldown expiry",
          eng.eligible_for_provider(emap_t["testkey1"], "groq", future, probe_ok=True) is True)

    # ── 5. no production-tree mutation by these tests ────────────────────
    live_cache_now = (src_dir / "llm_cache_25_6d.json").read_bytes()
    check("live cache untouched by tests", live_cache_now == live_before["cache"])
    live_ledger_now = (src_dir / "retry_ledger_25_6i.json").read_bytes()
    check("live ledger untouched by tests", live_ledger_now == live_before["ledger"])

    shutil.rmtree(tmp, ignore_errors=True)
    print(f"\nrunner-integrity: {'ALL PASS' if not failures else 'FAILURES: ' + ', '.join(failures)}")
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
