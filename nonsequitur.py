#!/usr/bin/env python3
# agent.py -- Article Agent main launcher
#
# USAGE:
#   python agent.py                        # interactive menu
#   python agent.py discover               # discovery + select -> queue
#   python agent.py discover --cat games
#   python agent.py queue                  # view queue
#   python agent.py queue list
#   python agent.py queue remove <id>
#   python agent.py queue clear-done
#   python agent.py run
#   python agent.py run --research-only
#   python agent.py run --generate-only

#!/usr/bin/env python3
# agent.py -- Article Agent main launcher
import sys
import io

# Fix UTF-8 output on Windows PowerShell
if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')
import os
import argparse
import requests as _requests

import core.queue as queue
from pipeline.discovery_run import run_discovery
from pipeline.research_run  import run_research
from pipeline.generate_run  import run_generate

# -- Session state -- API provider selected for this run --------------------
# "" = local Ollama (default)


_SESSION_API_PROVIDER = ""


# -- Health check ----------------------------------------------------------

def _check_connections() -> bool:
    from config import OLLAMA_URL, OLLAMA_EMBED_URL, QDRANT_URL, RERANKER_URL, CRAWL4AI_URL

    # Required services -- pipeline fails without them
    required = [
        ("Ollama Generate", f"{OLLAMA_URL}/api/tags"),
        ("Ollama Embed",    f"{OLLAMA_EMBED_URL}/api/tags"),
        ("Qdrant",          f"{QDRANT_URL}/collections"),
    ]
    # Optional services -- pipeline degrades gracefully
    optional = [
        ("Reranker",        f"{RERANKER_URL}/health"),
        ("Crawl4AI",        f"{CRAWL4AI_URL}/health"),
    ]

    print("  -- Service health check -------------------------------------")
    all_ok = True
    for name, url in required:
        try:
            r = _requests.get(url, timeout=4)
            if r.status_code == 200:
                print(f"  [OK] {name:<20} {url}")
            else:
                print(f"  [X] {name:<20} HTTP {r.status_code}")
                all_ok = False
        except Exception as e:
            print(f"  [X] {name:<20} OFFLINE -- {e}")
            all_ok = False

    for name, url in optional:
        try:
            r = _requests.get(url, timeout=3)
            if r.status_code == 200:
                print(f"  [OK] {name:<20} {url}")
            else:
                print(f"  o {name:<20} HTTP {r.status_code} (optional)")
        except Exception:
            print(f"  o {name:<20} offline (optional -- reranker disabled)")

    if not all_ok:
        print("\n  ⚠  Some services are offline. Pipeline may fail.")
        raw = input("  Continue anyway? [y/N]: ").strip().lower()
        if raw != "y":
            return False
    else:
        print("  All services online.\n")
    return True


def _init_persona_collections() -> None:
    """Create persona_* Qdrant collections if they don't exist."""
    from config import QDRANT_URL, EMBED_DIM, PERSONAS

    try:
        r = _requests.get(f"{QDRANT_URL}/collections", timeout=5)
        existing = [c["name"] for c in r.json().get("result", {}).get("collections", [])]
    except Exception:
        return  # Qdrant offline -- skip silently

    created = 0
    for name in PERSONAS:
        col = f"persona_{name}"
        if col not in existing:
            payload = {
                "vectors":        {"dense":  {"size": EMBED_DIM, "distance": "Cosine"}},
                "sparse_vectors": {"sparse": {"index": {"on_disk": False}}},
            }
            r2 = _requests.put(f"{QDRANT_URL}/collections/{col}",
                                json=payload, timeout=10)
            if r2.status_code in (200, 201):
                print(f"  [OK] Created persona collection: {col}")
                created += 1

    if created:
        print()


# -- Notification ----------------------------------------------------------

def _notify_pipeline_done(count: int, elapsed: str) -> None:
    import datetime as _dt
    msg   = f"Article Agent: {count} article(s) done in {elapsed}"
    stamp = _dt.datetime.now().strftime("%Y-%m-%d %H:%M")

    try:
        import winsound
        winsound.Beep(1000, 500)
        winsound.Beep(1200, 300)
    except Exception:
        print("\a")

    print(f"\n  PIPELINE DONE -- {msg}")

    try:
        from config import OUTPUT_DIR
        log_path = os.path.join(OUTPUT_DIR, "_pipeline_log.txt")
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(f"{stamp}  {msg}\n")
    except Exception:
        pass


# -- Pipeline --------------------------------------------------------------

def _run_pipeline(api_provider: str = "") -> None:
    import time

    ETA_RESEARCH_PER_TOPIC = 360
    ETA_GENERATE_PER_TOPIC = 300

    already_researched = queue.get_researched()
    pending            = queue.get_pending()
    total_topics       = len(already_researched) + len(pending)

    if total_topics == 0:
        print("  Queue is empty.")
        return

    eta_sec = (len(already_researched) * ETA_GENERATE_PER_TOPIC +
               len(pending) * (ETA_RESEARCH_PER_TOPIC + ETA_GENERATE_PER_TOPIC))
    eta_min = eta_sec // 60
    eta_h   = eta_min // 60
    eta_m   = eta_min % 60
    eta_str = f"{eta_h}h {eta_m}min" if eta_h else f"{eta_m}min"

    provider_label = ""
    if api_provider:
        from config import API_PROVIDERS
        provider_label = f" | API: {API_PROVIDERS.get(api_provider, {}).get('label', api_provider)}"

    print(f"\n  -- Pipeline starting -----------------------------------------")
    print(f"  Topics: {total_topics}  "
          f"({len(already_researched)} researched + {len(pending)} pending){provider_label}")
    print(f"  Estimated time: ~{eta_str}")
    print(f"  -------------------------------------------------------------")

    pipeline_start = time.time()
    done_count     = 0

    if already_researched:
        print(f"\n  -> Generating {len(already_researched)} already researched topic(s)...")
        for item in already_researched:
            run_generate(item_ids=[item["id"]], api_provider=api_provider)
            done_count += 1
            _print_pipeline_progress(done_count, total_topics, pipeline_start)

    while True:
        pending = queue.get_pending()
        if not pending:
            break
        item      = pending[0]
        remaining = len(pending)
        print(f"\n  -- [{done_count+1}/{total_topics}] -----------------------------")
        print(f"  Topic: {item['topic'][:65]}")
        print(f"  Remaining after this: {remaining - 1} topic(s)")

        run_research(item_ids=[item["id"]])

        refreshed = [i for i in queue.get_all() if i["id"] == item["id"]]
        if refreshed and refreshed[0]["status"] == queue.STATUS_RESEARCHED:
            run_generate(item_ids=[item["id"]], api_provider=api_provider)
            done_count += 1
            _print_pipeline_progress(done_count, total_topics, pipeline_start)
        else:
            print(f"  [X] Research failed for [{item['id']}] -- skipping generate")
            done_count += 1

    elapsed = time.time() - pipeline_start
    elapsed_str = (f"{int(elapsed//3600)}h {int((elapsed%3600)//60)}min"
                   if elapsed >= 3600 else f"{int(elapsed//60)}min {int(elapsed%60)}s")

    print(f"\n  +==========================================+")
    print(f"  |  [OK] Pipeline complete                     |")
    print(f"  |  Topics processed: {done_count:<22}|")
    print(f"  |  Total time: {elapsed_str:<27}|")
    print(f"  +==========================================+\n")

    _notify_pipeline_done(done_count, elapsed_str)


def _print_pipeline_progress(done: int, total: int, start: float) -> None:
    import time
    elapsed   = time.time() - start
    per_item  = elapsed / done if done else 0
    remaining = total - done
    eta_sec   = per_item * remaining
    eta_str   = (f"{int(eta_sec//3600)}h {int((eta_sec%3600)//60)}min"
                 if eta_sec >= 3600 else f"{int(eta_sec//60)}min {int(eta_sec%60)}s")
    bar_len = 20
    filled  = int(bar_len * done / max(total, 1))
    bar     = "#" * filled + "░" * (bar_len - filled)
    print(f"\n  Progress  [{bar}]  {done}/{total}  "
          f"ETA: {eta_str if remaining else 'done'}")


# -- API Settings menu ------------------------------------------------------

# -- Menu modules ------------------------------------------------------------
from menus.settings  import _settings_menu, _embedding_model_menu
from menus.queue     import _fmt_list, _inspect_edit_item, _show_rag_sources, _rewrite_single, _queue_menu
from menus.knowledge import _print_chunk, _knowledge_menu, _paste_text_to_qdrant, _feed_persona_menu, _feed_menu
from menus.rewrite   import _rewrite_menu, _cumulative_cost, _night_run_menu, _feeds_menu


def _run_smart(n_pending: int, n_research: int) -> None:
    """Smart run -- no submenu for common cases."""
    global _SESSION_API_PROVIDER
    from config import API_PROVIDERS

    current_provider = _SESSION_API_PROVIDER
    provider_label   = (API_PROVIDERS[current_provider]["label"]
                        if current_provider in API_PROVIDERS else "Local Ollama")

    total = n_pending + n_research

    if total == 0:
        print("  Queue is empty -- run Discovery first.")
        return

    # Simple case: only research needed
    if n_pending > 0 and n_research == 0:
        confirm = input(f"  Research {n_pending} topic(s)? [Y/n]: ").strip().lower()
        if confirm in ("", "y", "yes"):
            run_research()
        return

    # Simple case: only generate needed
    if n_research > 0 and n_pending == 0:
        print(f"  {n_research} article(s) ready to generate via {provider_label}")
        print(f"  [Enter] Generate  |  [a] Pick API provider  |  [q] Cancel")
        raw = input("  > ").strip().lower()
        if raw == "q":
            return
        if raw == "a":
            current_provider = _pick_provider_quick()
            if not current_provider and current_provider != "":
                return
            provider_label = API_PROVIDERS.get(current_provider, {}).get("label", "Local Ollama") if current_provider else "Local Ollama"
        confirm = input(f"  Generate {n_research} article(s) via {provider_label}? [Y/n]: ").strip().lower()
        if confirm in ("", "y", "yes"):
            run_generate(api_provider=current_provider)
        return

    # Both pending and researched
    print(f"  {n_pending} pending (need research) + {n_research} ready (need generate)")
    print(f"  [1] Full pipeline -- research + generate all")
    print(f"  [2] Research only ({n_pending} topics)")
    print(f"  [3] Generate only ({n_research} articles) via {provider_label}")
    print(f"  [4] Pick items to generate")
    print(f"  [Enter] Cancel")
    raw = input("  > ").strip().lower()

    if raw == "1":
        confirm = input(f"  Run full pipeline? [Y/n]: ").strip().lower()
        if confirm in ("", "y", "yes"):
            _run_pipeline(api_provider=current_provider)
    elif raw == "2":
        confirm = input(f"  Research {n_pending} topics? [Y/n]: ").strip().lower()
        if confirm in ("", "y", "yes"):
            run_research()
    elif raw == "3":
        confirm = input(f"  Generate {n_research} articles? [Y/n]: ").strip().lower()
        if confirm in ("", "y", "yes"):
            run_generate(api_provider=current_provider)
    elif raw == "4":
        researched_items = queue.get_researched()
        if not researched_items:
            print("  No researched items.")
            return
        print()
        for i, item in enumerate(researched_items, 1):
            print(f"  [{i}] {item['topic'][:65]}  [{item['id']}]")
        print()
        raw_pick = input("  Numbers (e.g. 1 3) or Enter=all: ").strip()
        if not raw_pick:
            selected = researched_items
        else:
            idxs = [int(x)-1 for x in raw_pick.split() if x.isdigit()]
            selected = [researched_items[i] for i in idxs if 0 <= i < len(researched_items)]
        if not selected:
            print("  Nothing selected.")
            return
        print(f"  Generating {len(selected)} article(s)...")
        for item in selected:
            run_generate(item_ids=[item["id"]], api_provider=current_provider)


def _pick_provider_quick() -> str:
    """Quick provider picker -- one-liner."""
    from config import API_PROVIDERS
    options = [("", "Local Ollama")] + [(k, v["label"]) for k, v in API_PROVIDERS.items()]
    for i, (key, label) in enumerate(options, 1):
        key_ok = "[OK]" if (not key or os.environ.get(API_PROVIDERS[key]["env_key"], "")) else "[X]"
        print(f"  [{i}] {key_ok} {label}")
    raw = input("  Provider (Enter = cancel): ").strip()
    if not raw:
        return None
    if raw.isdigit() and 1 <= int(raw) <= len(options):
        return options[int(raw) - 1][0]
    return None


# Keep old _run_menu as alias for CLI compatibility
def _run_menu(n_pending: int, n_research: int) -> None:
    _run_smart(n_pending, n_research)


# -- Knowledge base menu ----------------------------------------------------



def _pad(text: str, width: int) -> str:
    """Pad text to display width, accounting for wide unicode chars."""
    try:
        from wcwidth import wcswidth
        display_w = wcswidth(text)
        if display_w < 0:
            display_w = len(text)
    except ImportError:
        # Fallback: count known wide chars manually
        wide = set("o◑*[X][OK]⚠-><->◀#░")
        display_w = sum(2 if c in wide else 1 for c in text)
    pad = width - display_w
    return text + " " * max(pad, 0)


def _mline(text: str, width: int = 42) -> str:
    """Force text to exactly `width` display chars, then wrap in menu borders."""
    # Truncate if too long
    if len(text) > width:
        text = text[:width]
    # Pad to exact width
    return f"  |  {text}{' ' * (width - len(text))}|"

def interactive_menu() -> None:
    global _SESSION_API_PROVIDER
    from config import API_PROVIDERS

    while True:
        all_items  = queue.get_all()
        n_pending  = sum(1 for i in all_items if i["status"] == queue.STATUS_PENDING)
        n_research = sum(1 for i in all_items if i["status"] == queue.STATUS_RESEARCHED)
        n_done     = sum(1 for i in all_items if i["status"] == queue.STATUS_DONE)
        n_errors   = sum(1 for i in all_items if i["status"] == queue.STATUS_ERROR)

        provider_label = (API_PROVIDERS[_SESSION_API_PROVIDER]["label"]
                          if _SESSION_API_PROVIDER in API_PROVIDERS else "Local Ollama")
        cost = _cumulative_cost()
        cost_str = f"${cost:.3f} spent" if cost > 0 else "no API spend"

        # Build queue status line
        q_parts = []
        if n_pending:  q_parts.append(f"o{n_pending} pending")
        if n_research: q_parts.append(f"~{n_research} ready")
        if n_done:     q_parts.append(f"*{n_done} done")
        if n_errors:   q_parts.append(f"x{n_errors} err")
        q_str = "  ".join(q_parts) if q_parts else "empty"

        # Menu width: 44 chars between borders
        W = 44
        DIV = "  " + "+" + "=" * W + "+"

        def ml(text):
            # Truncate and pad to exactly W chars, wrap in borders
            t = text[:W]
            return "  |" + t + " " * (W - len(t)) + "|"

        print()
        print("  " + "+" + "=" * W + "+")
        print(ml("        ARTICLE AGENT"))
        print(DIV)
        print(ml("  " + q_str))
        _lbl = provider_label[:22]
        _cst = cost_str[:14]
        _gap = W - 2 - len(_lbl) - len(_cst)
        print("  |  " + _lbl + " " * max(_gap, 1) + _cst + "|")
        print(DIV)
        print(ml("  [1]  Discover - find topics"))
        print(ml("  [2]  Queue - manage / edit"))

        # Smart [3] label based on queue state
        if n_pending > 0 and n_research > 0:
            run_label = f"Run - {n_pending} research + {n_research} generate"
        elif n_pending > 0:
            run_label = f"Run - research {n_pending} topic(s)"
        elif n_research > 0:
            run_label = f"Run - generate {n_research} article(s)"
        else:
            run_label = "Run pipeline"
        print(ml(f"  [3]  {run_label}"))
        print(ml("  [F]  Feeds - RSS sources & browse"))
        print(ml("  [R]  Rewrite - Claude / API"))
        print(ml("  [K]  Knowledge base"))
        print(ml("  [N]  Night run - batch pipeline"))
        print(ml("  [A]  Settings"))
        print(ml("  [q]  Quit"))
        print("  " + "+" + "=" * W + "+")
        print()

        raw = input("  Choice: ").strip().lower()

        if raw in ("q", "quit", "exit", ""):
            print("  Goodbye.")
            break

        elif raw == "1":
            while True:
                run_discovery()
                again = input("\n  Discover another category? [y/N]: ").strip().lower()
                if again not in ("y", "yes"):
                    break

        elif raw == "2":
            _queue_menu()

        elif raw == "3":
            _run_smart(n_pending, n_research)
        elif raw == "f":
            _feeds_menu()
        elif raw == "r":
            _rewrite_menu()

        elif raw == "k":
            _knowledge_menu()

        elif raw == "n":
            _night_run_menu()

        elif raw == "a":
            _settings_menu()

        else:
            print("  Unknown option.")


# -- CLI --------------------------------------------------------------------

def main():
    from banner import show as _banner_show
    _banner_show()
    if not _check_connections():
        sys.exit(1)

    _init_persona_collections()

    if len(sys.argv) == 1:
        interactive_menu()
        return

    parser = argparse.ArgumentParser(
        prog        = "agent.py",
        description = "Article Agent -- discovery, research, generate",
    )
    sub = parser.add_subparsers(dest="command")

    p_disc = sub.add_parser("discover")
    p_disc.add_argument("--cat")
    p_disc.add_argument("--query")
    p_disc.add_argument("--section")

    p_queue = sub.add_parser("queue")
    p_queue.add_argument("action", nargs="?", default="list",
                         choices=["list", "pending", "researched", "remove", "clear-done", "clear-all"])
    p_queue.add_argument("item_id", nargs="?")

    p_run = sub.add_parser("run")
    p_run.add_argument("--research-only", action="store_true")
    p_run.add_argument("--generate-only", action="store_true")
    p_run.add_argument("--api", default="", help="API provider: claude/openai/gemini/deepseek")

    args = parser.parse_args()

    if args.command == "discover":
        run_discovery(category=args.cat, query=args.query, section=args.section)

    elif args.command == "queue":
        if args.action in ("list", None):
            queue.print_queue()
        elif args.action == "pending":
            queue.print_queue(filter_status=queue.STATUS_PENDING)
        elif args.action == "researched":
            queue.print_queue(filter_status=queue.STATUS_RESEARCHED)
        elif args.action == "remove":
            if not args.item_id:
                print("  Provide ID: python agent.py queue remove <id>")
                sys.exit(1)
            if queue.remove_item(args.item_id):
                print(f"  Removed [{args.item_id}].")
            else:
                print(f"  Not found [{args.item_id}].")
        elif args.action == "clear-done":
            n = queue.clear_done()
            print(f"  Removed {n} completed items.")
        elif args.action == "clear-all":
            confirm = input("  Clear entire queue? [y/N]: ").strip().lower()
            if confirm in ("y", "yes"):
                n = queue.clear_all()
                print(f"  Cleared {n} items.")

    elif args.command == "run":
        api = getattr(args, "api", "")
        if args.generate_only:
            run_generate(api_provider=api)
        elif args.research_only:
            run_research()
        else:
            if queue.get_pending():
                run_research()
            run_generate(api_provider=api)

    else:
        parser.print_help()


if __name__ == "__main__":
    main()