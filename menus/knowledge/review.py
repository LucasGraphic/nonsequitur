# menus/knowledge/review.py -- Inspect & Promote pipeline

import re
from datetime import datetime, timezone

import core.queue as queue

from .chunk_utils import _garbage_label, _clean_markdown, _edit_notepad, _TRUST_LABEL
from .qdrant_ops import (
    _ensure_collection, _fetch_with_vectors, _upsert_point,
    _delete_ids, _load_all_points,
)


# -- Auto-clean ----------------------------------------------------------------

def _auto_clean(client, col: str, qdrant_url: str) -> dict:
    """
    Run auto-clean on collection before review.
    Returns report dict with counts per label and total deleted.
    Steps:
      1. Remove garbage chunks (GDPR, newsletter, author bio, forum comments)
      2. Remove duplicate URLs (same url, keep lowest chunk_idx batch)
    """
    print(f"\n  Auto-clean: {col}")
    print(f"  {'-'*50}")

    points = _load_all_points(client, col)
    if not points:
        print("  Collection is empty.")
        return {}

    # Step 1: Garbage detection
    garbage_by_label = {}
    garbage_ids = []
    for p in points:
        text = p.payload.get("text", "")
        url  = p.payload.get("url", "")
        label = _garbage_label(text, url)
        if label:
            garbage_ids.append(p.id)
            garbage_by_label[label] = garbage_by_label.get(label, 0) + 1

    if garbage_ids:
        _delete_ids(qdrant_url, col, garbage_ids)
        print(f"  Garbage removed: {len(garbage_ids)} chunks")
        for label, count in sorted(garbage_by_label.items(), key=lambda x: -x[1]):
            print(f"    {label:<20} {count:>5}")
    else:
        print(f"  Garbage: none found")

    # Reload after garbage removal
    points = _load_all_points(client, col)

    # Step 2: Duplicate URL detection
    url_points = {}
    for p in points:
        url = p.payload.get("url", "")
        if url not in url_points:
            url_points[url] = []
        url_points[url].append(p)

    dup_ids = []
    for url, pts in url_points.items():
        if len(pts) < 2:
            continue
        pts_sorted = sorted(pts, key=lambda p: p.payload.get("chunk_idx", 0))
        batches = [[pts_sorted[0]]]
        for i in range(1, len(pts_sorted)):
            prev_idx = pts_sorted[i-1].payload.get("chunk_idx", 0)
            curr_idx = pts_sorted[i].payload.get("chunk_idx", 0)
            if curr_idx - prev_idx > 50:
                batches.append([])
            batches[-1].append(pts_sorted[i])
        if len(batches) > 1:
            for batch in batches[1:]:
                dup_ids += [p.id for p in batch]

    if dup_ids:
        _delete_ids(qdrant_url, col, dup_ids)
        print(f"  Duplicate crawls removed: {len(dup_ids)} chunks")
    else:
        print(f"  Duplicates: none found")

    total = len(garbage_ids) + len(dup_ids)
    print(f"  {'-'*50}")
    print(f"  Total removed: {total} chunks  ({len(points) + len(garbage_ids)} -> {len(points) - len(dup_ids)})")

    return {
        "garbage": garbage_by_label,
        "garbage_total": len(garbage_ids),
        "duplicates": len(dup_ids),
        "total": total,
    }


# -- Pre-screening -------------------------------------------------------------

def _prescreen_urls(url_list: list) -> list:
    screened = []
    for url, points in url_list:
        domain = points[0].payload.get("domain", "?") if points else "?"
        trust  = points[0].payload.get("domain_trust", "unknown") if points else "unknown"
        title  = points[0].payload.get("title", "") if points else ""
        if title.startswith("http") or len(title) > 100:
            title = domain

        total   = len(points)
        garbage = sum(1 for p in points if _garbage_label(
            p.payload.get("text",""), p.payload.get("url","")
        ))
        pct = int(garbage / total * 100) if total else 0

        screened.append({
            "url":          url,
            "points":       points,
            "domain":       domain,
            "trust":        trust,
            "title":        title,
            "total":        total,
            "garbage":      garbage,
            "garbage_pct":  pct,
        })
    return screened


# -- URL list display ----------------------------------------------------------

def _show_url_list(screened: list, decisions: dict) -> None:
    print()
    for i, s in enumerate(screened, 1):
        trust_str = _TRUST_LABEL.get(s["trust"], s["trust"])
        dec       = decisions.get(i, "")
        dec_str   = f"  [{dec}]" if dec else ""

        if s["garbage_pct"] >= 80:
            g_str = f"  ! {s['garbage']}/{s['total']} garbage ({s['garbage_pct']}%)"
        elif s["garbage"] > 0:
            g_str = f"  {s['garbage']}/{s['total']} garbage"
        else:
            g_str = ""

        print(f"  [{i:>2}] {s['domain']:<30} [{trust_str}]  {s['total']:>3} chunks{g_str}{dec_str}")
        print(f"       {s['title'][:65]}")
        print()


# -- Execute decisions ---------------------------------------------------------

def _execute_decisions(client, col, screened, decisions, category,
                       topic_slug, stats, qdrant_url, auto_skip_community):
    now_iso = datetime.now(timezone.utc).isoformat()

    for i, s in enumerate(screened, 1):
        dec = decisions.get(i, "")

        if not dec and auto_skip_community and s["trust"] in ("community", "unknown"):
            dec = "S"
        if not dec:
            dec = "S"

        url    = s["url"]
        points = s["points"]
        domain = s["domain"]

        if dec == "X":
            ids = [p.id for p in points]
            _delete_ids(qdrant_url, col, ids)
            stats["deleted"] += len(ids)
            print(f"  x [{i}] {domain} -- deleted {len(ids)} chunks")

        elif dec in ("K", "E"):
            evergreen  = (dec == "E")
            target_col = "knowledge_evergreen" if evergreen else f"knowledge_{category}"
            _ensure_collection(client, target_col)

            chunk_edits   = s.get("chunk_edits", {})
            chunk_deleted = s.get("chunk_deleted", set())

            promoted = 0
            for p in points:
                if p.id in chunk_deleted:
                    _delete_ids(qdrant_url, col, [p.id])
                    stats["deleted"] += 1
                    continue
                new_text = chunk_edits.get(p.id, p.payload.get("text", ""))
                full = _fetch_with_vectors(qdrant_url, col, p.id)
                if not full:
                    continue
                payload = dict(full.get("payload", {}))
                payload["knowledge"]   = True
                payload["evergreen"]   = evergreen
                payload["accepted_at"] = now_iso
                payload["text"]        = _clean_markdown(new_text)
                payload["topic_slug"]  = topic_slug
                payload.pop("expires_at", None)
                payload.pop("candidate_score", None)
                ok = _upsert_point(qdrant_url, target_col, {
                    "id":      full["id"],
                    "vector":  full["vector"],
                    "payload": payload,
                })
                if ok:
                    promoted += 1

            ev_tag = " [EVERGREEN]" if evergreen else ""
            print(f"  v [{i}] {domain} -> {target_col}{ev_tag}  ({promoted} chunks)")
            if evergreen:
                stats["evergreen"] += promoted
            else:
                stats["knowledge"] += promoted

        else:  # S = skip
            stats["skipped"] += len(points)
            print(f"  - [{i}] {domain} skipped ({len(points)} chunks)")


# -- Chunk review for one URL --------------------------------------------------

def _print_chunk_full(ch, i):
    """Print one chunk with full text, wrapped at 90 chars."""
    pay  = ch["point"].payload
    text = ch["text"]
    g    = _garbage_label(text, pay.get("url", ""))

    flags = []
    if ch["deleted"]: flags.append("x SKIP")
    if ch["edited"]:  flags.append("[e] EDITED")
    if g:             flags.append(f"! {g}")
    flag_str = "  " + "  ".join(flags) if flags else ""

    print(f"  -- C{i:02d} -- {len(text)}ch{flag_str}")
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
    print()


def _review_url_chunks(client, col, s, category, url_num, url_total,
                       qdrant_url, topic_slug) -> str:
    """
    Review chunks for one URL -- new unified flow.
    Full text always visible. k / k -N / e / e -N / x / s / n / b / q
    Returns decision: K / E / X / S / Q (quit) / "" (next)
    """
    if "chunk_edits"   not in s: s["chunk_edits"]   = {}
    if "chunk_deleted" not in s: s["chunk_deleted"]  = set()

    chunks = [
        {
            "point":   p,
            "text":    s["chunk_edits"].get(p.id, p.payload.get("text", "")),
            "deleted": p.id in s["chunk_deleted"],
            "edited":  p.id in s["chunk_edits"],
        }
        for p in s["points"]
    ]

    # Auto-mark garbage chunks
    for ch in chunks:
        if _garbage_label(ch["text"], ch["point"].payload.get("url", "")):
            ch["deleted"] = True

    def _show():
        trust_str = _TRUST_LABEL.get(s["trust"], s["trust"])
        n_del = sum(1 for c in chunks if c["deleted"])
        print()
        print(f"  {'='*60}")
        print(f"  [{url_num}/{url_total}] {s['domain']}  [{trust_str}]  {len(chunks)} chunks")
        print(f"  {s['title'][:70]}")
        if n_del:
            print(f"  {n_del} chunk(s) auto-flagged as garbage (will be skipped on promote)")
        print(f"  {'='*60}")
        print()
        for i, ch in enumerate(chunks, 1):
            _print_chunk_full(ch, i)
        print(f"  {'-'*60}")
        print(f"  k          promote all to knowledge_{category}")
        print(f"  k -1 -3    promote all EXCEPT C01 and C03")
        print(f"  e          promote all to evergreen")
        print(f"  e -1       promote all to evergreen EXCEPT C01")
        print(f"  x          delete entire URL")
        print(f"  s          skip URL")
        print(f"  n / Enter  next URL")
        print(f"  b          back to URL list")
        print(f"  q          quit review")
        print()

    _show()

    while True:
        raw   = input("  > ").strip()
        parts = raw.split()
        cmd0  = parts[0].lower() if parts else ""

        if not raw or cmd0 == "n":
            return ""
        if cmd0 == "b":
            return "BACK"
        if cmd0 == "q":
            return "Q"
        if cmd0 == "r":
            _show()
            continue

        # k / e with optional exclusions: k -1 -3 -5
        if cmd0 in ("k", "e") and all(
            p.startswith("-") and p[1:].lstrip("cC").isdigit() for p in parts[1:]
        ):
            excluded = set()
            for token in parts[1:]:
                n = int(token.lstrip("-").lstrip("cC")) - 1
                if 0 <= n < len(chunks):
                    excluded.add(n)

            # Apply deletions and edits to s dict
            for i, ch in enumerate(chunks):
                pid = ch["point"].id
                if i in excluded or ch["deleted"]:
                    s["chunk_deleted"].add(pid)
                elif ch["edited"]:
                    s["chunk_edits"][pid] = ch["text"]

            if excluded:
                print(f"  Excluding C{sorted(n+1 for n in excluded)} from promote")
            return cmd0.upper()

        if cmd0 == "x":
            confirm = input(f"  Delete all {len(chunks)} chunks from {s['domain']}? [y/N]: ").strip().lower()
            if confirm in ("y", "yes"):
                return "X"
            continue

        if cmd0 == "s":
            return "S"

        print("  k / k -1 -3 / e / e -1 / x / s / n / b / q")


# -- Review item ---------------------------------------------------------------

def _review_item(client, col, iid, data, item_num, item_total, qdrant_url):
    """Review all URLs for one item -- sequential flow, no separate bulk view."""
    topic    = data["topic"]
    category = data["category"]

    topic_slug = ""
    try:
        all_items = queue.get_all()
        for qi in all_items:
            if qi.get("id", "") == iid:
                topic_slug = qi.get("slug", "") or qi.get("topic_slug", "") or ""
                break
    except Exception:
        pass

    url_list = list(data["urls"].items())
    screened = _prescreen_urls(url_list)

    # Auto-skip community/unknown
    community_count = sum(1 for s in screened if s["trust"] in ("community", "unknown"))
    auto_skip_community = False
    if community_count > 0:
        print()
        print(f"  {community_count} community/unknown URLs found.")
        ans = input(f"  Auto-skip community/unknown URLs? [y/N]: ").strip().lower()
        auto_skip_community = (ans == "y")

    print()
    print(f"  {'='*60}")
    print(f"  Item {item_num}/{item_total} -- {topic[:55]}")
    print(f"  category={category}  slug={topic_slug or '(none)'}  {len(screened)} URLs")
    print(f"  {'='*60}")

    stats     = {"knowledge": 0, "evergreen": 0, "deleted": 0, "skipped": 0}
    decisions = {}

    # Show URL overview before starting
    print()
    _show_url_list(screened, decisions)
    print(f"  Press Enter to start reviewing URLs one by one, or [q] to quit.")
    print()
    if input("  > ").strip().lower() == "q":
        return

    now_iso = datetime.now(timezone.utc).isoformat()

    def _execute_one(s, dec):
        """Execute decision for one URL immediately."""
        domain = s["domain"]
        points = s["points"]
        chunk_edits   = s.get("chunk_edits", {})
        chunk_deleted = s.get("chunk_deleted", set())

        if dec == "X":
            ids = [p.id for p in points]
            _delete_ids(qdrant_url, col, ids)
            stats["deleted"] += len(ids)
            print(f"  x {domain} -- deleted {len(ids)} chunks")

        elif dec in ("K", "E"):
            evergreen  = (dec == "E")
            target_col = "knowledge_evergreen" if evergreen else f"knowledge_{category}"
            _ensure_collection(client, target_col)
            promoted = 0
            for p in points:
                if p.id in chunk_deleted:
                    _delete_ids(qdrant_url, col, [p.id])
                    stats["deleted"] += 1
                    continue
                new_text = chunk_edits.get(p.id, p.payload.get("text", ""))
                full = _fetch_with_vectors(qdrant_url, col, p.id)
                if not full:
                    continue
                payload = dict(full.get("payload", {}))
                payload["knowledge"]   = True
                payload["evergreen"]   = evergreen
                payload["accepted_at"] = now_iso
                payload["text"]        = _clean_markdown(new_text)
                payload["topic_slug"]  = topic_slug
                payload.pop("expires_at", None)
                payload.pop("candidate_score", None)
                ok = _upsert_point(qdrant_url, target_col, {
                    "id":      full["id"],
                    "vector":  full["vector"],
                    "payload": payload,
                })
                if ok:
                    promoted += 1
            ev_tag = " [evergreen]" if evergreen else ""
            print(f"  k {domain} -> {target_col}{ev_tag} ({promoted} chunks)")
            if evergreen:
                stats["evergreen"] += promoted
            else:
                stats["knowledge"] += promoted

        else:  # S
            stats["skipped"] += len(points)

    idx = 0
    while idx < len(screened):
        s = screened[idx]

        if auto_skip_community and s["trust"] in ("community", "unknown"):
            stats["skipped"] += len(s["points"])
            idx += 1
            continue

        dec = _review_url_chunks(
            client, col, s, category, idx + 1, len(screened),
            qdrant_url, topic_slug,
        )

        if dec == "Q":
            break
        if dec == "BACK":
            idx = max(0, idx - 1)
            continue
        if dec and dec not in ("BACK", "Q"):
            _execute_one(s, dec)

        idx += 1

    print()
    print(f"  -- done: k={stats['knowledge']} e={stats['evergreen']} x={stats['deleted']} s={stats['skipped']}")
    print()
    input("  [Enter] continue ->")


# -- Inspect & Promote entry point ---------------------------------------------

def _inspect_and_promote(client) -> None:
    """[3] Inspect & Promote -- full pipeline with auto-clean and pre-screening."""
    from config import QDRANT_URL

    try:
        all_cols     = [c.name for c in client.get_collections().collections]
        research_cols = sorted([c for c in all_cols if c.startswith("research_")])
        persona_cols  = sorted([c for c in all_cols if c.startswith("persona_")])
    except Exception as e:
        print(f"  x Qdrant error: {e}")
        return

    inspectable = [c for c in research_cols + persona_cols
                   if client.get_collection(c).points_count > 0]
    if not inspectable:
        print("  No collections with data.")
        return

    print()
    print("  Select collection:")
    for i, col in enumerate(inspectable, 1):
        n = client.get_collection(col).points_count
        print(f"  [{i:>2}] {col:<35} {n:>6} chunks")
    print()
    raw = input("  > ").strip()
    if not raw.isdigit() or not (1 <= int(raw) <= len(inspectable)):
        return
    col = inspectable[int(raw) - 1]

    print()
    confirm = input(f"  Run auto-clean on {col} before review? [Y/n]: ").strip().lower()
    if confirm != "n":
        _auto_clean(client, col, QDRANT_URL)
        input("  [Enter] to continue to review ->")

    print(f"\n  Loading {col}...")
    all_points = _load_all_points(client, col)
    if not all_points:
        print("  No chunks remaining after auto-clean.")
        return
    print(f"  {len(all_points)} chunks loaded.")

    items = {}
    for p in all_points:
        pay = p.payload
        iid = pay.get("item_id", "unknown")
        url = pay.get("url", "")
        if iid not in items:
            items[iid] = {
                "topic":    pay.get("topic", ""),
                "category": pay.get("category", "other"),
                "urls":     {},
            }
        if url not in items[iid]["urls"]:
            items[iid]["urls"][url] = []
        items[iid]["urls"][url].append(p)

    for iid in items:
        for url in items[iid]["urls"]:
            items[iid]["urls"][url].sort(key=lambda p: p.payload.get("chunk_idx", 0))

    item_list = sorted(
        items.items(),
        key=lambda x: sum(len(v) for v in x[1]["urls"].values()),
        reverse=True,
    )

    while True:
        print()
        print(f"  {'='*60}")
        print(f"  {col} -- {len(item_list)} items")
        print(f"  {'='*60}")
        print()
        for i, (iid, data) in enumerate(item_list, 1):
            total  = sum(len(v) for v in data["urls"].values())
            n_urls = len(data["urls"])
            topic  = data["topic"][:55]
            dom_counts = {}
            for url, pts in data["urls"].items():
                d = pts[0].payload.get("domain", "?") if pts else "?"
                dom_counts[d] = dom_counts.get(d, 0) + len(pts)
            top = sorted(dom_counts.items(), key=lambda x: -x[1])[:3]
            dom_str = "  ".join(f"{d}({c})" for d, c in top)
            print(f"  [{i}]  {topic:<55} {total:>5} chunks  {n_urls} URLs")
            print(f"        {dom_str}")
            print()

        print(f"  [a]  Review all items sequentially")
        print(f"  [Enter]  Back")
        print()
        cmd = input("  > ").strip().lower()

        if cmd in ("", "q", "back"):
            return
        if cmd == "a":
            for i, (iid, data) in enumerate(item_list):
                _review_item(client, col, iid, data, i+1, len(item_list), QDRANT_URL)
            return
        if cmd.isdigit() and 1 <= int(cmd) <= len(item_list):
            idx = int(cmd) - 1
            iid, data = item_list[idx]
            _review_item(client, col, iid, data, idx+1, len(item_list), QDRANT_URL)
            return _inspect_and_promote(client)
        print("  Unknown option.")
