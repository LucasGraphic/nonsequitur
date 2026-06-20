# pipeline/rss_run.py -- RSS feed fetcher -> Qdrant (rss_feed / rss_feed_pl / rss_feed_nor)
#
# Language routing:
#   English  -> rss_feed       (used in RAG during generate)
#   Polish   -> rss_feed_pl    (Browse RSS only)
#   Norwegian-> rss_feed_nor   (Browse RSS only)
#
# USAGE:
#   python rss_run.py                    # fetch all feeds
#   python rss_run.py --category games   # fetch only games feeds
#   python rss_run.py --dry-run          # show what would be fetched
#   python rss_run.py --list             # list configured feeds

import argparse
import hashlib
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import requests

BASE_DIR     = Path(__file__).parent.parent
SOURCES_FILE = BASE_DIR / "data" / "sources.json"
sys.path.insert(0, str(BASE_DIR))

from config import (
    QDRANT_URL, OLLAMA_EMBED_URL, EMBED_MODEL, EMBED_DIM,
    RESEARCH_CHUNK_SIZE,
)

CHUNK_SIZE    = RESEARCH_CHUNK_SIZE
CHUNK_OVERLAP = 80
SKIP_KEYS     = {"_comment", "_last_updated", "feeds"}

# -- Collections ------------------------------------------------------------
COLLECTION_EN  = "rss_feed"
COLLECTION_PL  = "rss_feed_pl"
COLLECTION_NOR = "rss_feed_nor"

LANG_TO_COLLECTION = {
    "en": COLLECTION_EN,
    "pl": COLLECTION_PL,
    "no": COLLECTION_NOR,
    "nb": COLLECTION_NOR,
    "nn": COLLECTION_NOR,
}

ALL_COLLECTIONS = [COLLECTION_EN, COLLECTION_PL, COLLECTION_NOR]


# -- Helpers ----------------------------------------------------------------

def _load_sources(category: str = None) -> list:
    if not SOURCES_FILE.exists():
        print(f"  sources.json not found: {SOURCES_FILE}")
        return []
    data  = json.loads(SOURCES_FILE.read_text(encoding="utf-8"))
    feeds = []
    for cat, items in data.items():
        if cat in SKIP_KEYS or not isinstance(items, list):
            continue
        if category and cat != category:
            continue
        for item in items:
            if not item.get("url"):
                continue
            feeds.append({
                "url":      item["url"],
                "category": cat,
                "label":    item.get("domain", item["url"]),
                "trust":    item.get("trust", "unknown"),
            })
    return feeds


def _detect_lang(text: str) -> str:
    try:
        from langdetect import detect
        return detect(text)
    except Exception:
        return "en"


def _embed(text: str) -> list | None:
    try:
        r = requests.post(
            f"{OLLAMA_EMBED_URL}/api/embed",
            json={"model": EMBED_MODEL, "input": text},
            timeout=60,
        )
        data = r.json()
        emb  = data.get("embeddings")
        if emb and len(emb) > 0:
            return emb[0]
        return data.get("embedding")
    except Exception as e:
        print(f"  [embed] ERROR: {e}")
        return None


def _ensure_collections() -> None:
    for col in ALL_COLLECTIONS:
        url = f"{QDRANT_URL}/collections/{col}"
        r   = requests.get(url, timeout=10)
        if r.status_code == 200:
            continue
        payload = {
            "vectors":        {"dense":  {"size": EMBED_DIM, "distance": "Cosine"}},
            "sparse_vectors": {"sparse": {"index": {"on_disk": False}}},
        }
        r2 = requests.put(url, json=payload, timeout=10)
        if r2.status_code in (200, 201):
            print(f"  [qdrant] Created collection: {col}")


def _chunk_text(text: str) -> list:
    chunks, start = [], 0
    while start < len(text):
        end = start + CHUNK_SIZE
        chunks.append(text[start:end].strip())
        start += CHUNK_SIZE - CHUNK_OVERLAP
    return [c for c in chunks if len(c) > 80]


def _already_indexed(url: str) -> bool:
    """Check if URL already exists in any rss collection."""
    for col in ALL_COLLECTIONS:
        try:
            r = requests.post(
                f"{QDRANT_URL}/collections/{col}/points/scroll",
                json={
                    "filter": {"must": [{"key": "url", "match": {"value": url}}]},
                    "limit":  1,
                    "with_payload": False,
                },
                timeout=10,
            )
            if r.status_code == 200:
                if len(r.json().get("result", {}).get("points", [])) > 0:
                    return True
        except Exception:
            pass
    return False


def _upsert(chunks_payload: list, collection: str = COLLECTION_EN) -> int:
    points = []
    for cp in chunks_payload:
        vec = _embed(cp["text"])
        if not vec:
            continue

        words   = re.findall(r"\w+", cp["text"].lower())
        total   = max(len(words), 1)
        tf: dict = {}
        for w in words:
            if len(w) > 2:
                tf[w] = tf.get(w, 0) + 1
        idx_map: dict = {}
        for w, count in tf.items():
            idx = abs(hash(w)) % (2**20)
            idx_map[idx] = idx_map.get(idx, 0.0) + round(count / total, 6)

        point_id = abs(hash(cp["url"] + str(cp["chunk_idx"]))) % (2**53)
        points.append({
            "id":     point_id,
            "vector": {
                "dense":  vec,
                "sparse": {
                    "indices": list(idx_map.keys()),
                    "values":  [round(v, 6) for v in idx_map.values()],
                },
            },
            "payload": cp,
        })

    if not points:
        return 0

    r = requests.put(
        f"{QDRANT_URL}/collections/{collection}/points",
        json={"points": points},
        timeout=60,
    )
    return len(points) if r.status_code in (200, 201) else 0


# -- RSS fetch --------------------------------------------------------------

def _parse_feed_raw(url: str) -> list:
    """Minimal XML RSS/Atom parser -- fallback when feedparser not available."""
    try:
        r = requests.get(url, timeout=15, headers={
            "User-Agent": "Mozilla/5.0 (compatible; ArticleAgent/1.0)"
        })
        r.raise_for_status()
        text = r.text

        blocks = re.findall(r"<item>(.*?)</item>", text, re.DOTALL)
        if not blocks:
            blocks = re.findall(r"<entry>(.*?)</entry>", text, re.DOTALL)

        entries = []
        for block in blocks:
            title_m = re.search(
                r"<title[^>]*>(?:<!\[CDATA\[)?\s*(.*?)\s*(?:\]\]>)?</title>",
                block, re.DOTALL
            )
            link_m = re.search(
                r"<link[^>]*>\s*(?:<!\[CDATA\[)?\s*(https?://[^\s<]+?)\s*(?:\]\]>)?</link>",
                block, re.DOTALL
            )
            if not link_m:
                link_m = re.search(r'<link[^>]+href=["\']([^"\']+)["\']', block)
            desc_m = re.search(
                r"<(?:description|summary)[^>]*>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</(?:description|summary)>",
                block, re.DOTALL
            )

            if title_m and link_m:
                title   = re.sub(r"<[^>]+>", "", title_m.group(1)).strip()
                link    = link_m.group(1).strip()
                summary = ""
                if desc_m:
                    summary = re.sub(r"<[^>]+>", " ", desc_m.group(1))
                    summary = re.sub(r"\s+", " ", summary).strip()[:600]
                entries.append({"title": title, "link": link, "summary": summary})

        return entries
    except Exception as e:
        print(f"  [rss] parse error: {e}")
        return []


def _fetch_feed(feed: dict, dry_run: bool = False) -> dict:
    url      = feed["url"]
    category = feed.get("category", "other")
    trust    = feed.get("trust", "unknown")
    label    = feed.get("label", url)

    result = {"feed": label, "category": category,
              "new_en": 0, "new_pl": 0, "new_nor": 0,
              "skipped": 0, "errors": 0}

    # Try feedparser first, fallback to raw parser
    entries = []
    try:
        import feedparser
        parsed  = feedparser.parse(url)
        entries = getattr(parsed, "entries", [])
        entries = [
            {
                "title":   getattr(e, "title", ""),
                "link":    getattr(e, "link", ""),
                "summary": re.sub(r"<[^>]+>", " ",
                           getattr(e, "summary", "") or
                           getattr(e, "description", "") or "")[:600],
            }
            for e in entries
        ]
    except ImportError:
        entries = _parse_feed_raw(url)
    except Exception as e:
        print(f"  [rss] feedparser error for {label}: {e}")
        entries = _parse_feed_raw(url)

    if not entries:
        print(f"  o {label[:35]:<35} 0 entries")
        return result

    now_iso = datetime.now(timezone.utc).isoformat()
    domain  = urlparse(url).netloc.removeprefix("www.")

    for entry in entries[:25]:
        item_url   = entry.get("link", "").strip()
        item_title = entry.get("title", "").strip()
        summary    = entry.get("summary", "").strip()

        if not item_url or not item_title:
            continue

        if dry_run:
            lang = _detect_lang(f"{item_title} {summary[:100]}")
            col  = LANG_TO_COLLECTION.get(lang, COLLECTION_EN)
            print(f"    [dry] [{lang}->{col}] {item_title[:60]}")
            result["new_en"] += 1
            continue

        if _already_indexed(item_url):
            result["skipped"] += 1
            continue

        text = f"{item_title}\n\n{summary}" if summary else item_title
        if len(text) < 40:
            continue

        # Detect language -> route to correct collection
        lang       = _detect_lang(text)
        target_col = LANG_TO_COLLECTION.get(lang, COLLECTION_EN)

        chunks         = _chunk_text(text)
        chunks_payload = []
        item_id        = hashlib.md5(item_url.encode()).hexdigest()[:8]

        for idx, chunk in enumerate(chunks):
            chunks_payload.append({
                "topic":           item_title[:120],
                "category":        category,
                "url":             item_url,
                "title":           item_title[:120],
                "source":          "rss",
                "text":            chunk,
                "domain":          domain,
                "domain_trust":    trust,
                "trust_score":     1.0 if trust == "press" else 0.8,
                "trust_reason":    f"rss_{trust}",
                "content_type":    "article",
                "language":        lang,
                "retrieval_boost": 1.0,
                "indexed_at":      now_iso,
                "content_date":    now_iso[:10],
                "freshness":       "news",
                "knowledge":       True,
                "chunk_idx":       idx,
                "item_id":         item_id,
            })

        added = _upsert(chunks_payload, target_col)
        if added:
            if target_col == COLLECTION_EN:
                result["new_en"] += 1
            elif target_col == COLLECTION_PL:
                result["new_pl"] += 1
            else:
                result["new_nor"] += 1
        else:
            result["errors"] += 1

    return result


# -- Main run ---------------------------------------------------------------

def run_rss(category: str = None, dry_run: bool = False) -> dict:
    feeds = _load_sources(category)
    if not feeds:
        print("  No feeds configured.")
        return {"new_en": 0, "new_pl": 0, "new_nor": 0, "skipped": 0, "errors": 0}

    if not dry_run:
        _ensure_collections()

    cat_label = f" [{category}]" if category else f" [{len(set(f['category'] for f in feeds))} categories]"
    print(f"\n  RSS Fetch -- {len(feeds)} feed(s){cat_label}"
          + (" [dry-run]" if dry_run else ""))
    print("  " + "-" * 60)

    total     = {"new_en": 0, "new_pl": 0, "new_nor": 0, "skipped": 0, "errors": 0}
    by_cat: dict = {}

    for feed in feeds:
        result  = _fetch_feed(feed, dry_run=dry_run)
        new_en  = result["new_en"]
        new_pl  = result["new_pl"]
        new_nor = result["new_nor"]
        skip    = result["skipped"]
        err     = result["errors"]
        total["new_en"]  += new_en
        total["new_pl"]  += new_pl
        total["new_nor"] += new_nor
        total["skipped"] += skip
        total["errors"]  += err

        cat = result["category"]
        by_cat[cat] = by_cat.get(cat, 0) + new_en + new_pl + new_nor

        parts = []
        if new_en:  parts.append(f"+{new_en} EN")
        if new_pl:  parts.append(f"+{new_pl} PL")
        if new_nor: parts.append(f"+{new_nor} NOR")
        if skip:    parts.append(f"{skip} skip")
        if err:     parts.append(f"{err} err")
        status = "  ".join(parts) if parts else "0 new"

        icon = "⚠" if err else ("o" if not (new_en + new_pl + new_nor) else "[OK]")
        print(f"  {icon} {result['feed'][:35]:<35}  {status}")

    print("  " + "-" * 60)
    total_new = total["new_en"] + total["new_pl"] + total["new_nor"]
    print(f"  Total: +{total_new} new  "
          f"(EN:{total['new_en']}  PL:{total['new_pl']}  NOR:{total['new_nor']})  "
          f"{total['skipped']} skipped  {total['errors']} errors")
    if by_cat and not dry_run:
        print(f"  By category: " + "  ".join(f"{k}:{v}" for k, v in sorted(by_cat.items()) if v))
    return total


def main():
    parser = argparse.ArgumentParser(description="RSS feed fetcher -> Qdrant")
    parser.add_argument("--category", help="Filter: games / ai-data / hardware / ...")
    parser.add_argument("--dry-run",  action="store_true", help="Preview only, no indexing")
    parser.add_argument("--list",     action="store_true", help="List configured feeds")
    args = parser.parse_args()

    if args.list:
        feeds = _load_sources()
        cats  = sorted(set(f["category"] for f in feeds))
        print(f"\n  Configured feeds ({len(feeds)}) across {len(cats)} categories:\n")
        for cat in cats:
            cat_feeds = [f for f in feeds if f["category"] == cat]
            print(f"  [{cat}]  ({len(cat_feeds)} feeds)")
            for f in cat_feeds:
                trust_tag = f"[{f['trust']}]" if f["trust"] != "unknown" else ""
                print(f"    {trust_tag:<9} {f['label']}")
            print()
        return

    run_rss(category=args.category, dry_run=args.dry_run)


if __name__ == "__main__":
    main()

