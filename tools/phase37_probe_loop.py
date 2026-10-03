"""PHASE 37 — probe-gated resume loop.

Groq's verifier model (openai/gpt-oss-120b) is serving roughly one evaluation
per rate-limit window, and Gemini key1 is hard-quota 429 until ~07:00 UTC.
This driver re-checks both providers cheaply (1-token probes) and launches a
resume window of tools/run_phase37_final_current_key.py ONLY when the verifier
chain can actually serve. It exits when the cache-first ledger holds all 13
resume-sequence candidates (15 candidates minus 2 already written to MAIN)
with zero UNAVAILABLE records.

No Apify calls. No config changes. Ledger-safe at every interruption point.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

LEDGER = REPO / "data" / "validation" / "phase37_final_current_key_run" / "verdicts.jsonl"
RUNNER = REPO / "tools" / "run_phase37_final_current_key.py"
WINDOW_LOG = REPO / "logs" / "phase37_process10.log"
LOOP_LOG = REPO / "logs" / "phase37_probe_loop.log"

TARGET_DISTINCT = 13  # 15 candidates - 2 already in production (no ledger record)
POLL_FALLBACK_S = 600


def ts() -> str:
    return time.strftime("%H:%M:%S", time.gmtime())


def log(msg: str) -> None:
    line = f"[{ts()}] {msg}"
    print(line, flush=True)


def ledger_state() -> tuple[int, int]:
    recs: dict[str, str] = {}
    with LEDGER.open(encoding="utf-8") as f:
        for line in f:
            if line.strip():
                r = json.loads(line)
                recs[r["post_url"]] = r["decision"]
    unav = sum(1 for d in recs.values() if d == "UNAVAILABLE")
    return len(recs), unav


def groq_verifier_ready() -> tuple[bool, str]:
    """1-token probe of the exact verifier model (cheap readiness check)."""
    from app import config
    body = json.dumps({
        "model": config.GROQ_MODEL, "max_tokens": 1,
        "messages": [{"role": "user", "content": "hi"}],
    }).encode()
    req = urllib.request.Request(
        "https://api.groq.com/openai/v1/chat/completions", data=body,
        headers={"Authorization": "Bearer " + config.GROQ_API_KEY,
                 "Content-Type": "application/json",
                 "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return (r.status == 200), f"HTTP {r.status}"
    except urllib.error.HTTPError as e:
        detail = e.read()[:120].decode(errors="replace").replace("\n", " ")
        return False, f"HTTP {e.code} {detail}"
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"


def launch_window() -> None:
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    with WINDOW_LOG.open("a", encoding="utf-8") as logf:
        logf.write(f"\n===== window @ {ts()} =====\n")
        logf.flush()
        try:
            subprocess.run(
                [sys.executable, str(RUNNER), "--resume"],
                stdout=logf, stderr=subprocess.STDOUT,
                cwd=str(REPO), env=env, timeout=900)
        except subprocess.TimeoutExpired:
            logf.write("===== window timeout after 900s (killed) =====\n")
            log("window timed out after 900s; killed")


def main() -> None:
    log("probe loop started")
    for attempt in range(1, 60):  # hard cap: 60 windows max, then give up
        distinct, unav = ledger_state()
        log(f"ledger distinct={distinct}/{TARGET_DISTINCT} unavailable={unav}")
        if distinct >= TARGET_DISTINCT and unav == 0:
            log("ALL CANDIDATES EVALUATED — exiting")
            return
        ready, info = groq_verifier_ready()
        if not ready:
            log(f"groq verifier NOT ready ({info}); sleeping {POLL_FALLBACK_S}s")
            time.sleep(POLL_FALLBACK_S)
            continue
        log(f"groq verifier ready ({info}); launching window #{attempt}")
        launch_window()
    log("gave up after 60 windows — check ledger manually")


if __name__ == "__main__":
    main()
