#!/usr/bin/env python3
"""
night_run.py -- Unattended batch pipeline runner
Usage:
    python night_run.py                    # research + generate all pending
    python night_run.py --rewrite          # + Claude rewrite all generated
    python night_run.py --rewrite-only     # only rewrite existing drafts
    python night_run.py --model NORMAL     # override model for this run
    python night_run.py --limit 5          # max N articles to process
    python night_run.py --dry-run          # show what would run, no action
"""

import argparse
import os
import sys
import time
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).parent
sys.path.insert(0, str(BASE_DIR))

from core import queue


def _log(msg: str) -> None:
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] {msg}")


def _cumulative_cost() -> float:
    import re
    log_path = BASE_DIR / "output" / "_rewrite_log.txt"
    total = 0.0
    if not log_path.exists():
        return 0.0
    try:
        for line in log_path.read_text(encoding="utf-8").splitlines():
            m = re.search(r"cost=\$([\d.]+)", line)
            if m:
                total += float(m.group(1))
    except Exception:
        pass
    return total


def _cleanup_rss(dry_run: bool = False) -> None:
    from datetime import datetime, timezone, timedelta
    import requests as _rq
    from config import QDRANT_URL as _QURL

    cutoff = (datetime.now(timezone.utc) - timedelta(days=14)).isoformat()
    cols   = ["rss_feed", "rss_feed_pl", "rss_feed_nor"]

    for col in cols:
        try:
            r = _rq.get(f"{_QURL}/collections/{col}", timeout=5)
            if r.status_code != 200:
                continue
            if dry_run:
                _log(f"  [dry] Would clean {col} chunks older than 14 days")
                continue
            r2 = _rq.post(
                f"{_QURL}/collections/{col}/points/delete",
                json={"filter": {"must": [{"key": "indexed_at", "range": {"lt": cutoff}}]}},
                timeout=10,
            )
            if r2.status_code in (200, 201):
                _log(f"  [OK] RSS cleanup: {col} -- removed chunks older than 14 days")
        except Exception as e:
            _log(f"  ⚠ RSS cleanup error ({col}): {e}")
    """Remove RSS chunks older than 14 days from rss_feed collection."""
    try:
        from datetime import datetime, timezone, timedelta
        import requests as _rq
        from config import QDRANT_URL as _QURL

        r = _rq.get(f"{_QURL}/collections/rss_feed", timeout=5)
        if r.status_code != 200:
            return  # collection doesn't exist yet -- skip silently

        cutoff = (datetime.now(timezone.utc) - timedelta(days=14)).isoformat()

        if dry_run:
            _log("  [dry] Would clean rss_feed chunks older than 14 days")
            return

        r2 = _rq.post(
            f"{_QURL}/collections/rss_feed/points/delete",
            json={
                "filter": {
                    "must": [{
                        "key":   "indexed_at",
                        "range": {"lt": cutoff},
                    }]
                }
            },
            timeout=10,
        )
        if r2.status_code in (200, 201):
            _log("  [OK] RSS cleanup: removed chunks older than 14 days")
        else:
            _log(f"  ⚠ RSS cleanup failed: HTTP {r2.status_code}")
    except Exception as e:
        _log(f"  ⚠ RSS cleanup error: {e}")


def run_night(rewrite: bool, rewrite_only: bool, model_override: str,
              limit: int, dry_run: bool) -> None:

    start_time  = time.time()
    cost_before = _cumulative_cost()

    _log("=" * 60)
    _log("NIGHT RUN starting")
    _log(f"  rewrite={rewrite}  rewrite_only={rewrite_only}")
    _log(f"  model_override={model_override or 'keep item model'}")
    _log(f"  limit={limit}  dry_run={dry_run}")
    _log("=" * 60)

    # -- Override model if requested ------------------------------
    if model_override and not rewrite_only:
        items   = queue.get_all()
        changed = 0
        for item in items:
            if item["status"] in (queue.STATUS_PENDING, queue.STATUS_RESEARCHED):
                if item.get("model") != model_override:
                    q = queue.load()
                    for qi in q["items"]:
                        if qi["id"] == item["id"]:
                            qi["model"] = model_override
                            break
                    queue.save(q)
                    changed += 1
        if changed:
            _log(f"  Model overridden -> {model_override} for {changed} items")

    # -- REWRITE ONLY mode ----------------------------------------
    if rewrite_only:
        _run_rewrite_batch(limit, dry_run, cost_before)
        _cleanup_rss(dry_run=dry_run)
        _summary(start_time, cost_before)
        return

    # -- RESEARCH pending items -----------------------------------
    pending = queue.get_pending()
    if limit:
        pending = pending[:limit]

    if pending:
        _log(f"\nRESEARCH: {len(pending)} pending topics")
        if dry_run:
            for item in pending:
                _log(f"  [dry] Would research: [{item['id']}] {item['topic'][:60]}")
        else:
            from pipeline.research_run import run_research
            run_research(item_ids=[i["id"] for i in pending])
    else:
        _log("No pending topics to research.")

    # -- GENERATE researched items --------------------------------
    researched = queue.get_researched()
    if limit:
        researched = researched[:limit]

    generated_ids = []
    if researched:
        _log(f"\nGENERATE: {len(researched)} researched articles")
        if dry_run:
            for item in researched:
                _log(f"  [dry] Would generate: [{item['id']}] {item['topic'][:60]}")
        else:
            from pipeline.generate_run import run_generate
            run_generate(item_ids=[i["id"] for i in researched], night_run=True)
            generated_ids = [i["id"] for i in researched]
    else:
        _log("No researched articles to generate.")

    # -- CLAUDE REWRITE -------------------------------------------
    if rewrite:
        _run_rewrite_batch(limit, dry_run, cost_before, generated_ids=generated_ids)

    # -- RSS CLEANUP ----------------------------------------------
    _log("\nRSS cleanup...")
    _cleanup_rss(dry_run=dry_run)

    _summary(start_time, cost_before)


def _run_rewrite_batch(limit: int, dry_run: bool, cost_before: float,
                       generated_ids: list = None) -> None:
    """Rewrite drafts generated in this run with Claude."""
    import time as _time
    import json as _json
    from config import OUTPUT_DIR

    output_dir = Path(OUTPUT_DIR)
    if not output_dir.exists():
        _log("No output directory found.")
        return

    # Nowy format: katalogi z article.md
    if generated_ids:
        # Znajdź katalogi po queue_id w metadata.json
        drafts = []
        candidates = sorted(
            [d for d in output_dir.iterdir()
             if d.is_dir() and (d / "article.md").exists()],
            key=lambda x: x.stat().st_mtime,
            reverse=True,
        )
        for item_id in generated_ids:
            for d in candidates:
                meta_path = d / "metadata.json"
                if not meta_path.exists():
                    continue
                try:
                    meta = _json.loads(meta_path.read_text(encoding="utf-8"))
                    if meta.get("queue_id") == item_id:
                        drafts.append(d)
                        break
                except Exception:
                    pass
    else:
        # Fallback: katalogi zmodyfikowane w ostatnich 10 minutach
        cutoff = _time.time() - 600
        drafts = sorted(
            [d for d in output_dir.iterdir()
             if d.is_dir()
             and (d / "article.md").exists()
             and d.stat().st_mtime > cutoff],
            key=lambda x: x.stat().st_mtime,
            reverse=True,
        )

    if limit:
        drafts = drafts[:limit]

    # Filtruj już przepisane
    pending = []
    for d in drafts:
        try:
            has_rewrite = any(
                f.name.startswith("article_") and f.name.endswith("_rewrite.md")
                for f in d.iterdir()
            )
        except Exception:
            has_rewrite = False
        if not has_rewrite:
            pending.append(d)

    if not pending:
        _log("No fresh drafts found for rewrite.")
        return

    _log(f"\nREWRITE: {len(pending)} drafts with Claude")

    rewrite_script = BASE_DIR / "claude_rewrite.py"
    if not rewrite_script.exists():
        _log("  claude_rewrite.py not found -- skipping rewrite.")
        return

    rewrote = 0
    for draft_dir in pending:
        if dry_run:
            _log(f"  [dry] Would rewrite: {draft_dir.name}")
            continue

        _log(f"  -> Rewriting: {draft_dir.name}")
        import subprocess
        result = subprocess.run(
            [sys.executable, str(rewrite_script),
             "--file", str(draft_dir),
             "--persona", "lukasz",
             "--provider", "claude"],
            capture_output=False,
        )
        if result.returncode == 0:
            rewrote += 1
            _log(f"  [OK] Done")
        else:
            _log(f"  [X] Failed (exit {result.returncode})")

    if not dry_run:
        cost_after = _cumulative_cost()
        _log(f"\n  Rewrote {rewrote}/{len(pending)} articles")
        _log(f"  Rewrite cost this batch: ${cost_after - cost_before:.4f}")


def _summary(start_time: float, cost_before: float) -> None:
    elapsed   = time.time() - start_time
    cost_now  = _cumulative_cost()
    cost_diff = cost_now - cost_before
    mins      = int(elapsed // 60)
    secs      = int(elapsed % 60)

    done       = len([i for i in queue.get_all() if i["status"] == queue.STATUS_DONE])
    pending    = len(queue.get_pending())
    researched = len(queue.get_researched())

    print()
    print("=" * 60)
    print(f"  NIGHT RUN COMPLETE")
    print(f"  Time:    {mins}m {secs}s")
    print(f"  Cost:    ${cost_diff:.4f} this run  |  ${cost_now:.4f} total")
    print(f"  Queue:   {pending} pending  {researched} ready  {done} done")
    print("=" * 60)


def main():
    parser = argparse.ArgumentParser(description="Night run -- unattended batch pipeline")
    parser.add_argument("--rewrite",      action="store_true", help="Claude rewrite after generate")
    parser.add_argument("--rewrite-only", action="store_true", help="Only rewrite existing drafts")
    parser.add_argument("--model",        default="",          help="Override model (NORMAL/MAX/HERETIC)")
    parser.add_argument("--limit",        type=int, default=0, help="Max articles to process (0=all)")
    parser.add_argument("--dry-run",      action="store_true", help="Preview only, no changes")
    args = parser.parse_args()

    MODEL_MAP = {
        "NORMAL":  "qwen2.5:32b",
        "MAX":     "qwen3.5:122b",
        "HERETIC": "huihui-gpt-120b",
        "DEV":     "qwen2.5:7b",
    }
    model = MODEL_MAP.get(args.model.upper(), args.model)

    run_night(
        rewrite        = args.rewrite,
        rewrite_only   = args.rewrite_only,
        model_override = model,
        limit          = args.limit,
        dry_run        = args.dry_run,
    )


if __name__ == "__main__":
    main()