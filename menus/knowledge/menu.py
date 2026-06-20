# menus/knowledge/menu.py -- Main KNOWLEDGE BASE menu

from .review import _inspect_and_promote
from .browse import _browse_knowledge
from .clean import _clean_menu, _stats_menu
from .feed import _feed_menu
from .domains import _domains_menu
from .extract import _extract_menu


def _knowledge_menu() -> None:
    try:
        from qdrant_client import QdrantClient
        from config import QDRANT_URL
        client = QdrantClient(url=QDRANT_URL)
    except Exception as e:
        print(f"  \u2717 Cannot connect to Qdrant: {e}")
        return

    while True:
        try:
            all_cols = [c.name for c in client.get_collections().collections]
        except Exception as e:
            print(f"  \u2717 Qdrant error: {e}")
            return

        research_cols  = sorted([c for c in all_cols if c.startswith("research_")])
        knowledge_cols = sorted([c for c in all_cols if c.startswith("knowledge_")])
        persona_cols   = sorted([c for c in all_cols if c.startswith("persona_")])

        print()
        print(f"  \u2554\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2557")
        print(f"  \u2551  KNOWLEDGE BASE                          \u2551")
        print(f"  \u2560\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2563")
        print(f"  \u2551  RESEARCH (temporary)                    \u2551")
        for col in research_cols:
            try:
                n = client.get_collection(col).points_count
                print(f"  \u2551    {col:<28} {n:>6} chunks  \u2551")
            except Exception:
                pass
        print(f"  \u2560\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2563")
        print(f"  \u2551  KNOWLEDGE (permanent)                   \u2551")
        for col in knowledge_cols:
            try:
                n = client.get_collection(col).points_count
                print(f"  \u2551    {col:<28} {n:>6} chunks  \u2551")
            except Exception:
                pass
        if not knowledge_cols:
            print(f"  \u2551    (empty)                               \u2551")
        print(f"  \u2560\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2563")
        print(f"  \u2551  PERSONAS                                 \u2551")
        for col in persona_cols:
            try:
                n = client.get_collection(col).points_count
                print(f"  \u2551    {col:<28} {n:>6} chunks  \u2551")
            except Exception:
                pass
        print(f"  \u2560\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2563")
        print(f"  \u2551  [1]  Stats                              \u2551")
        print(f"  \u2551  [2]  Feed                               \u2551")
        print(f"  \u2551  [3]  Inspect & Promote                  \u2551")
        print(f"  \u2551  [4]  Browse knowledge                   \u2551")
        print(f"  \u2551  [5]  Clean                              \u2551")
        print(f"  \u2551  [6]  Wipe collection(s)                 \u2551")
        print(f"  \u2551  [7]  Domains                            \u2551")
        print(f"  \u2551  [8]  Extract facts (LLM)                \u2551")
        print(f"  \u2551  [Enter]  Back                           \u2551")
        print(f"  \u255a\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u255d")
        print()

        cmd = input("  Choice: ").strip().lower()

        if cmd in ("", "q", "back"):
            break
        elif cmd == "1":
            _stats_menu(client)
        elif cmd == "2":
            _feed_menu()
        elif cmd == "3":
            _inspect_and_promote(client)
        elif cmd == "4":
            _browse_knowledge(client)
        elif cmd == "5":
            _clean_menu(client)
        elif cmd == "6":
            try:
                all_wipeable = research_cols + knowledge_cols + persona_cols
                if not all_wipeable:
                    print("  No collections found.")
                    continue
                print()
                for i, c in enumerate(all_wipeable, 1):
                    try:
                        n = client.get_collection(c).points_count
                    except Exception:
                        n = 0
                    print(f"  [{i:>2}] {c:<35} {n:>6} chunks")
                print()
                print("  Select: number (1), range (1-5), list (1,3,5) | a=all research")
                raw = input("  > ").strip().lower()
                if not raw:
                    continue
                if raw == "a":
                    indices = list(range(len(research_cols)))
                else:
                    from discovery.selector import _parse_selection
                    parsed = _parse_selection(raw, len(all_wipeable))
                    if parsed is None:
                        print("  Invalid format.")
                        continue
                    indices = parsed
                cols_to_wipe = [all_wipeable[i] for i in indices]
                if not cols_to_wipe:
                    continue
                print(f"\n  Will wipe {len(cols_to_wipe)} collection(s):")
                for c in cols_to_wipe:
                    print(f"    \u2022 {c}")
                confirm = input(f"\n  Wipe ALL chunks? [y/N]: ").strip().lower()
                if confirm in ("y","yes"):
                    for col in cols_to_wipe:
                        try:
                            client.delete_collection(col)
                            print(f"  \u2713 Wiped '{col}'")
                        except Exception as e:
                            print(f"  \u2717 Error wiping '{col}': {e}")
            except Exception as e:
                print(f"  Error: {e}")
        elif cmd == "7":
            _domains_menu()
        elif cmd == "8":
            _extract_menu(client)
        else:
            print("  Unknown option.")
