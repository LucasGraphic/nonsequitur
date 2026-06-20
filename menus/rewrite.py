# menus/rewrite.py -- Rewrite menu, night run
import os
import sys
import json

import core.queue as queue


def _rewrite_menu() -> None:
    import subprocess

    rewrite_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "claude_rewrite.py")
    if not os.path.exists(rewrite_path):
        print("  claude_rewrite.py not found.")
        return

    while True:
        print()
        print("  +==========================================+")
        print("  |  REWRITE -- API                           |")
        print("  +==========================================+")
        print("  |  [1]  Rewrite article (interactive)      |")
        print("  |  [2]  Dry run -- preview prompt only      |")
        print("  |  [3]  Token / cost stats                 |")
        print("  |  [Enter]  Back                           |")
        print("  +==========================================+")
        print()

        raw = input("  Choice: ").strip().lower()

        if raw in ("", "q", "back"):
            break
        elif raw == "1":
            subprocess.call([sys.executable, rewrite_path])
        elif raw == "2":
            subprocess.call([sys.executable, rewrite_path, "--dry-run"])
        elif raw == "3":
            subprocess.call([sys.executable, rewrite_path, "--stats"])
        else:
            print("  Unknown option.")


# -- Cumulative cost --------------------------------------------------------

def _cumulative_cost() -> float:
    import re as _re
    log_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "output", "_rewrite_log.txt")
    total = 0.0
    if not os.path.exists(log_path):
        return 0.0
    try:
        with open(log_path, encoding="utf-8") as f:
            for line in f:
                m = _re.search(r"cost=\$([\d.]+)", line)
                if m:
                    total += float(m.group(1))
    except Exception:
        pass
    return total


# -- Feeds menu -------------------------------------------------------------

def _feeds_menu() -> None:
    """[F] Feeds -- RSS sources management and browsing."""
    while True:
        print()
        print("  +==========================================+")
        print("  |  FEEDS -- RSS                             |")
        print("  +==========================================+")
        print("  |  [1]  Browse -- przeglądaj artykuły       |")
        print("  |  [2]  Fetch all feeds                    |")
        print("  |  [3]  Fetch by category                  |")
        print("  |  [4]  List configured feeds              |")
        print("  |  [Enter]  Back                           |")
        print("  +==========================================+")
        print()

        raw = input("  Choice: ").strip().lower()

        if raw in ("", "q", "back"):
            break

        elif raw == "1":
            from pipeline.discovery_run import _rss_discover
            added = _rss_discover("DATA")
            if added:
                print(f"\n  -> Added {added} topics to queue.")
                import core.queue as _q
                _q.print_queue(filter_status=_q.STATUS_PENDING)

        elif raw == "2":
            from pipeline.rss_run import run_rss
            run_rss()

        elif raw == "3":
            from pipeline.rss_run import run_rss
            from config import RESEARCH_CATEGORIES_DATA, RESEARCH_CATEGORIES_PORTFOLIO
            cats = RESEARCH_CATEGORIES_DATA + RESEARCH_CATEGORIES_PORTFOLIO
            print()
            for i, c in enumerate(cats, 1):
                print(f"  [{i:>2}] {c}")
            print()
            raw_cat = input("  Category (number or name): ").strip()
            if raw_cat.isdigit() and 1 <= int(raw_cat) <= len(cats):
                cat = cats[int(raw_cat) - 1]
            else:
                cat = raw_cat
            if cat:
                run_rss(category=cat)

        elif raw == "4":
            from pipeline.rss_run import _load_sources
            feeds = _load_sources()
            cats  = sorted(set(f["category"] for f in feeds))
            print(f"\n  Configured feeds ({len(feeds)}):\n")
            for cat in cats:
                cat_feeds = [f for f in feeds if f["category"] == cat]
                print(f"  [{cat}]  ({len(cat_feeds)} feeds)")
                for f in cat_feeds:
                    trust_tag = f"[{f['trust']}]" if f["trust"] != "unknown" else ""
                    print(f"    {trust_tag:<9} {f['label']}")
                print()

        else:
            print("  Unknown option.")


# -- Night run menu ---------------------------------------------------------

def _night_run_menu() -> None:
    """Night run -- batch pipeline without interaction."""
    import subprocess

    n_pending    = len(queue.get_pending())
    n_research   = len(queue.get_researched())
    pending_items    = queue.get_pending()
    researched_items = queue.get_researched()

    print()

    # Show pending items
    if pending_items:
        print("  -- TO RESEARCH --------------------------------------")
        for item in pending_items:
            slug  = f"[{item.get('topic_slug', '')}]" if item.get('topic_slug') else ""
            focus = "[F]" if item.get('article_focus') else ""
            urls  = f"[{len(item.get('seed_urls', []))}u]" if item.get('seed_urls') else ""
            print(f"  o  {item['topic'][:60]:<60} {slug} {focus} {urls}")
        print()

    # Show researched items
    if researched_items:
        print("  -- TO GENERATE --------------------------------------")
        for item in researched_items:
            slug  = f"[{item.get('topic_slug', '')}]" if item.get('topic_slug') else ""
            focus = "[F]" if item.get('article_focus') else "    "
            model = item.get('model', '')[:14]
            print(f"  ◑  {item['topic'][:60]:<60} {focus} {model}")
        print()

    if not pending_items and not researched_items:
        print("  Queue is empty -- nothing to run.")
        print()

    print("  +==============================================+")
    print("  |  NIGHT RUN                                   |")
    print("  +==============================================+")
    print(f"  |  Queue: {n_pending} pending  {n_research} ready{' '*(32-len(str(n_pending))-len(str(n_research)))}|")
    print("  +==============================================+")
    print("  |  [1]  Research + Generate                    |")
    print("  |  [2]  Research + Generate + Rewrite          |")
    print("  |  [A]  AUTO -- no prompts                      |")
    print("  |  [3]  Rewrite only -- select articles         |")
    print("  |  [4]  Dry run -- preview only                 |")
    print("  |  [Enter]  Back                               |")
    print("  +==============================================+")
    print()

    raw = input("  Choice: ").strip().lower()
    if not raw or raw == "0":
        return

    night_script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "night_run.py")
    if not os.path.exists(night_script):
        print("  night_run.py not found.")
        return

    # [A] AUTO -- zero pytań
    if raw == "a":
        n_total = n_pending + n_research
        est     = n_total * 0.046
        print(f"\n  AUTO mode: {n_total} article(s) | model: NORMAL | rewrite: YES")
        print(f"  Estimated cost: ~${est:.2f}")
        print(f"  Starting in 3 seconds -- Ctrl+C to abort...")
        import time
        time.sleep(3)
        subprocess.call([sys.executable, night_script, "--rewrite", "--model", "NORMAL"])
        return

    # [3] Rewrite only
    if raw == "3":
        from config import OUTPUT_DIR
        import json as _json

        all_dirs = sorted(
            [d for d in os.listdir(OUTPUT_DIR)
             if os.path.isdir(os.path.join(OUTPUT_DIR, d))
             and os.path.exists(os.path.join(OUTPUT_DIR, d, "article.md"))],
            reverse=True,
        )

        pending_dirs = all_dirs[:30]
        if not pending_dirs:
            print("  No drafts found.")
            return

        print()
        print("  Select articles to rewrite (most recent first):")
        for i, d in enumerate(pending_dirs, 1):
            meta_path = os.path.join(OUTPUT_DIR, d, "metadata.json")
            try:
                meta  = _json.loads(open(meta_path, encoding="utf-8").read())
                topic = meta.get("title", d)[:55]
                size  = os.path.getsize(os.path.join(OUTPUT_DIR, d, "article.md")) // 1024
            except Exception:
                topic = d[:55]
                size  = 0
            print(f"  [{i:>2}] {topic:<55} {size}KB")

        print()
        print("  Select: number (1), range (1-5), list (1,3,5) | a=all | Enter=cancel")
        sel = input("  > ").strip().lower()
        if not sel:
            return

        if sel == "a":
            selected = pending_dirs[:10]
        else:
            indices = []
            try:
                if "-" in sel and "," not in sel:
                    parts  = sel.split("-")
                    start, end = int(parts[0]) - 1, int(parts[1]) - 1
                    indices = list(range(max(0, start), min(len(pending_dirs), end + 1)))
                elif "," in sel:
                    indices = [int(x.strip()) - 1 for x in sel.split(",")]
                    indices = [i for i in indices if 0 <= i < len(pending_dirs)]
                else:
                    idx = int(sel) - 1
                    if 0 <= idx < len(pending_dirs):
                        indices = [idx]
            except ValueError:
                pass
            if not indices:
                print("  Invalid selection.")
                return
            selected = [pending_dirs[i] for i in indices]

        est_cost = len(selected) * 0.046
        print(f"\n  Selected {len(selected)} articles")
        print(f"  Estimated cost: ~${est_cost:.2f}")
        confirm = input(f"  Rewrite {len(selected)} with Claude? [y/N]: ").strip().lower()
        if confirm not in ("y", "yes"):
            return

        rewrite_script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "claude_rewrite.py")
        rewrote = 0
        for d in selected:
            dir_path = os.path.join(OUTPUT_DIR, d)
            print(f"\n  -> {d}")
            import subprocess as _sp
            result = _sp.call([sys.executable, rewrite_script,
                               "--file", dir_path,
                               "--persona", "lukasz",
                               "--provider", "claude"])
            if result == 0:
                rewrote += 1
        print(f"\n  [OK] Rewrote {rewrote}/{len(selected)} articles")
        return

    # Modes 1, 2, 4
    args = [sys.executable, night_script]

    if raw == "1":
        pass
    elif raw == "2":
        args.append("--rewrite")
    elif raw == "4":
        args.append("--dry-run")
    else:
        print("  Unknown option.")
        return

    model_raw = input("  Model override? (Enter = keep item models, or: NORMAL/MAX): ").strip().upper()
    if model_raw in ("NORMAL", "MAX", "HERETIC", "DEV"):
        args += ["--model", model_raw]

    limit_raw = input("  Max articles? (Enter = all, or number): ").strip()
    if limit_raw.isdigit() and int(limit_raw) > 0:
        args += ["--limit", limit_raw]

    if "--rewrite" in args:
        n_total = n_pending + n_research
        if limit_raw.isdigit():
            n_total = min(n_total, int(limit_raw))
        est = n_total * 0.046
        print(f"\n  Estimated rewrite cost: ~${est:.2f} for {n_total} article(s)")
        confirm = input(f"  Proceed? [Y/n]: ").strip().lower()
        if confirm == "n":
            return

    print()
    print(f"  Starting: {' '.join(args[2:]) or 'research + generate'}")
    print("  Press Ctrl+C to stop at any time.")
    print()

    subprocess.call(args)
