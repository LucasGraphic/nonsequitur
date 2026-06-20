# menus/knowledge.py -- Knowledge base menu v2
# Auto-clean before review, pre-screening, bulk decisions, trust-based auto-skip
import os
import sys
import re
import json
import tempfile
import subprocess
from datetime import datetime, timezone

import core.queue as queue


try:
    from knowledge.knowledge_chunker import (
        slug_autocomplete, tag_autocomplete, prompt_slug_and_tags,
    )
    _chunker_available = True
except ImportError:
    _chunker_available = False


# -- Garbage patterns ----------------------------------------------------------

_GARBAGE_PATTERNS = [
    # GDPR / TCF consent walls
    (r"tcf vendors",                                     "GDPR"),
    (r"manage your choices",                             "GDPR"),
    (r"view details consent \(\d+ vendors\)",            "GDPR"),
    (r"vendors want your permission",                    "GDPR"),
    (r"store and/or access information on a device",     "GDPR"),
    (r"personalised advertising and content.*measurement","GDPR"),
    (r"your personal data will be processed",            "GDPR"),
    (r"accept all cookies",                              "GDPR"),
    (r"cookie consent",                                  "GDPR"),
    (r"continue to site",                                "GDPR"),
    (r"legitimate interest \(\d+ vendors\)",             "GDPR"),
    # Newsletter signup
    (r"contact me with news and offers from other",      "NEWSLETTER"),
    (r"receive email from us on behalf of our trusted",  "NEWSLETTER"),
    (r"by submitting your information you agree to the terms", "NEWSLETTER"),
    (r"your newsletter sign-up was successful",          "NEWSLETTER"),
    (r"subscribe \+ every (friday|thursday|monday|tuesday|wednesday|saturday|sunday)", "NEWSLETTER"),
    (r"unlock instant access to exclusive member",       "NEWSLETTER"),
    (r"become a member in seconds",                      "NEWSLETTER"),
    # Author bio
    (r"contributing (writer|editor) at",                 "AUTHOR_BIO"),
    (r"50% pizza by volume",                             "AUTHOR_BIO"),
    (r"mmo raider by day",                               "AUTHOR_BIO"),
    (r"he has been gaming on pcs from the very beginning","AUTHOR_BIO"),
    (r"when not wr(iting|apping)",                       "AUTHOR_BIO"),
    (r"although his background is in legal",             "AUTHOR_BIO"),
    (r"horror game enthusiast with a deep admiration",   "AUTHOR_BIO"),
    (r"andy has been gaming on pcs",                     "AUTHOR_BIO"),
    # Discourse forum comments (Username Month Year format)
    (r"\w+\s+(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\s+20\d\d\b", "FORUM_COMMENT"),
    (r"praetorian\s+(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)",      "FORUM_COMMENT"),
    (r"initiate\s+(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)",        "FORUM_COMMENT"),
    (r"\d+ replies.*continue this thread",               "FORUM_COMMENT"),
    (r"more replies.*continue this thread",              "FORUM_COMMENT"),
    # Reddit comments
    (r"\w+ \u2022 \d+mo ago",                            "REDDIT_COMMENT"),
    (r"\w+ \u2022 \d+[ywdh] ago",                        "REDDIT_COMMENT"),
    (r"upvotes \u00b7 \d+ comments",                     "REDDIT_COMMENT"),
    (r"continue this thread",                            "REDDIT_COMMENT"),
    # Navigation / sidebar
    (r"when you (purchase|buy) through links",           "AFFILIATE"),
    (r"earn a commission",                               "AFFILIATE"),
    (r"we sometimes include affiliate links",            "AFFILIATE"),
    # JavaScript
    (r"function\s*\(\s*\)\s*\{",                         "JS_CODE"),
    (r"document\.cookie",                                "JS_CODE"),
    (r"window\.location",                                "JS_CODE"),
    # Newsletter traps
    (r"please leave this field empty",               "NEWSLETTER"),
    (r"gear up for latest news",                     "NEWSLETTER"),
    (r"check your inbox or spam folder to confirm",  "NEWSLETTER"),
    (r"get exclusive gaming.*news before it drops",  "NEWSLETTER"),
    # Cookie tables
    (r"cookie.*duration.*description",               "COOKIE_TABLE"),
    (r"necessary.*always active.*necessary cookies", "COOKIE_TABLE"),
    (r"analytical cookies are used to understand",   "COOKIE_TABLE"),
    (r"powered by customise consent preferences",    "COOKIE_TABLE"),
    (r"having trouble with this popup",              "COOKIE_TABLE"),
    # Site footers
    (r"valnet publishing group",                     "FOOTER"),
    (r"follow wccftech on google",                   "FOOTER"),
    (r"load comments.*further reading",              "FOOTER"),
    (r"is part of the valnet",                       "FOOTER"),
    (r"affiliate disclosure.*work with us",          "FOOTER"),
    (r"about us.*editorial guidelines.*our team",    "FOOTER"),
    (r"join our community.*fans.*followers",         "FOOTER"),
    (r"was our article helpful",                     "FOOTER"),
]

_DOMAIN_BLACKLIST = {
    "chinahighlights.com", "theworldfactbook.org", "worldatlas.com",
}

_TRUST_LABEL = {
    "press":     "\u2605 press",
    "trusted":   "\u2713 trusted",
    "community": "~ community",
    "unknown":   "? unknown",
    "forum":     "~ forum",
}


def _garbage_label(text: str, url: str = "") -> str:
    """Return garbage label or empty string."""
    if len(text) < 80:
        return "TOO_SHORT"
    if url:
        try:
            from urllib.parse import urlparse as _up
            d = _up(url).netloc.lower().lstrip("www.")
            if d in _DOMAIN_BLACKLIST:
                return "BLACKLIST"
        except Exception:
            pass
    t = text.lower()
    for pat, label in _GARBAGE_PATTERNS:
        if re.search(pat, t):
            return label
    return ""


def _clean_markdown(text: str) -> str:
    """Strip markdown formatting -- applied automatically on every promote."""
    text = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', text)   # [label](url) -> label
    text = re.sub(r'\[\[\d+\]\]', '', text)                  # [[N]] footnotes
    text = re.sub(r'\[\d+\]', '', text)                      # [N] citations
    text = re.sub(r'\*\*([^*]+)\*\*', r'\1', text)           # **bold**
    text = re.sub(r'\_([^_]+)\_', r'\1', text)               # _italic_
    text = re.sub(r'\*([^*]+)\*', r'\1', text)               # *italic*
    text = re.sub(r'^#{1,6}\s+', '', text, flags=re.MULTILINE)
    text = re.sub(r'!\[[^\]]*\]\([^)]+\)', '', text)         # images
    text = re.sub(r'<https?://[^>]+>', '', text)             # bare URLs
    text = re.sub(r'\n{3,}', '\n\n', text)
    text = re.sub(r' {2,}', ' ', text)
    return text.strip()


# -- Qdrant helpers ------------------------------------------------------------

def _ensure_collection(client, name: str, dim: int = 4096) -> None:
    created = False
    try:
        client.get_collection(name)
    except Exception:
        import requests as _req
        from config import QDRANT_URL
        payload = {
            "vectors":        {"dense":  {"size": dim, "distance": "Cosine"}},
            "sparse_vectors": {"sparse": {"index": {"on_disk": False}}},
        }
        _req.put(f"{QDRANT_URL}/collections/{name}", json=payload, timeout=10)
        created = True

    if created and name.startswith("knowledge_"):
        # Create payload indexes for fast filtered retrieval
        try:
            from qdrant_client.models import PayloadSchemaType
            client.create_payload_index(name, "topic_slug", PayloadSchemaType.KEYWORD)
            client.create_payload_index(name, "category",   PayloadSchemaType.KEYWORD)
            client.create_payload_index(name, "evergreen",  PayloadSchemaType.BOOL)
        except Exception as e:
            pass  # Indexes are optional -- retrieval still works without them


def _fetch_with_vectors(qdrant_url: str, collection: str, point_id) -> dict | None:
    import requests as _req
    try:
        resp = _req.post(
            f"{qdrant_url}/collections/{collection}/points",
            json={"ids": [point_id], "with_payload": True, "with_vectors": True},
            timeout=10,
        )
        data = resp.json().get("result", [])
        return data[0] if data else None
    except Exception as e:
        print(f"  [fetch] Error: {e}")
        return None


def _upsert_point(qdrant_url: str, collection: str, point: dict) -> bool:
    import requests as _req
    try:
        resp = _req.put(
            f"{qdrant_url}/collections/{collection}/points",
            json={"points": [point]},
            timeout=30,
        )
        return resp.status_code in (200, 201)
    except Exception as e:
        print(f"  [upsert] Error: {e}")
        return False


def _delete_ids(qdrant_url: str, collection: str, ids: list) -> None:
    import requests as _req
    if not ids:
        return
    try:
        _req.post(
            f"{qdrant_url}/collections/{collection}/points/delete",
            json={"points": ids},
            timeout=10,
        )
    except Exception:
        pass


def _load_all_points(client, col: str) -> list:
    """Load all points from collection with pagination."""
    pts = []
    offset = None
    while True:
        result = client.scroll(
            collection_name=col, limit=200, offset=offset,
            with_payload=True, with_vectors=False,
        )
        batch, offset = result
        pts.extend(batch)
        if offset is None:
            break
    return pts


# -- Notepad editor ------------------------------------------------------------

def _edit_notepad(text: str, label: str = "chunk") -> str | None:
    tmp = tempfile.NamedTemporaryFile(
        mode="w", suffix=".txt", prefix=f"chunk_{label}_",
        delete=False, encoding="utf-8"
    )
    tmp.write(text)
    tmp.flush()
    tmp.close()
    path = tmp.name
    print(f"  Opening Notepad -- save and close to continue...")
    try:
        subprocess.call(["notepad.exe", path])
    except FileNotFoundError:
        try:
            subprocess.call(["notepad", path])
        except Exception:
            print("  notepad.exe not found.")
            os.unlink(path)
            return None
    try:
        with open(path, encoding="utf-8") as f:
            result = f.read().strip()
        os.unlink(path)
        return result if result else None
    except Exception as e:
        print(f"  Read error: {e}")
        return None


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
    # Group by url -- if same url appears with chunk_idx in two separate ranges,
    # it's a duplicate crawl. Keep the first range (lowest min chunk_idx).
    url_batches = {}  # url -> list of (min_idx, [point_ids])
    url_points  = {}  # url -> [points]
    for p in points:
        url = p.payload.get("url", "")
        if url not in url_points:
            url_points[url] = []
        url_points[url].append(p)

    dup_ids = []
    for url, pts in url_points.items():
        if len(pts) < 2:
            continue
        # Sort by chunk_idx
        pts_sorted = sorted(pts, key=lambda p: p.payload.get("chunk_idx", 0))
        # Detect gap -- if chunk_idx jumps by >50, it's a duplicate crawl
        batches = [[pts_sorted[0]]]
        for i in range(1, len(pts_sorted)):
            prev_idx = pts_sorted[i-1].payload.get("chunk_idx", 0)
            curr_idx = pts_sorted[i].payload.get("chunk_idx", 0)
            if curr_idx - prev_idx > 50:
                batches.append([])
            batches[-1].append(pts_sorted[i])
        # Keep first batch, mark rest as duplicates
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
    """
    Analyse each URL before review.
    Returns enriched list of dicts with garbage_count, garbage_pct, trust.
    """
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
    """Display pre-screened URL list with garbage info and pending decisions."""
    print()
    for i, s in enumerate(screened, 1):
        trust_str = _TRUST_LABEL.get(s["trust"], s["trust"])
        dec       = decisions.get(i, "")
        dec_str   = f"  [{dec}]" if dec else ""

        # Garbage indicator
        if s["garbage_pct"] >= 80:
            g_str = f"  \u26a0 {s['garbage']}/{s['total']} garbage ({s['garbage_pct']}%)"
        elif s["garbage"] > 0:
            g_str = f"  {s['garbage']}/{s['total']} garbage"
        else:
            g_str = ""

        print(f"  [{i:>2}] {s['domain']:<30} [{trust_str}]  {s['total']:>3} chunks{g_str}{dec_str}")
        print(f"       {s['title'][:65]}")
        print()


# -- Inspect & Promote ---------------------------------------------------------

def _inspect_and_promote(client) -> None:
    """[3] Inspect & Promote -- full pipeline with auto-clean and pre-screening."""
    from config import QDRANT_URL

    try:
        all_cols     = [c.name for c in client.get_collections().collections]
        research_cols = sorted([c for c in all_cols if c.startswith("research_")])
        persona_cols  = sorted([c for c in all_cols if c.startswith("persona_")])
    except Exception as e:
        print(f"  \u2717 Qdrant error: {e}")
        return

    inspectable = [c for c in research_cols + persona_cols
                   if client.get_collection(c).points_count > 0]
    if not inspectable:
        print("  No collections with data.")
        return

    # Select collection
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

    # Step 1: Auto-clean (mandatory)
    print()
    confirm = input(f"  Run auto-clean on {col} before review? [Y/n]: ").strip().lower()
    if confirm != "n":
        _auto_clean(client, col, QDRANT_URL)
        input("  [Enter] to continue to review ->")

    # Load all points
    print(f"\n  Loading {col}...")
    all_points = _load_all_points(client, col)
    if not all_points:
        print("  No chunks remaining after auto-clean.")
        return
    print(f"  {len(all_points)} chunks loaded.")

    # Group by item_id
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

    # Sort chunks by chunk_idx ASC within each URL
    for iid in items:
        for url in items[iid]["urls"]:
            items[iid]["urls"][url].sort(key=lambda p: p.payload.get("chunk_idx", 0))

    item_list = sorted(
        items.items(),
        key=lambda x: sum(len(v) for v in x[1]["urls"].values()),
        reverse=True,
    )

    # Item selection loop
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


def _review_item(client, col, iid, data, item_num, item_total, qdrant_url):
    """Review all URLs for one item with pre-screening and bulk decisions."""
    topic    = data["topic"]
    category = data["category"]

    # Get topic_slug from queue
    topic_slug = ""
    try:
        all_items = queue.get_all()
        for qi in all_items:
            if qi.get("id", "") == iid:
                topic_slug = qi.get("slug", "") or qi.get("topic_slug", "") or ""
                break
    except Exception:
        pass

    # Pre-screen URLs
    url_list  = list(data["urls"].items())
    screened  = _prescreen_urls(url_list)

    # Auto-skip prompt
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
    decisions = {}  # url_num -> decision string for display

    # URL list loop with bulk decisions
    while True:
        _show_url_list(screened, decisions)
        print(f"  -- Bulk decisions ---------------------------------------")
        print(f"  [N]      enter URL N to review chunks")
        print(f"  [k N]    promote URL N -> knowledge_{category}")
        print(f"  [e N]    promote URL N -> evergreen")
        print(f"  [x N]    delete URL N")
        print(f"  [x N-M]  delete URL range N to M")
        print(f"  [s N]    skip URL N")
        print(f"  [go]     execute all pending decisions and finish item")
        print(f"  [q]      quit review")
        print()

        raw = input("  > ").strip().lower()

        if raw in ("q", "quit"):
            return

        if raw == "go":
            _execute_decisions(client, col, screened, decisions, category,
                               topic_slug, stats, qdrant_url, auto_skip_community)
            break

        # Bulk: k/e/x/s N  or  k/e/x/s N M P  or  x N-M
        parts = raw.split()
        if len(parts) >= 2 and parts[0].lower() in ("k", "e", "x", "s"):
            action = parts[0].lower()
            idxs = []
            for token in parts[1:]:
                import re as _reb
                m = _reb.match(r"^(\d+)-(\d+)$", token)
                if m:
                    idxs += list(range(int(m.group(1)), int(m.group(2)) + 1))
                elif token.isdigit():
                    idxs.append(int(token))
            if not idxs:
                print("  Invalid number.")
                continue
            for n in idxs:
                if 1 <= n <= len(screened):
                    decisions[n] = action.upper()
                    print(f"  \u2713 URL {n} marked [{action.upper()}]")
                else:
                    print(f"  URL {n} out of range")
            continue
            for n in idxs:
                if 1 <= n <= len(screened):
                    decisions[n] = action.upper()
                    print(f"  \u2713 URL {n} marked [{action.upper()}]")
                else:
                    print(f"  URL {n} out of range")
            continue

        # Enter URL N for chunk review
        if raw.isdigit() and 1 <= int(raw) <= len(screened):
            n = int(raw)
            s = screened[n - 1]
            if auto_skip_community and s["trust"] in ("community", "unknown"):
                print(f"  Auto-skipping {s['domain']} [{s['trust']}]")
                decisions[n] = "S"
                continue
            dec = _review_url_chunks(
                client, col, s, category, n, len(screened),
                qdrant_url, topic_slug,
            )
            if dec:
                decisions[n] = dec
            if dec == "Q":
                return
            continue

        print("  k/e/x/s N = bulk decision | N = review chunks | go = execute | q = quit")

    # Summary
    print()
    print(f"  -- Item complete ----------------------------------------")
    print(f"  \u2192 knowledge_{category}: {stats['knowledge']} chunks")
    print(f"  \u2192 knowledge_evergreen:  {stats['evergreen']} chunks")
    print(f"  \u2192 deleted:              {stats['deleted']} chunks")
    print(f"  \u2192 skipped:              {stats['skipped']} chunks")
    print()
    input("  [Enter] continue \u2192")


def _execute_decisions(client, col, screened, decisions, category,
                       topic_slug, stats, qdrant_url, auto_skip_community):
    """Execute all bulk decisions + auto-skip."""
    now_iso = datetime.now(timezone.utc).isoformat()

    for i, s in enumerate(screened, 1):
        dec = decisions.get(i, "")

        # Auto-skip community if enabled
        if not dec and auto_skip_community and s["trust"] in ("community", "unknown"):
            dec = "S"

        # Default: skip if no decision
        if not dec:
            dec = "S"

        url    = s["url"]
        points = s["points"]
        domain = s["domain"]

        if dec == "X":
            ids = [p.id for p in points]
            _delete_ids(qdrant_url, col, ids)
            stats["deleted"] += len(ids)
            print(f"  \u2717 [{i}] {domain} -- deleted {len(ids)} chunks")

        elif dec in ("K", "E"):
            evergreen  = (dec == "E")
            target_col = "knowledge_evergreen" if evergreen else f"knowledge_{category}"
            _ensure_collection(client, target_col)

            # Use working copy if chunks were edited during review
            chunk_edits = s.get("chunk_edits", {})
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
            print(f"  \u2713 [{i}] {domain} \u2192 {target_col}{ev_tag}  ({promoted} chunks)")
            if evergreen:
                stats["evergreen"] += promoted
            else:
                stats["knowledge"] += promoted

        else:  # S = skip
            stats["skipped"] += len(points)
            print(f"  \u2013 [{i}] {domain} skipped ({len(points)} chunks)")


def _review_url_chunks(client, col, s, category, url_num, url_total,
                       qdrant_url, topic_slug) -> str:
    """
    Review chunks for one URL.
    Returns decision: K / E / X / S / Q (quit)
    Edits and deletions stored back in s dict.
    """
    if "chunk_edits"   not in s: s["chunk_edits"]   = {}
    if "chunk_deleted" not in s: s["chunk_deleted"]  = set()

    # Working copy
    chunks = [
        {
            "point":   p,
            "text":    s["chunk_edits"].get(p.id, p.payload.get("text", "")),
            "deleted": p.id in s["chunk_deleted"],
            "edited":  p.id in s["chunk_edits"],
        }
        for p in s["points"]
    ]

    def _show():
        trust_str = _TRUST_LABEL.get(s["trust"], s["trust"])
        print()
        print(f"  {'-'*60}")
        print(f"  URL {url_num}/{url_total} -- {s['domain']}  [{trust_str}]")
        print(f"  {s['title'][:70]}")
        print(f"  {s['url'][:80]}")
        print(f"  {len(chunks)} chunks  |  marked for deletion: {sum(1 for c in chunks if c['deleted'])}")
        print()

        for i, ch in enumerate(chunks, 1):
            pay   = ch["point"].payload
            cidx  = pay.get("chunk_idx", "?")
            score = pay.get("trust_score", 0)
            trust = pay.get("domain_trust", "?")
            text  = ch["text"]
            url_s = pay.get("url", "")[:75]
            g     = _garbage_label(text, pay.get("url", ""))

            markers = []
            if ch["deleted"]: markers.append("\u2717 DELETE")
            if ch["edited"]:  markers.append("\u270e EDITED")
            if g:             markers.append(f"\u26a0 {g}")
            mstr = "  " + "  ".join(markers) if markers else ""

            print(f"  [C{i:02d}] chunk_idx={cidx}  score={score:.2f}  trust={trust}{mstr}")
            print(f"  {url_s}")
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

        print(f"  {'-'*60}")
        print(f"  [K] knowledge_{category}  [E] evergreen  [X] delete URL  [S] skip")
        print(f"  [d N-M] mark range  [df N] delete from N  [dc] forum/comments  [da] all garbage")
        print(f"  [u N] unmark  [e N] edit  [v N] preview  [r] show again")
        print(f"  [n] next  [b] back  [q] quit")
        print()

    _show()

    while True:
        raw   = input("  > ").strip()
        parts = raw.split()
        cmd0  = parts[0].lower() if parts else ""

        # Navigation / decisions that return
        if not raw or cmd0 == "n":
            return ""
        if cmd0 == "b":
            return "BACK"
        if cmd0 == "q":
            return "Q"
        if cmd0 == "r":
            _show()
            continue

        # Final decisions
        if cmd0 in ("k", "e", "x", "s") and len(parts) == 1:
            # Store chunk edits/deletions back into s
            for ch in chunks:
                pid = ch["point"].id
                if ch["deleted"]:
                    s["chunk_deleted"].add(pid)
                elif ch["edited"]:
                    s["chunk_edits"][pid] = ch["text"]
            dec = cmd0.upper()
            if dec == "X":
                confirm = input(f"  Delete all {len(chunks)} chunks from {s['domain']}? [y/N]: ").strip().lower()
                if confirm not in ("y", "yes"):
                    continue
            return dec

        # Mark range: d N-M or d N
        if cmd0 == "d" and len(parts) > 1:
            indices = []
            for token in parts[1:]:
                token = token.lstrip("cC")
                m = re.match(r"^(\d+)-(\d+)$", token)
                if m:
                    indices += list(range(int(m.group(1))-1, int(m.group(2))))
                elif token.isdigit():
                    indices.append(int(token)-1)
            for n in indices:
                if 0 <= n < len(chunks):
                    chunks[n]["deleted"] = True
                    preview = chunks[n]["text"][:45].replace("\n"," ")
                    print(f"  \u2713 C{n+1:02d} marked \u2014 \"{preview}...\"")
                else:
                    print(f"  C{n+1:02d} out of range")
            continue

        # df N -- delete from N to end
        if cmd0 == "df" and len(parts) > 1:
            token = parts[1].lstrip("cC")
            if token.isdigit():
                start = int(token) - 1
                count = 0
                for n in range(start, len(chunks)):
                    chunks[n]["deleted"] = True
                    count += 1
                print(f"  \u2713 Marked C{start+1:02d}\u2013C{len(chunks):02d} ({count} chunks)")
            continue

        # dc -- delete forum/comment chunks
        if cmd0 == "dc":
            count = 0
            for ch in chunks:
                label = _garbage_label(ch["text"], ch["point"].payload.get("url",""))
                if label in ("FORUM_COMMENT", "REDDIT_COMMENT", "AUTHOR_BIO", "NEWSLETTER"):
                    ch["deleted"] = True
                    count += 1
            print(f"  \u2713 Marked {count} comment/noise chunks" if count else "  No comment chunks detected")
            continue

        # da -- delete all garbage
        if cmd0 == "da":
            count = 0
            for ch in chunks:
                if _garbage_label(ch["text"], ch["point"].payload.get("url","")):
                    ch["deleted"] = True
                    count += 1
            print(f"  \u2713 Marked {count} garbage chunks" if count else "  No garbage chunks detected")
            continue

        # u N -- unmark
        if cmd0 == "u" and len(parts) > 1:
            for token in parts[1:]:
                token = token.lstrip("cC")
                if token.isdigit():
                    n = int(token) - 1
                    if 0 <= n < len(chunks):
                        chunks[n]["deleted"] = False
                        print(f"  \u2713 C{n+1:02d} unmarked")
            continue

        # e N -- edit in Notepad
        if cmd0 == "e" and len(parts) > 1:
            token = parts[1].lstrip("cC")
            if token.isdigit():
                n = int(token) - 1
                if 0 <= n < len(chunks):
                    chunks[n]["deleted"] = False
                    new_text = _edit_notepad(chunks[n]["text"], label=f"C{n+1:02d}")
                    if new_text and new_text != chunks[n]["text"]:
                        chunks[n]["text"]   = new_text
                        chunks[n]["edited"] = True
                        print(f"  \u2713 C{n+1:02d} updated ({len(new_text)} chars)")
                    else:
                        print(f"  C{n+1:02d} unchanged")
            continue

        # v N -- preview single chunk
        if cmd0 == "v" and len(parts) > 1:
            token = parts[1].lstrip("cC")
            if token.isdigit():
                n = int(token) - 1
                if 0 <= n < len(chunks):
                    ch  = chunks[n]
                    pay = ch["point"].payload
                    markers = []
                    if ch["deleted"]: markers.append("\u2717 DELETE")
                    if ch["edited"]:  markers.append("\u270e EDITED")
                    g = _garbage_label(ch["text"], pay.get("url",""))
                    if g: markers.append(f"\u26a0 {g}")
                    mstr = "  ".join(markers)
                    print()
                    print(f"  [\u2500\u2500 C{n+1:02d} \u2500\u2500 chunk_idx={pay.get('chunk_idx','?')}  {mstr}]")
                    print(f"  {pay.get('url','')[:75]}")
                    words = ch["text"].split()
                    line  = "  "
                    for word in words:
                        if len(line) + len(word) + 1 > 90:
                            print(line)
                            line = "  " + word + " "
                        else:
                            line += word + " "
                    if line.strip():
                        print(line)
                    print(f"  [{len(ch['text'])} chars]")
                    print()
            continue

        print("  K E X S = decide | d/df/dc/da = mark | u/e/v = edit | r/n/b/q = nav")


# -- Browse knowledge ----------------------------------------------------------

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

    # Load all points
    points = _load_all_points(client, col)
    if not points:
        print("  Empty collection.")
        return

    # Group by topic_slug (fallback to item_id)
    slugs = {}
    for p in points:
        slug = p.payload.get("topic_slug", "") or p.payload.get("item_id", "unknown")
        if slug not in slugs:
            slugs[slug] = []
        slugs[slug].append(p)

    # Sort by chunk count DESC
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

    while True:
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
            to_del = []
            for n in indices:
                if 0 <= n < len(points):
                    to_del.append(points[n].id)
                    print(f"  \u2713 C{n+1:02d} marked")
            if to_del:
                confirm = input(f"  Delete {len(to_del)} chunks? [y/N]: ").strip().lower()
                if confirm in ("y","yes"):
                    _delete_ids(qdrant_url, col, to_del)
                    print(f"  \u2713 Deleted {len(to_del)} chunks")
                    return
            continue

        if cmd0 == "da":
            confirm = input(f"  Delete ALL {len(points)} chunks for '{slug}'? [y/N]: ").strip().lower()
            if confirm in ("y","yes"):
                _delete_ids(qdrant_url, col, [p.id for p in points])
                print(f"  \u2713 Deleted {len(points)} chunks")
                return
            continue

        print("  d N / d N-M / da = delete | Enter = back")


# -- Clean menu ----------------------------------------------------------------

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

    all_c = research_cols + knowledge_cols + persona_cols

    while True:
        print()
        print(f"  +==========================================+")
        print(f"  |  CLEAN                                   |")
        print(f"  +==========================================+")
        print(f"  |  [1]  Auto-clean garbage (research + knowledge)  |")
        print(f"  |  [2]  Deduplicate knowledge              |")
        print(f"  |  [3]  Remove stale chunks (by age)       |")
        print(f"  |  [4]  Remove domain from knowledge       |")
        print(f"  |  [5]  Rebuild payload indexes         |")
        print(f"  |  [Enter]  Back                           |")
        print(f"  +==========================================+")
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
            if not knowledge_cols:
                print("  No knowledge collections.")
                continue
            total_removed = 0
            for col in knowledge_cols:
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
                            key = f"{p.payload.get('url','')}::{p.payload.get('chunk_idx',0)}"
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
                            print(f"  \u2713 Removed {len(to_delete)} from '{col}'")
                            total_removed += len(to_delete)
                    else:
                        print(f"  '{col}': no duplicates")
                except Exception as e:
                    print(f"  Error in {col}: {e}")
            if total_removed:
                print(f"\n  \u2713 Total: {total_removed} duplicates removed")

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
                        print(f"  [OK] {col} -- indexes built")
                    except Exception as e:
                        print(f"  ⚠ {col} -- {e}")
            except Exception as e:
                print(f"  Error: {e}")
            input("  [Enter] to continue: ")

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


# -- Stats ---------------------------------------------------------------------

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


# -- Main menu -----------------------------------------------------------------

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
        else:
            print("  Unknown option.")


# -- Legacy stubs --------------------------------------------------------------

def _print_chunk(p) -> None:
    pay = p.payload
    print(f"\n  chunk_idx={pay.get('chunk_idx','?')} | {pay.get('domain','?')} | trust={pay.get('domain_trust','?')} score={pay.get('trust_score',0):.2f}")
    print(f"  {pay.get('text','')[:200]}")


def _paste_text_to_qdrant() -> None:
    print("  Use Feed menu [2] -> Paste.")


def _feed_persona_menu() -> None:
    print("  Use Feed menu [2] -> Feed persona.")



def _ask_slug_and_category() -> tuple:
    """Ask for category, destination, and topic_slug with live autocomplete.
    Returns (category, slug, collection) where:
      - collection = "knowledge_{category}" for topic-specific chunks (slug required)
      - collection = "knowledge_evergreen"  for general/cross-topic chunks (no slug)
    """
    try:
        from config import RESEARCH_CATEGORIES_DATA
    except Exception:
        RESEARCH_CATEGORIES_DATA = ["games","hardware","software","security","ai-data","other"]
    print("  Category:")
    for i, cat in enumerate(RESEARCH_CATEGORIES_DATA, 1):
        print(f"    [{i}] {cat}")
    cat_raw  = input("  > ").strip()
    category = RESEARCH_CATEGORIES_DATA[int(cat_raw)-1] if cat_raw.isdigit() and 1 <= int(cat_raw) <= len(RESEARCH_CATEGORIES_DATA) else "other"

    print()
    print("  Destination:")
    print(f"  [1] knowledge_{category}  -- topic-specific (requires slug)")
    print(f"  [2] knowledge_evergreen   -- general, cross-topic (no slug)")
    dest_raw = input("  > ").strip()

    if dest_raw == "2":
        return category, "", "knowledge_evergreen"

    def _slug_ac(partial, col):
        if not partial:
            return []
        try:
            from qdrant_client import QdrantClient
            from config import QDRANT_URL
            _qc = QdrantClient(url=QDRANT_URL)
            _seen = set()
            _offset = None
            while True:
                _res = _qc.scroll(
                    collection_name=col,
                    limit=200,
                    offset=_offset,
                    with_payload=True,
                    with_vectors=False,
                )
                _batch, _offset = _res
                for _pt in _batch:
                    _s = _pt.payload.get("topic_slug", "") or _pt.payload.get("slug", "")
                    if _s:
                        _seen.add(_s)
                if _offset is None:
                    break
            return sorted([s for s in _seen if partial in s])
        except Exception:
            return []

    _raw = input("  Topic slug (e.g. 'baldurs-gate-2', 'nvidia-dlss'): ").strip().lower().replace(" ", "-")
    if _raw:
        _suggestions = _slug_ac(_raw, f"knowledge_{category}")
        if _suggestions:
            print("  Matching slugs:")
            for _i, _s in enumerate(_suggestions[:8], 1):
                print(f"    [{_i}] {_s}")
            print("  [Enter] keep: " + _raw)
            _pick = input("  > ").strip()
            if _pick.isdigit() and 1 <= int(_pick) <= len(_suggestions[:8]):
                _raw = _suggestions[int(_pick) - 1]
    slug = _raw
    return category, slug, f"knowledge_{category}"
def _embed_and_upsert_chunks(chunks_list, url, category, slug, domain, title, collection):
    """Embed text chunks and upsert to Qdrant knowledge collection."""
    import hashlib as _hl
    import struct as _st
    import re as _re
    import requests as _req
    from datetime import datetime, timezone

    try:
        from config import QDRANT_URL, EMBED_MODEL, EMBED_DIM, OLLAMA_EMBED_URL, VALKEY_URL
    except Exception as e:
        print(f"  Config error: {e}")
        return 0

    _valkey = None
    try:
        import redis
        _valkey = redis.from_url(VALKEY_URL, decode_responses=False,
                                  socket_connect_timeout=2, socket_timeout=2)
        _valkey.ping()
    except Exception:
        _valkey = None

    now_iso  = datetime.now(timezone.utc).isoformat()
    item_id  = _hl.md5((url + slug).encode()).hexdigest()[:8]

    points = []
    print(f"  Embedding {len(chunks_list)} chunks...")
    for idx, chunk in enumerate(chunks_list):
        vec = None
        if _valkey:
            cache_key = b"emb:" + _hl.sha256(chunk.encode()).digest()
            cached    = _valkey.get(cache_key)
            if cached:
                floats = _st.unpack(f"{len(cached)//4}f", cached)
                vec    = list(floats)
        if vec is None:
            try:
                r2 = _req.post(f"{OLLAMA_EMBED_URL}/api/embed",
                               json={"model": EMBED_MODEL, "input": chunk}, timeout=30)
                r2.raise_for_status()
                vec = r2.json()["embeddings"][0]
                if _valkey:
                    packed = _st.pack(f"{len(vec)}f", *vec)
                    _valkey.setex(cache_key, 86400*30, packed)
            except Exception as e:
                print(f"  [embed] ERROR: {e}")
                continue

        ws    = _re.findall(r"\w+", chunk.lower())
        total = max(len(ws), 1)
        tf    = {}
        for w in ws:
            if len(w) > 2:
                tf[w] = tf.get(w, 0) + 1
        idx_map = {}
        for w, count in tf.items():
            ii = abs(hash(w)) % (2**20)
            idx_map[ii] = idx_map.get(ii, 0.0) + round(count / total, 6)

        point_id = abs(hash(url + slug + str(idx))) % (2**53)
        points.append({
            "id": point_id,
            "vector": {
                "dense":  vec,
                "sparse": {"indices": list(idx_map.keys()), "values": [round(v, 6) for v in idx_map.values()]},
            },
            "payload": {
                "topic":        title or slug,
                "category":     category,
                "item_id":      item_id,
                "url":          url,
                "title":        title or slug,
                "source":       "manual",
                "text":         chunk,
                "domain":       domain,
                "domain_trust": "trusted",
                "trust_score":  1.0,
                "trust_reason": "manual_feed",
                "content_type": "article",
                "language":     "en",
                "retrieval_boost": 1.0,
                "indexed_at":   now_iso,
                "knowledge":    True,
                "topic_slug":   slug,
                "accepted_at":  now_iso,
                "chunk_idx":    idx,
            },
        })
        print(f"  {idx+1}/{len(chunks_list)}...", end="\r")

    if not points:
        print("  No points to save.")
        return 0

    try:
        from config import QDRANT_URL, EMBED_DIM
        import requests as _req2
        col_url = f"{QDRANT_URL}/collections/{collection}"
        r = _req2.get(col_url, timeout=10)
        if r.status_code != 200:
            payload = {
                "vectors":        {"dense":  {"size": EMBED_DIM, "distance": "Cosine"}},
                "sparse_vectors": {"sparse": {"index": {"on_disk": False}}},
            }
            _req2.put(col_url, json=payload, timeout=10)

        r3 = _req2.put(f"{QDRANT_URL}/collections/{collection}/points",
                      json={"points": points}, timeout=60)
        if r3.status_code in (200, 201):
            print(f"  \u2713 {len(points)} chunks \u2192 {collection}  [slug: {slug}]")
            return len(points)
        else:
            print(f"  Error: {r3.text}")
            return 0
    except Exception as e:
        print(f"  Upsert error: {e}")
        return 0


def _paste_to_knowledge() -> None:
    """Paste clipboard text directly into knowledge collection with topic_slug."""
    from urllib.parse import urlparse as _up
    try:
        from config import RESEARCH_CHUNK_SIZE, RESEARCH_CHUNK_OVERLAP
        # Knowledge chunks should be larger than research chunks for better LLM context
        RESEARCH_CHUNK_SIZE    = max(RESEARCH_CHUNK_SIZE, 1500)
        RESEARCH_CHUNK_OVERLAP = 120
    except Exception:
        RESEARCH_CHUNK_SIZE, RESEARCH_CHUNK_OVERLAP = 1500, 120

    url = input("  Source URL (Enter to skip): ").strip()
    if not url:
        url = "manual://paste"

    category, slug, collection_dest = _ask_slug_and_category()
    if not slug and collection_dest != "knowledge_evergreen":
        print("  Slug is required for topic-specific knowledge feed.")
        return

    # Open Notepad for text input
    text = _edit_notepad("Paste your article text here, then save and close Notepad.", label="paste_input")
    if not text or len(text) < 100:
        print(f"  Too little text or cancelled.")
        return

    print(f"  \u2713 {len(text)} chars loaded")
    collection = collection_dest
    confirm = input(f"  Save to {collection} [slug: {slug if slug else 'evergreen'}]? [Y/n]: ").strip().lower()
    if confirm == "n":
        return

    # Clean markdown before chunking
    text = _clean_markdown(text)

    # Chunk
    chunks_list = []
    start = 0
    while start < len(text):
        chunks_list.append(text[start:start+RESEARCH_CHUNK_SIZE].strip())
        start += RESEARCH_CHUNK_SIZE - RESEARCH_CHUNK_OVERLAP
    chunks_list = [c for c in chunks_list if len(c) > 80]

    if url.startswith("manual://"):
        domain = "manual"
        title  = slug.replace("-", " ").title()
    else:
        domain = _up(url).netloc.lstrip("www.")
        title  = slug.replace("-", " ").title()

    _embed_and_upsert_chunks(chunks_list, url, category, slug, domain, title, collection)


def _feed_persona_to_knowledge() -> None:
    """Feed text into persona collection."""
    try:
        from config import PERSONAS
        persona_list = list(PERSONAS.keys())
    except Exception:
        persona_list = ["lukasz", "neutral", "critic", "paranoic"]

    print()
    for i, p in enumerate(persona_list, 1):
        print(f"  [{i}] persona_{p}")
    print()
    raw = input("  Pick persona (Enter = cancel): ").strip()
    if not raw or not raw.isdigit() or not (1 <= int(raw) <= len(persona_list)):
        print("  Cancelled.")
        return

    persona    = persona_list[int(raw) - 1]
    collection = f"persona_{persona}"

    print(f"  \u2192 Feeding: {collection}")
    print("  Copy your text (Ctrl+C), then press Enter...")
    input("  [Enter when copied]: ")

    text = ""
    try:
        import subprocess as _sp
        result = _sp.run(["powershell", "-command", "Get-Clipboard"],
                         capture_output=True, text=True, timeout=5)
        text = result.stdout.strip()
        _sp.run(["powershell", "-command", "Set-Clipboard -Value ''"],
                capture_output=True, timeout=3)
    except Exception as e:
        print(f"  Clipboard error: {e}")
        return

    if len(text) < 50:
        print(f"  Too little text ({len(text)} chars).")
        return

    print(f"  \u2713 {len(text)} chars")
    print(text[:300])
    if len(text) > 300:
        print(f"  ... [{len(text)-300} more]")

    source_label = input("  Label (e.g. 'forum comment', 'article draft'): ").strip() or "manual"
    confirm = input(f"  Save to {collection}? [Y/n]: ").strip().lower()
    if confirm == "n":
        return

    _embed_and_upsert_chunks(
        [text[i:i+1500] for i in range(0, len(text), 1380) if len(text[i:i+1500]) > 80],
        url=f"manual://{persona}/{source_label}",
        category="persona",
        slug=persona,
        domain="manual",
        title=source_label,
        collection=collection,
    )


def _feed_menu() -> None:
    while True:
        print()
        print(f"  \u2554\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2557")
        print(f"  \u2551  KNOWLEDGE FEED                          \u2551")
        print(f"  \u2560\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2563")
        print(f"  \u2551  [1]  Clip \u2014 fetch URL \u2192 Qdrant          \u2551")
        print(f"  \u2551  [2]  Paste \u2014 clipboard text \u2192 Qdrant    \u2551")
        print(f"  \u2551  [3]  Manual \u2014 load JSON file(s)         \u2551")
        print(f"  \u2551  [P]  Feed persona collection            \u2551")
        print(f"  \u2551  [Enter]  Back                           \u2551")
        print(f"  \u255a\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u255d")
        print()
        raw = input("  Choice: ").strip().lower()
        if raw in ("", "q", "back"):
            break
        elif raw == "1":
            url = input("  URL: ").strip()
            if not url:
                continue
            try:
                from config import RESEARCH_CATEGORIES_DATA
            except Exception:
                RESEARCH_CATEGORIES_DATA = ["games","hardware","software","security","ai-data","other"]
            print("  Category:")
            for i, cat in enumerate(RESEARCH_CATEGORIES_DATA, 1):
                print(f"    [{i}] {cat}")
            cat_raw  = input("  > ").strip()
            category = RESEARCH_CATEGORIES_DATA[int(cat_raw)-1] if cat_raw.isdigit() and 1 <= int(cat_raw) <= len(RESEARCH_CATEGORIES_DATA) else "other"
            if _chunker_available:
                slug, tags = prompt_slug_and_tags(category)
            else:
                slug, tags = "", []
            clip_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "clip.py")
            if not os.path.exists(clip_path):
                print(f"  clip.py not found: {clip_path}")
                continue
            cmd_args = [sys.executable, clip_path, "--url", url, "--category", category]
            if slug:
                cmd_args += ["--topic-slug", slug]
            if tags:
                cmd_args += ["--topic-tags", ",".join(tags)]
            subprocess.call(cmd_args)
        elif raw == "2":
            _paste_to_knowledge()
        elif raw == "p":
            _feed_persona_to_knowledge()
        elif raw == "3":
            path = input("  Path to .json file or directory: ").strip().strip('"')
            if not path:
                continue
            feed_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "manual_feed.py")
            if not os.path.exists(feed_path):
                print(f"  manual_feed.py not found: {feed_path}")
                continue
            if os.path.isdir(path):
                subprocess.call([sys.executable, feed_path, "--dir", path])
            else:
                subprocess.call([sys.executable, feed_path, "--file", path])
        else:
            print("  Unknown option.")
