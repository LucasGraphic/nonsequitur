# menus/knowledge/chunk_utils.py -- Garbage patterns, text utilities, notepad editor

import os
import re
import tempfile
import subprocess

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
    # Related articles / navigation dumps
    (r"(### .+\n){4,}",                              "RELATED_NAV"),
    (r"(#{1,3} (related|more from|trending|latest|popular|see also))",  "RELATED_NAV"),
    (r"read (more|next|also).*\|.*\|",               "RELATED_NAV"),
    (r"more options.*agree.*until dawn",             "RELATED_NAV"),
    (r"iabgpp_hdr_gppstring",                        "GDPR"),
    (r"your preferences will apply to this website only", "GDPR"),
]

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
            from domain_config import is_blocked as _is_blocked
            if _is_blocked(url):
                return "BLACKLIST"
        except Exception:
            pass
    t = text.lower()
    for pat, label in _GARBAGE_PATTERNS:
        if re.search(pat, t, re.DOTALL | re.MULTILINE):
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


def _print_chunk(p) -> None:
    """Legacy public stub used by agent.py."""
    pay = p.payload
    print(f"\n  chunk_idx={pay.get('chunk_idx','?')} | {pay.get('domain','?')} | trust={pay.get('domain_trust','?')} score={pay.get('trust_score',0):.2f}")
    print(f"  {pay.get('text','')[:200]}")


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
