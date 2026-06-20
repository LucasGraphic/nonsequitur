# menus/knowledge/clean.py -- Clean menu and Stats menu

from .qdrant_ops import _load_all_points, _delete_ids
from .review import _auto_clean


def _stats_menu(client) -> None:
    """[1] Stats -- domain breakdown per collection."""
    try:
        all_cols = [c.name for c in client.get_collections().collections]
    except Exception as e:
        print(f"  \u2717 Qdrant error: {e}")
        return

    all_c = sorted([c for c in all_cols
                    if c.startswith(("research_","knowledge_","persona_"))])
    if not all_c:
        print("  No collections.")
        return

    print()
    print("  Select collection (Enter = all knowledge):")
    for i, c in enumerate(all_c, 1):
        try:
            n = client.get_collection(c).points_count
        except Exception:
            n = 0
        print(f"  [{i}] {c:<35} {n:>6} chunks")
    print()
    raw = input("  > ").strip()

    if raw.isdigit() and 1 <= int(raw) <= len(all_c):
        selected = [all_c[int(raw)-1]]
    else:
        selected = [c for c in all_c if c.startswith("knowledge_")] or all_c

    for col in selected:
        print(f"\n  {col}:")
        try:
            domain_counts = {}
            offset = None
            while True:
                result = client.scroll(
                    collection_name=col, limit=100, offset=offset,
                    with_payload=True, with_vectors=False,
                )
                points, offset = result
                for p in points:
                    d = p.payload.get("domain","unknown")
                    domain_counts[d] = domain_counts.get(d, 0) + 1
                if offset is None:
                    break
            total = sum(domain_counts.values())
            print(f"  Total: {total} chunks")
            for d, count in sorted(domain_counts.items(), key=lambda x: -x[1])[:20]:
                bar = "\u2588" * min(30, count // max(1, total // 30))
                print(f"  {d:<35} {count:>5}  {bar}")
        except Exception as e:
            print(f"  Error: {e}")


def _clean_menu(client) -> None:
    """[5] Clean -- auto-clean, deduplicate, stale removal."""
    from config import QDRANT_URL

    try:
        all_cols = [c.name for c in client.get_collections().collections]
        research_cols  = sorted([c for c in all_cols if c.startswith("research_")])
        knowledge_cols = sorted([c for c in all_cols if c.startswith("knowledge_")])
        persona_cols   = sorted([c for c in all_cols if c.startswith("persona_")])
    except Exception as e:
        print(f"  \u2717 Qdrant error: {e}")
        return

    while True:
        print()
        print(f"  \u2554\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2557")
        print(f"  \u2551  CLEAN                                   \u2551")
        print(f"  \u2560\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2563")
        print(f"  \u2551  [1]  Auto-clean garbage                 \u2551")
        print(f"  \u2551  [2]  Deduplicate knowledge              \u2551")
        print(f"  \u2551  [3]  Remove stale chunks (by age)       \u2551")
        print(f"  \u2551  [4]  Remove domain from knowledge       \u2551")
        print(f"  \u2551  [5]  Rebuild payload indexes            \u2551")
        print(f"  \u2551  [Enter]  Back                           \u2551")
        print(f"  \u255a\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u255d")
        print()
        cmd = input("  Choice: ").strip().lower()

        if cmd in ("", "q", "back"):
            return

        elif cmd == "1":
            cols = research_cols + knowledge_cols
            for col in cols:
                try:
                    n = client.get_collection(col).points_count
                    if n == 0:
                        continue
                    _auto_clean(client, col, QDRANT_URL)
                except Exception as e:
                    print(f"  Error in {col}: {e}")
            input("  [Enter] to continue: ")

        elif cmd == "2":
            dedup_cols = research_cols + knowledge_cols + persona_cols
            if not dedup_cols:
                print("  No collections to deduplicate.")
                continue
            total_removed = 0
            for col in dedup_cols:
                try:
                    seen_keys = {}
                    to_delete = []
                    offset    = None
                    while True:
                        result = client.scroll(
                            collection_name=col, limit=200, offset=offset,
                            with_payload=True, with_vectors=False,
                        )
                        points, offset = result
                        for p in points:
                            text = p.payload.get("text", "")
                            key  = text[:120] if text else f"{p.payload.get('url','')}::{p.payload.get('chunk_idx',0)}"
                            if key in seen_keys:
                                to_delete.append(p.id)
                            else:
                                seen_keys[key] = p.id
                        if offset is None:
                            break
                    if to_delete:
                        confirm = input(f"  Remove {len(to_delete)} duplicates from '{col}'? [Y/n]: ").strip().lower()
                        if confirm in ("","y","yes"):
                            client.delete(collection_name=col, points_selector=to_delete)
                            print(f"  [OK] Removed {len(to_delete)} from '{col}'")
                            total_removed += len(to_delete)
                    else:
                        print(f"  '{col}': no duplicates")
                except Exception as e:
                    print(f"  Error in {col}: {e}")
            if total_removed:
                print(f"\n  [OK] Total: {total_removed} duplicates removed")

        elif cmd == "3":
            if not knowledge_cols:
                print("  No knowledge collections.")
                continue
            raw = input("  Delete chunks older than N days (Enter=365): ").strip()
            try:
                max_days = int(raw) if raw else 365
            except ValueError:
                max_days = 365
            import datetime as _dt
            cutoff = (_dt.datetime.utcnow() - _dt.timedelta(days=max_days)).isoformat()
            for col in knowledge_cols:
                try:
                    stale_ids = []
                    offset = None
                    while True:
                        result = client.scroll(collection_name=col, limit=200, offset=offset,
                                               with_payload=True, with_vectors=False)
                        points, offset = result
                        for p in points:
                            if p.payload.get("indexed_at","") < cutoff:
                                stale_ids.append(p.id)
                        if offset is None:
                            break
                    if stale_ids:
                        confirm = input(f"  Delete {len(stale_ids)} stale from '{col}'? [y/N]: ").strip().lower()
                        if confirm in ("y","yes"):
                            client.delete(collection_name=col, points_selector=stale_ids)
                            print(f"  \u2713 Deleted {len(stale_ids)} from '{col}'")
                    else:
                        print(f"  '{col}': no stale chunks")
                except Exception as e:
                    print(f"  Error in {col}: {e}")

        elif cmd == "4":
            if not knowledge_cols:
                print("  No knowledge collections.")
                continue
            from qdrant_client.models import Filter, FieldCondition, MatchValue
            domain = input("  Domain to remove: ").strip().lower()
            if not domain:
                continue
            total_found = 0
            for col in knowledge_cols:
                try:
                    count = 0
                    offset = None
                    while True:
                        result = client.scroll(
                            collection_name=col,
                            scroll_filter=Filter(must=[FieldCondition(
                                key="domain", match=MatchValue(value=domain)
                            )]),
                            limit=100, offset=offset,
                            with_payload=False, with_vectors=False,
                        )
                        points, offset = result
                        count += len(points)
                        if offset is None:
                            break
                    if count:
                        print(f"  {col}: {count} chunks")
                        total_found += count
                except Exception as e:
                    print(f"  Error scanning {col}: {e}")
            if total_found == 0:
                print(f"  Domain '{domain}' not found.")
                continue
            confirm = input(f"  Delete {total_found} chunks from '{domain}'? [y/N]: ").strip().lower()
            if confirm in ("y","yes"):
                for col in knowledge_cols:
                    try:
                        from qdrant_client.models import Filter as _F, FieldCondition as _FC, MatchValue as _MV
                        client.delete(collection_name=col, points_selector=_F(
                            must=[_FC(key="domain", match=_MV(value=domain))]
                        ))
                        print(f"  \u2713 Deleted '{domain}' from '{col}'")
                    except Exception as e:
                        print(f"  Error in {col}: {e}")

        elif cmd == "5":
            if not knowledge_cols:
                print("  No knowledge collections.")
                continue
            print()
            print("  Building payload indexes on all knowledge collections...")
            try:
                from qdrant_client.models import PayloadSchemaType
                for col in knowledge_cols:
                    try:
                        client.create_payload_index(col, "topic_slug", PayloadSchemaType.KEYWORD)
                        client.create_payload_index(col, "category",   PayloadSchemaType.KEYWORD)
                        client.create_payload_index(col, "evergreen",  PayloadSchemaType.BOOL)
                        print(f"  \u2713 {col} -- indexes built")
                    except Exception as e:
                        print(f"  \u26a0 {col} -- {e}")
            except Exception as e:
                print(f"  Error: {e}")
            input("  [Enter] to continue: ")

        else:
            print("  Unknown option.")
