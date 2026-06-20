# menus/knowledge/browse.py -- Browse knowledge collections per slug

import re

from .chunk_utils import _TRUST_LABEL
from .qdrant_ops import _load_all_points, _delete_ids


def _browse_knowledge(client) -> None:
    """[4] Browse -- knowledge + persona collections."""
    from config import QDRANT_URL

    try:
        all_cols       = [c.name for c in client.get_collections().collections]
        knowledge_cols = sorted([c for c in all_cols if c.startswith("knowledge_")])
        persona_cols   = sorted([c for c in all_cols if c.startswith("persona_")])
    except Exception as e:
        print(f"  [X] Qdrant error: {e}")
        return

    if not knowledge_cols and not persona_cols:
        print("  No collections.")
        return

    all_browsable = knowledge_cols + persona_cols

    print()
    if knowledge_cols:
        print("  -- KNOWLEDGE --")
        for i, c in enumerate(knowledge_cols, 1):
            try:
                n = client.get_collection(c).points_count
            except Exception:
                n = 0
            print(f"  [{i}] {c:<35} {n:>6} chunks")
    if persona_cols:
        offset = len(knowledge_cols)
        print("  -- PERSONAS --")
        for i, c in enumerate(persona_cols, offset + 1):
            try:
                n = client.get_collection(c).points_count
            except Exception:
                n = 0
            print(f"  [{i}] {c:<35} {n:>6} chunks")
    print()
    raw = input("  > ").strip()
    if not raw.isdigit() or not (1 <= int(raw) <= len(all_browsable)):
        return
    col = all_browsable[int(raw) - 1]

    points = _load_all_points(client, col)
    if not points:
        print("  Empty collection.")
        return

    slugs = {}
    for p in points:
        slug = p.payload.get("topic_slug", "") or p.payload.get("item_id", "unknown")
        if slug not in slugs:
            slugs[slug] = []
        slugs[slug].append(p)

    slug_list = sorted(slugs.items(), key=lambda x: -len(x[1]))

    while True:
        print()
        print(f"  -- {col} -- {len(slug_list)} slugs ----------------------")
        print()
        for i, (slug, pts) in enumerate(slug_list, 1):
            domains = {}
            for p in pts:
                d = p.payload.get("domain", "?")
                domains[d] = domains.get(d, 0) + 1
            top = sorted(domains.items(), key=lambda x: -x[1])[:3]
            dom_str = "  ".join(f"{d}({c})" for d, c in top)
            print(f"  [{i:>2}] {slug:<45} {len(pts):>4} chunks")
            print(f"        {dom_str}")
            print()

        print(f"  [Enter]  Back")
        print()
        raw = input("  > ").strip()
        if not raw or raw.lower() in ("q", "back"):
            return
        if not raw.isdigit() or not (1 <= int(raw) <= len(slug_list)):
            print("  Invalid choice.")
            continue

        slug, pts = slug_list[int(raw) - 1]
        pts_sorted = sorted(pts, key=lambda p: p.payload.get("chunk_idx", 0))
        _browse_slug_chunks(client, col, slug, pts_sorted, QDRANT_URL)


def _browse_slug_chunks(client, col, slug, points, qdrant_url) -> None:
    """Browse and optionally delete chunks within a slug."""
    points = list(points)  # mutable local copy

    while True:
        if not points:
            print(f"  No chunks remaining for '{slug}'.")
            return

        print()
        print(f"  -- {slug} -- {len(points)} chunks ----------------------")
        print()

        for i, p in enumerate(points, 1):
            pay   = p.payload
            cidx  = pay.get("chunk_idx", "?")
            dom   = pay.get("domain", "?")
            score = pay.get("trust_score", 0)
            ev    = " [EV]" if pay.get("evergreen") else ""
            text  = pay.get("text", "")
            print(f"  [C{i:02d}] chunk_idx={cidx}  {dom}  score={score:.2f}{ev}")
            words = text.split()
            line  = "  "
            for word in words:
                if len(line) + len(word) + 1 > 90:
                    print(line)
                    line = "  " + word + " "
                else:
                    line += word + " "
            if line.strip():
                print(line)
            print(f"  [{len(text)} chars]")
            print()

        print(f"  [d N] delete chunk  [d N-M] delete range  [da] delete all  [Enter] back")
        print()

        raw   = input("  > ").strip()
        parts = raw.split()
        cmd0  = parts[0].lower() if parts else ""

        if not raw or cmd0 in ("q", "back"):
            return

        if cmd0 == "d" and len(parts) > 1:
            indices = []
            for token in parts[1:]:
                token = token.lstrip("cC")
                m = re.match(r"^(\d+)-(\d+)$", token)
                if m:
                    indices += list(range(int(m.group(1))-1, int(m.group(2))))
                elif token.isdigit():
                    indices.append(int(token)-1)
            to_del_ids = []
            to_del_idx = []
            for n in indices:
                if 0 <= n < len(points):
                    to_del_ids.append(points[n].id)
                    to_del_idx.append(n)
                    print(f"  \u2713 C{n+1:02d} marked")
            if to_del_ids:
                confirm = input(f"  Delete {len(to_del_ids)} chunks? [y/N]: ").strip().lower()
                if confirm in ("y", "yes"):
                    _delete_ids(qdrant_url, col, to_del_ids)
                    print(f"  \u2713 Deleted {len(to_del_ids)} chunks")
                    # Remove deleted from local list and refresh view
                    del_set = set(to_del_idx)
                    points = [p for i, p in enumerate(points) if i not in del_set]
            continue

        if cmd0 == "da":
            confirm = input(f"  Delete ALL {len(points)} chunks for '{slug}'? [y/N]: ").strip().lower()
            if confirm in ("y", "yes"):
                _delete_ids(qdrant_url, col, [p.id for p in points])
                print(f"  \u2713 Deleted {len(points)} chunks")
                return
            continue

        print("  d N / d N-M / da = delete | Enter = back")
