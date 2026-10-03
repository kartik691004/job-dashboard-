"""Phase 37 recovery shim — materialize the ONE cap-aborted actor run's
dataset as raw_posts.jsonl (free dataset re-read; NO actor call), then hand
control to the phase37 runner's --resume path for the full pipeline.

One-time use for legacy run CIo4E4s4SddaAath2 (charge-capped at the account
allowance boundary before the freeze guard existed). Idempotent by design:
it refuses to overwrite a non-empty raw_posts.jsonl.
"""
import json
import os
import sys
from pathlib import Path

os.environ["DRY_RUN"] = "false"  # in-process production override (run_daily pattern)

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
except Exception:
    pass

from apify_client import ApifyClient  # noqa: E402
from app.config import APIFY_API_TOKEN  # noqa: E402
from app.models import RawPost  # noqa: E402
from app.orchestrator import Orchestrator  # noqa: E402

RUN_ID = "CIo4E4s4SddaAath2"
OUT_DIR = ROOT / "data" / "validation" / "phase37_final_current_key_run"
RAW_POSTS = OUT_DIR / "raw_posts.jsonl"


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    if RAW_POSTS.exists() and RAW_POSTS.read_text(encoding="utf-8").strip():
        print("raw_posts.jsonl already materialized — nothing to do")
        return 0
    source = Orchestrator(production_gate=True).source
    client = ApifyClient(APIFY_API_TOKEN)
    run = client.run(RUN_ID).get()
    ds_id = run.default_dataset_id
    items = list(client.dataset(ds_id).iterate_items())
    posts, bad = [], 0
    for it in items:
        if it.get("error"):
            bad += 1
            continue
        rp = source._to_raw_post(it)
        if rp is None:
            bad += 1
            continue
        d = rp.model_dump() if hasattr(rp, "model_dump") else vars(rp)
        posts.append(d)
    if not posts:
        print("FATAL: no usable posts recovered from dataset", ds_id)
        return 1
    with RAW_POSTS.open("w", encoding="utf-8", newline="\n") as f:
        for d in posts:
            f.write(json.dumps(d, ensure_ascii=False) + "\n")
    print(f"materialized {len(posts)} RawPosts (bad records: {bad}) "
          f"from dataset {ds_id} -> {RAW_POSTS.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
