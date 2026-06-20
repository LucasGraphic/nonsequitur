import sys
import re
import time
import requests
from typing import Optional
from qdrant_client import QdrantClient
from qdrant_client.models import (
    Distance, VectorParams,
    SparseVectorParams, SparseIndexParams, SparseVector,
    PointStruct,
    Filter, FieldCondition, MatchValue, MatchAny,
)
import core.queue as queue
from discovery.sources import fetch_all
from domain_config import get_domain_trust
from pipeline.content_filter import is_garbage as _is_garbage_text
from config import (
    QDRANT_URL, EMBED_MODEL, EMBED_DIM,
    OLLAMA_URL, OLLAMA_EMBED_URL,
    RESEARCH_FETCH_MAX, RESEARCH_CHUNK_SIZE, RESEARCH_CHUNK_OVERLAP,
    research_collection,
)
# --- Blocked domain filter ----------------------------------------

def _is_blocked(url: str) -> bool:
    try:
        from domain_config import is_blocked
        return is_blocked(url)
    except Exception:
        return False

# --- Qdrant helpers --------------------------------------

def _ensure_collection(client: QdrantClient, name: str) -> None:
    """
    Ensure collection exists with correct vector dimensions.
    If collection exists with wrong dimensions ->?? deletes and recreates it.
    """
    existing = [c.name for c in client.get_collections().collections]
    if name in existing:

        # Check if dimensions match current EMBED_DIM
        try:
            info = client.get_collection(name)
            current_dim = info.config.params.vectors.get("dense", {})
            if hasattr(current_dim, "size") and current_dim.size != EMBED_DIM:
                print(f"   [qdrant] Dimension mismatch in '{name}': "
                      f"expected {EMBED_DIM}, got {current_dim.size} ->?? recreating")
                client.delete_collection(name)
                existing = []  # force recreate
            else:
                print(f"   [qdrant] Exists: '{name}'")
        except Exception:
            print(f"   [qdrant] Exists: '{name}'")
    if name not in existing:
        client.create_collection(
            collection_name       = name,
            vectors_config        = {"dense": VectorParams(size=EMBED_DIM, distance=Distance.COSINE)},
            sparse_vectors_config = {"sparse": SparseVectorParams(index=SparseIndexParams(on_disk=False))},
        )
        print(f"   [qdrant] Created hybrid collection: '{name}' (dim={EMBED_DIM})")
    try:
        client.create_payload_index(
            collection_name = name,
            field_name      = "item_id",
            field_schema    = "keyword",
        )
    except Exception:
        pass

def _get_valkey():
    """Get Valkey client ->?? lazy init, returns None if unavailable."""
    try:
        import redis as _redis
        from config import VALKEY_URL
        client = _redis.from_url(VALKEY_URL, socket_connect_timeout=2, socket_timeout=2)
        client.ping()
        return client
    except Exception:
        return None

def _cache_key(text: str) -> str:
    """Generate cache key from text hash."""
    import hashlib
    return f"emb:{hashlib.sha256(text.encode()).hexdigest()[:32]}"

def _embed(text: str) -> Optional[list]:

    # Try cache first
    valkey = _get_valkey()
    if valkey:
        try:
            import json as _json
            cached = valkey.get(_cache_key(text))
            if cached:
                return _json.loads(cached)
        except Exception:
            pass
    try:
        r = requests.post(
            f"{OLLAMA_EMBED_URL}/api/embed",
            json={"model": EMBED_MODEL, "input": text},
            timeout=60,
        )
        data = r.json()
        embeddings = data.get("embeddings")
        if embeddings and len(embeddings) > 0:
            vec = embeddings[0]
        else:
            vec = data.get("embedding")

        # Save to cache
        if vec and valkey:
            try:
                import json as _json
                valkey.setex(_cache_key(text), 2592000, _json.dumps(vec))  # 30 days TTL
            except Exception:
                pass
        return vec
    except Exception as e:
        print(f"   [embed] error: {e}")
        return None

def _embed_batch(texts: list) -> list:
    """Batch embedding with Valkey cache ->?? checks cache first, only embeds misses."""
    if not texts:
        return []
    import json as _json
    valkey      = _get_valkey()
    results     = [None] * len(texts)
    miss_idx    = []
    miss_texts  = []

    # Check cache for each text
    if valkey:
        for i, text in enumerate(texts):
            try:
                cached = valkey.get(_cache_key(text))
                if cached:
                    results[i] = _json.loads(cached)
                else:
                    miss_idx.append(i)
                    miss_texts.append(text)
            except Exception:
                miss_idx.append(i)
                miss_texts.append(text)
    else:
        miss_idx   = list(range(len(texts)))
        miss_texts = texts
    cache_hits = len(texts) - len(miss_texts)
    if cache_hits > 0:
        print(f"   [embed] cache: {cache_hits} hits, {len(miss_texts)} misses")

    # Embed only cache misses
    if miss_texts:
        try:
            r = requests.post(
                f"{OLLAMA_EMBED_URL}/api/embed",
                json={"model": EMBED_MODEL, "input": miss_texts},
                timeout=120,
            )
            data = r.json()
            embeddings = data.get("embeddings", [])
            while len(embeddings) < len(miss_texts):
                embeddings.append(None)
            # Fill results and save to cache
            for j, (idx, text) in enumerate(zip(miss_idx, miss_texts)):
                vec = embeddings[j]
                results[idx] = vec
                if vec and valkey:
                    try:
                        valkey.setex(_cache_key(text), 2592000, _json.dumps(vec))
                    except Exception:
                        pass
        except Exception as e:
            print(f"   [embed batch] error: {e}")
    return results

def _sparse_vector(text: str) -> SparseVector:
    """
    Build sparse BM25-style vector from text for keyword matching.
    Uses idx_map to deduplicate hash collisions.
    """
    tokens = re.findall(r"[a-z0-9]+", text.lower())
    if not tokens:
        return SparseVector(indices=[0], values=[0.0])
    tf: dict = {}
    for token in tokens:
        if len(token) < 2:
            continue
        tf[token] = tf.get(token, 0) + 1

    # Deduplicate: sum values for colliding indices
    idx_map: dict = {}
    total = max(len(tokens), 1)
    for token, count in tf.items():
        idx = abs(hash(token)) % (2**20)
        idx_map[idx] = idx_map.get(idx, 0.0) + round(count / total, 6)
    return SparseVector(
        indices = list(idx_map.keys()),
        values  = [round(v, 6) for v in idx_map.values()],
    )
# --- URL pre-filter (skip before fetching) -----------------------

def _should_skip_url(url: str) -> tuple:
    """
    Fast pre-filter -- returns (True, reason) if URL should be skipped
    before fetching, (False, "") otherwise.
    Called before _fetch_page to avoid wasting Crawl4AI/trafilatura time.
    """
    import re as _re
    from urllib.parse import urlparse as _up

    if not url or len(url) < 10:
        return True, "empty"

    try:
        parsed = _up(url)
        domain = parsed.netloc.lower().removeprefix("www.")
        path   = parsed.path.lower()
        query  = parsed.query.lower()
    except Exception:
        return True, "parse_error"

    # -- Binary / media extensions -------------------------------------
    BINARY_EXTS = (
        ".pdf", ".zip", ".exe", ".dmg", ".pkg", ".msi", ".deb", ".rpm",
        ".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg", ".ico", ".bmp",
        ".mp4", ".mp3", ".avi", ".mov", ".mkv", ".webm", ".flac", ".wav",
        ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
        ".tar", ".gz", ".7z", ".rar",
    )
    if any(path.endswith(ext) for ext in BINARY_EXTS):
        return True, "binary_file"

    # -- Video / social platforms (no useful text) ---------------------
    NO_TEXT_DOMAINS = {
        "youtube.com", "youtu.be", "twitch.tv", "tiktok.com", "vimeo.com",
        "instagram.com", "pinterest.com", "snapchat.com",
        "spotify.com", "soundcloud.com",
        "play.google.com", "apps.apple.com",
    }
    if domain in NO_TEXT_DOMAINS or any(domain.endswith("." + d) for d in NO_TEXT_DOMAINS):
        return True, "no_text_platform"

    # -- Known low-yield domains ---------------------------------------
    LOW_YIELD_DOMAINS = {
        # Forums -- mostly nav/boilerplate, rarely indexable article text
        "resetera.com", "neogaf.com", "facepunch.com",
        "forums.guru3d.com",
        # Pure aggregators with no original content
        "alltop.com", "popurls.com", "techmeme.com",
        # Spam / SEO farms flagged in previous sessions
        "odysseusai.net", "blog.veefly.com",
        "gaminghq.blog", "gaminghqgames.com",
        "global-esports.news",
        # Login-walled or cookie-heavy with no text yield
        "facebook.com", "twitter.com", "x.com", "threads.com",
        "linkedin.com", "discord.com", "t.me",
        # Pure image/video content
        "flickr.com", "imgur.com", "giphy.com",
        # Maps / geo -- not useful for article research
        "maps.google.com", "waze.com",
        # Redirect / shortener services
        "bit.ly", "tinyurl.com", "t.co", "ow.ly", "buff.ly",
    }
    if domain in LOW_YIELD_DOMAINS:
        return True, f"low_yield_domain:{domain}"

    # -- URL structural patterns -- listing / nav pages -----------------
    SKIP_PATH_PATTERNS = [
        r"^/tag/",
        r"^/tags/",
        r"^/category/",
        r"^/categories/",
        r"^/author/",
        r"^/authors/",
        r"^/user/",
        r"^/profile/",
        r"^/page/\d+",
        r"/page/\d+/?$",
        r"^/search[/?]",
        r"^/feed/?$",
        r"^/rss/?",
        r"^/sitemap",
        r"^/wp-json/",
        r"^/wp-admin/",
        r"^/cdn-cgi/",
        r"^/static/",
        r"^/assets/",
        r"^/images?/",
        r"^/media/",
        r"^/login/?$",
        r"^/register/?$",
        r"^/signup/?$",
        r"^/checkout/",
        r"^/cart/?",
        r"^/account/",
        r"^/privacy-policy/?$",
        r"^/terms/?$",
        r"^/cookie-policy/?$",
        r"^/about/?$",
        r"^/contact/?$",
        r"^/advertise/?$",
        r"^/newsletter/?$",
    ]
    for pat in SKIP_PATH_PATTERNS:
        if _re.search(pat, path):
            return True, f"listing_page:{pat}"

    # -- Query string patterns -- search / tracking ---------------------
    SKIP_QUERY_PATTERNS = [
        r"^\?s=",         # WordPress search
        r"[?&]s=",
        r"[?&]q=",        # generic search
        r"[?&]search=",
        r"[?&]utm_",      # tracking parameters only (no content signal)
        r"[?&]ref=",
        r"[?&]fbclid=",
        r"[?&]gclid=",
    ]
    # Only skip if ONLY tracking params (no meaningful path)
    if query and path in ("/", "") and any(_re.search(p, "?" + query) for p in SKIP_QUERY_PATTERNS[:3]):
        return True, "search_query_url"

    # -- Social sharing / tracking URL fragments -----------------------
    if "#comment" in url.lower() or "#respond" in url.lower():
        return True, "comment_anchor"

    return False, ""


# --- Fetch page (full text) ------------------------------

def _fetch_page(url: str, timeout: int = 10) -> str:
    """
    Fetch and extract main text content from URL.
    Primary: Crawl4AI service (http://10.0.0.195:8777) -- handles JS-rendered pages.
    Fallback 1: Jina Reader (r.jina.ai) -- clean markdown extraction.
    Fallback 2: trafilatura -- fast, good for static pages.
    Fallback 3: basic requests + strip HTML tags.
    Skips YouTube/Twitch (JS-rendered video, no useful text).
    """

    # Skip JS-rendered video platforms
    skip_domains = ("youtube.com", "youtu.be", "twitch.tv", "tiktok.com")
    if any(d in url for d in skip_domains):
        return ""
    from config import CRAWL4AI_URL

    # Primary: Crawl4AI service
    try:
        r = requests.post(
            f"{CRAWL4AI_URL}/crawl",
            json={"url": url},
            timeout=timeout + 5,
        )
        if r.status_code == 200:
            data = r.json()
            if data.get("success"):
                text = data.get("markdown", "").strip()
                if text and len(text) > 100:
                    import re as _re_md
                    # Remove images
                    text = _re_md.sub(r"!\[[^\]]*\]\([^\)]*\)", "", text)
                    # Keep link text, remove URLs
                    text = _re_md.sub(r"\[([^\]]*)\]\([^\)]*\)", r"\1", text)
                    text = _re_md.sub(r"https?://\S+", "", text)
                    text = _re_md.sub(r"\n{4,}", "\n\n\n", text)
                    # Skip leading navigation blocks
                    lines = text.split("\n")
                    content_start = 0
                    for idx_line, line in enumerate(lines):
                        stripped = line.strip()
                        if stripped and not stripped.startswith("*") and not stripped.startswith("#") and len(stripped) > 80:
                            content_start = idx_line
                            break
                    text = "\n".join(lines[content_start:]).strip()
                    if len(text) > 100:
                        return text[:25000]
    except Exception:
        pass

    # Fallback 1: Jina Reader
    try:
        r = requests.get(
            f"https://r.jina.ai/{url}",
            timeout=15,
            headers={"Accept": "text/plain"},
        )
        if r.status_code == 200:
            text = r.text.strip()
            if text and len(text) > 200:
                import re as _re_j
                text = _re_j.sub(r"\n{4,}", "\n\n\n", text)
                return text[:25000]
    except Exception:
        pass

    # Fallback 2: trafilatura
    import threading
    result = {"text": ""}
    def _fetch():
        try:
            import trafilatura
            downloaded = trafilatura.fetch_url(url)
            if downloaded:
                text = trafilatura.extract(
                    downloaded,
                    include_comments = False,
                    include_tables   = True,
                    no_fallback      = False,
                    favor_precision  = True,
                )
                if text and len(text.strip()) > 100:
                    result["text"] = text.strip()[:25000]
        except Exception:
            pass
    t = threading.Thread(target=_fetch, daemon=True)
    t.start()
    t.join(timeout=timeout)
    if result["text"]:
        return result["text"]

    # Fallback 3: basic requests + strip HTML tags
    try:
        import re as _re
        r = requests.get(url, timeout=timeout, headers={
            "User-Agent": "Mozilla/5.0 (compatible; ArticleAgent/1.0)"
        })
        r.raise_for_status()
        text = _re.sub(r"<[^>]+>", " ", r.text)
        text = _re.sub(r"\s+", " ", text).strip()
        return text[:25000] if len(text) > 100 else ""
    except Exception:
        return ""

def _is_garbage_text(text: str) -> bool:
    """
    Detect garbage text that should not be indexed.
    Returns True if text should be rejected.
    """
    if not text or len(text) < 50:
        return True

    # PDF binary
    if text.lstrip().startswith("%PDF"):
        return True
    pdf_markers = ["endobj", "endstream", "/Type /Page", "obj <<", ">> endobj"]
    if sum(1 for m in pdf_markers if m in text) >= 3:
        return True

    # High ratio of non-printable chars
    import re as _re
    printable = len(_re.findall(r"[a-zA-Z0-9 \n\t.,;:!?'\"-]", text))
    if printable / max(len(text), 1) < 0.5:
        return True

    # Mostly numeric
    words = text.split()
    if len(words) > 20:
        numeric = sum(1 for w in words if _re.match(r"^[\d.]+$", w))
        if numeric / len(words) > 0.7:
            return True
    t = text.lower()

    # JavaScript / code
    if _re.search(r"function\s*\(\s*\)\s*\{", t): return True
    if _re.search(r"document\.cookie|window\.location", t): return True
    if _re.search(r"addsize\(\[", t): return True
    if _re.search(r'"@type"\s*:\s*"person"', t): return True
    if _re.search(r'"author"\s*:\s*\{', t): return True
    if t.lstrip().startswith("(function"): return True

    # Affiliate / coupon
    if _re.search(r"when you buy through links", t): return True
    if _re.search(r"(use the|remember to use).{0,30}coupon code", t): return True
    if _re.search(r"earn a commission|syndication partners may earn", t): return True

    # Author bios
    if _re.search(r"although he loves everything that.s hardware", t): return True
    if _re.search(r"he has a soft spot for (cpus|gpus|ram)", t): return True
    if _re.search(r"although his background is in legal", t): return True
    if _re.search(r"news.{0,20}world report.{0,20}lifewire", t): return True
    if _re.search(r"when not wr(iting|apping)", t): return True
    if _re.search(r"contributing (writer|editor) at", t): return True

    # Forum reply metadata
    if _re.search(r"^[-\w/]+ - reply\b", t[:50]): return True
    if _re.search(r"reply\b.{0,20}\bas a westerner\b", t): return True

    # Benchmark boilerplate
    if _re.search(r"the first value corresponds to the average frames per second", t): return True
    if _re.search(r"1% low fps.*metric for measuring", t): return True

    # Review boilerplate
    if _re.search(r"by selecting premium components across the board.*review", t): return True
    if _re.search(r"tom.s hardware verdict.*the .{3,30} offers", t): return True

    # Off-topic
    if _re.search(r"omega seamaster|007.*first light|first light.*io interactive", t): return True
    if _re.search(r"lenovo.{0,20}sells a bunch", t): return True
    if _re.search(r"save over.{0,30}on this.{0,30}gaming pc|score a big discount", t): return True
    if _re.search(r"substantial social and economic disruption followed in china", t): return True
    if _re.search(r"the spread of this culture was also supported", t): return True
    if _re.search(r"confucianism was a leading philosophy", t): return True
    if _re.search(r"china is an east asian country.{0,30}situated", t): return True
    if _re.search(r"making up around one.fifth of the world.s economy", t): return True
    if _re.search(r"the country was unstable and fragmented during the warlord", t): return True
    if _re.search(r"chinahighlights\.com", t): return True
    if _re.search(r"worldatlas\.com", t) and _re.search(r"(maps?|geography|country|located|situated)", t): return True
    if _re.search(r"theworldfactbook\.org", t): return True
    if _re.search(r"great leap forward.*cultural revolution", t): return True
    if _re.search(r"mao zedong|mao died in 197", t): return True

    # Cookie consent
    if _re.search(r"(accept all cookies|cookie consent)", t) and len(text) < 300: return True

    # Reddit noise
    if _re.search(r"hey y.?all|first time encountering this tactic", t): return True
    if _re.search(r"first game (ever |against a real)|obviously a blunder", t): return True

    # Facebook junk
    if _re.search(r"facebook\.com/login", t): return True
    if _re.search(r"\[log in\].*facebook", t): return True
    if _re.search(r"facebook\.com/hashtag/", t): return True
    if _re.search(r"title:\s*\d+[km]? views\s*.\s*[\d.,]+[km]? reactions", t): return True
    if _re.search(r"\[\d+[hd]\]\(https://www\.facebook\.com", t): return True
    if _re.search(r"facebook\.com/sharer", t): return True

    # Navigation leaks
    if _re.search(r"home\s*/\s*tech news\s*/\s*featured", t): return True
    if _re.search(r"nextstay cc settings", t): return True
    if _re.search(r"just a moment\.\.\.", t) and len(text) < 200: return True
    if _re.search(r"title:\s*just a moment", t): return True
    if _re.search(r"url source:\s*https://www\.resetera\.com", t): return True
    if _re.search(r"level up your gaming news", t): return True
    if _re.search(r"global-esports\.news is your place", t): return True
    if _re.search(r"sign up for our newsletter.*written by", t): return True
    if _re.search(r"gaminghq(blog|games)", t): return True
    if _re.search(r"login\s+get exitlag", t): return True
    if _re.search(r"diablo iv.*arpg.*diablo iii.*arpg", t): return True

    # Wikipedia markdown citations junk
    if _re.search(r"\[\^]\(https://en\.wikipedia\.org/wiki/", t) and len(text) < 400: return True

    # SaaS / marketing CTA
    if _re.search(r"request a demo", t): return True
    if _re.search(r"click below and (let|ask)", t): return True
    if _re.search(r"power up (lead generation|your)", t): return True
    if _re.search(r"let ai summaris.e and analys.e this post", t): return True
    if _re.search(r"chatgptperplexity", t): return True
    if _re.search(r"sign up for (free|our newsletter) (today|now|below)", t): return True
    if _re.search(r"book (a|your) (free |demo )?(call|session|consultation)", t): return True
    if _re.search(r"schedule (a|your) (free )?(demo|call|consultation)", t): return True
    if _re.search(r"start (your )?(free )?trial", t): return True
    if _re.search(r"get (started|access) (for free|today|now)", t): return True
    if _re.search(r"tags\s+(ai agents?|autonomous ai)", t): return True
    if _re.search(r"tl;dr\s*/\s*summary\s+most b", t): return True
    if _re.search(r"in this guide.{0,30}we will discover", t): return True
    if _re.search(r"the practical framework for", t): return True
    if _re.search(r"industries\s+\*\s+saas", t): return True
    return False

# -- DROP-IN REPLACEMENT for _chunk() in pipeline/research_run.py -------------
#
# Changes vs previous version:
#   - Hard section boundaries at markdown headings (#, ##, ###)
#   - Overlap stays within a section, never bleeds across headings
#   - Heading prepended to first chunk of each section (RAG context)
#   - No other behaviour changes -- paragraph/sentence logic identical
#
# After replacing: wipe all Qdrant research_* and knowledge_* collections,
# then re-run research for all items.
 
def _chunk(text: str, size: int = RESEARCH_CHUNK_SIZE, overlap: int = RESEARCH_CHUNK_OVERLAP) -> list:
    """
    Section-aware chunking. Hard boundaries at markdown headings (#, ##, ###).
    Within each section: paragraph-based merging, sentence-boundary splits.
    Overlap only within a section, never across section boundaries.
    Heading is kept at the top of the first chunk of each section for RAG context.
    """
    import re as _re
 
    # Split on markdown headings -- each heading starts a new section
    section_re = _re.compile(r"(?=^#{1,3}\s)", _re.MULTILINE)
    raw_sections = section_re.split(text)
    raw_sections = [s.strip() for s in raw_sections if s.strip()]
 
    # No headings found -- treat entire text as one section
    if not raw_sections:
        raw_sections = [text]
 
    chunks = []
 
    def _chunk_section(section_text: str):
        raw_paras = [p.strip() for p in _re.split(r"\n{2,}", section_text) if p.strip()]
        current = ""
        _ov = ""
 
        for para in raw_paras:
            if len(current) + len(para) + 2 <= size:
                current = (current + "\n\n" + para).strip() if current else para
            else:
                if current and len(current) > 50:
                    chunks.append(current)
                    _ov = current[-overlap:] if overlap and len(current) > overlap else ""
                else:
                    _ov = ""
 
                if len(para) <= size:
                    current = (_ov + " " + para).strip() if _ov else para
                else:
                    current = ""
                    sentences = _re.split(r"(?<=[.!?])\s+", para)
                    buf = _ov
                    for sent in sentences:
                        if len(buf) + len(sent) + 1 <= size:
                            buf = (buf + " " + sent).strip() if buf else sent
                        else:
                            if buf and len(buf) > 50:
                                chunks.append(buf)
                                _ov = buf[-overlap:] if overlap and len(buf) > overlap else ""
                                buf = (_ov + " " + sent).strip() if _ov else sent
                            else:
                                buf = sent
                    if buf and len(buf) > 50:
                        current = buf
 
        if current and len(current) > 50:
            chunks.append(current)
 
    for section in raw_sections:
        _chunk_section(section)
 
    return chunks
    """
    Paragraph-based chunking ->?? splits on blank lines first.
    Paragraphs longer than `size` chars are split at sentence boundaries.
    Adjacent short paragraphs are merged until approaching `size`.
    """
    import re as _re

    # Split into paragraphs on blank lines
    raw_paras = [p.strip() for p in _re.split(r"\n{2,}", text) if p.strip()]
    chunks  = []
    current = ""
    for para in raw_paras:

        # Para fits in current chunk ->?? merge
        if len(current) + len(para) + 2 <= size:
            current = (current + "\n\n" + para).strip() if current else para
        else:
            # Save current chunk if non-empty
            if current and len(current) > 50:
                chunks.append(current)
                _ov = current[-overlap:] if overlap and len(current) > overlap else ""
            else:
                _ov = ""
            # Para itself fits in one chunk
            if len(para) <= size:
                current = (_ov + " " + para).strip() if _ov else para
            else:
                # Split long para at sentence boundaries
                current = ""
                sentences = _re.split(r"(?<=[.!?])\s+", para)
                buf = ""
                for sent in sentences:
                    if len(buf) + len(sent) + 1 <= size:
                        buf = (buf + " " + sent).strip() if buf else sent
                    else:
                        if buf and len(buf) > 50:
                            chunks.append(buf)
                            _ov = buf[-overlap:] if overlap and len(buf) > overlap else ""
                            buf = (_ov + " " + sent).strip() if _ov else sent
                        else:
                            buf = sent
                if buf and len(buf) > 50:
                    # Last sentence buf ->?? start new current
                    current = buf
    if current and len(current) > 50:
        chunks.append(current)
    return chunks
# --- Progress bar ---------------------------------------

def _progress(current: int, total: int, label: str = "") -> None:
    bar_len = 30
    filled  = int(bar_len * current / max(total, 1))
    bar     = "#" * filled + "." * (bar_len - filled)
    sys.stdout.write(f"\r   Fetching {total} stron  [{bar}]  {current}/{total}  {label[:20]}")
    sys.stdout.flush()
# --- Main function (run_research) -----------------------

def _generate_search_queries(topic: str, category: str,
                              article_focus: str = "",
                              slug: str = "",
                              seed_queries: list = None,
                              article_type: str = "") -> list:
    """
    Use a fast LLM to generate 4-5 targeted English search queries
    from the topic and optional article focus.
    Always uses DEV model (qwen2.5:7b) -- fast, ~5-8s.
    Returns list of query strings, falls back to [topic] on failure.

    slug, seed_queries, article_type are used to anchor queries to the
    specific subject and prevent overly broad franchise/series queries.
    """
    focus_block = f"\nArticle focus/angle: {article_focus}" if article_focus else ""

    # Build anchor block from slug keywords and seed_queries
    # These are the most reliable signals of what is actually being researched
    anchor_terms = []
    if slug:
        # slug words are the clearest identifier of the specific subject
        slug_words = [w for w in slug.replace("-", " ").split()
                      if len(w) > 3 and w.lower() not in
                      {"with", "from", "that", "this", "have", "been", "will", "your"}]
        if slug_words:
            anchor_terms.append("Slug keywords (highest priority): " + " ".join(slug_words[:6]))

    if seed_queries:
        # Take first 2 seed queries as concrete title anchors
        anchor_terms.append("Known titles/headlines: " + " | ".join(seed_queries[:2]))

    type_hint = ""
    if article_type:
        type_map = {
            "games_announcement": "This is a specific game ANNOUNCEMENT. Queries must target THIS specific release, not the broader franchise or series.",
            "games_review":       "This is a game REVIEW topic. Target reviews, hands-on impressions, and critic analysis.",
            "games_analysis":     "This is an ANALYSIS topic. Target opinion pieces, retrospectives, and industry commentary.",
            "ai_technical":       "This is a TECHNICAL AI topic. Target papers, benchmarks, and technical breakdowns.",
            "ai_news":            "This is an AI NEWS topic. Target recent announcements and releases only.",
            "hardware_review":    "This is a HARDWARE REVIEW topic. Target benchmarks, hands-on reviews, and comparisons.",
        }
        type_hint = f"\nArticle type: {type_map.get(article_type, article_type)}"

    anchor_block = ""
    if anchor_terms:
        anchor_block = "\nAnchors (use these to keep queries specific):\n" + "\n".join(f"  - {a}" for a in anchor_terms)

    prompt = f"""You are a search query generator for a research system.
Topic: {topic}
Category: {category}{focus_block}{type_hint}{anchor_block}

Generate 4 targeted English search queries to find high-quality articles about this SPECIFIC topic.
Rules:
- Queries must be in English regardless of topic language
- Each query should target a different angle (news, technical details, reactions, comparisons)
- Use specific keywords from the anchors above -- DO NOT generate broad franchise or series queries
- If the topic is a specific product/release/DLC, every query must include its specific name
- No quotes, no operators, just plain keywords
- If topic is not in English, translate the core subject first
- BAD example for "Songs of the Past DLC": "The Witcher 3 Wild Hunt" (too broad -- entire game)
- GOOD example: "Songs of the Past expansion 2027 CD Projekt" (specific release)
Respond with ONLY a JSON array of strings, no explanation, no markdown:
["query 1", "query 2", "query 3", "query 4"]"""
    try:
        resp = requests.post(
            f"{OLLAMA_URL}/api/generate",
            json={
                "model":   "qwen2.5:7b",
                "prompt":  prompt,
                "stream":  False,
                "options": {"temperature": 0.3, "num_predict": 200},
            },
            timeout=30,
        )
        resp.raise_for_status()
        raw = resp.json().get("response", "").strip()

        # Parse JSON ->?? strip markdown fences if present
        raw = re.sub(r"```json|```", "", raw).strip()

        # Find JSON array
        match = re.search(r'\[.*?\]', raw, re.DOTALL)
        if match:
            import json as _json
            queries = _json.loads(match.group())
            queries = [q.strip() for q in queries if isinstance(q, str) and len(q.strip()) > 5]
            # Remove queries containing CJK characters (model language bleed)
            import re as _re_cjk
            queries = [q for q in queries if not _re_cjk.search(r'[\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7af]', q)]
            if not queries:
                queries = [topic[:80]]
            if queries:
                return queries[:5]
    except Exception as e:
        pass

    # Fallback ->?? basic keyword extraction
    words = re.sub(r"[^\w\s]", "", topic).split()
    stopwords = {"the","a","an","and","or","of","for","in","on","z","co",
                 "do","nam","sie","jest","na","ze","jak","co","czy"}
    keywords = [w for w in words if w.lower() not in stopwords and len(w) > 3]
    return [" ".join(keywords[:5])] if keywords else [topic]

def run_research(item_ids: list = None) -> int:
    """
    Deep Research for pending queue items.
    item_ids=None ->?? all pending.
    Uses per-category Qdrant collections with hybrid vectors (dense + sparse).
    """
    if item_ids:
        all_items = queue.get_all()
        items = [i for i in all_items if i["id"] in item_ids]
    else:
        items = queue.get_pending()
    if not items:
        print("  No pending topics in queue.")
        return 0
    print("\n" + "-" * 60)
    print(f"  DEEP RESEARCH  ({len(items)} topics)")
    print("-" * 60)
    try:
        client = QdrantClient(url=QDRANT_URL)
    except Exception as e:
        print(f"  [qdrant] Connection error: {e}")
        return 0
    done_count = 0
    for idx, item in enumerate(items, 1):
        topic    = item["topic"]
        cat      = item["category"]
        item_id  = item["id"]
        slug     = item.get("slug", "")

        # Punkt 1: per-category collection
        col_name = research_collection(cat)
        print(f"\n  [{idx}/{len(items)}] {topic[:65]}")
        print(f"   Category: {cat} ->??  Collection: {col_name}")

        # Ensure this category's collection exists with hybrid config
        _ensure_collection(client, col_name)

        # Old chunks deleted AFTER new data is confirmed non-empty.
        # See post-upsert block below. Deleting here would destroy good
        # data if seed URLs are CF-blocked and fallback returns thin results.
        t_start = time.time()

        # 1. Seed URLs ->?? fetch first if provided
        seed_urls = item.get("seed_urls", [])
        seed_pages = []
        if seed_urls:
            print(f"   [>>] ?? Fetching {len(seed_urls)} seed URL(s) first...")
            for url in seed_urls:
                # Skip video platforms
                if any(d in url for d in ("youtube.com", "youtu.be", "twitch.tv", "tiktok.com", "vimeo.com")):
                    print(f"     \u26a0 Skipping video URL: {url[:60]}")
                    continue
                if _is_blocked(url):
                    print(f"     \u26a0 Skipping blocked domain: {url[:60]}")
                    continue
                full = _fetch_page(url)
                # Fallback: try with different User-Agent if trafilatura failed
                if not full:
                    try:
                        import re as _re2
                        for ua in [
                            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
                            "Googlebot/2.1 (+http://www.google.com/bot.html)",
                        ]:
                            r = requests.get(url, timeout=8, headers={"User-Agent": ua})
                            if r.status_code == 200 and len(r.text) > 500:
                                # Try trafilatura on the response
                                try:
                                    import trafilatura as _tf
                                    text = _tf.extract(r.text, include_comments=False,
                                                       favor_precision=True)
                                    if text and len(text.strip()) > 100:
                                        full = text.strip()[:25000]
                                        break
                                except Exception:
                                    pass
                                # Fallback: strip HTML
                                text = _re2.sub(r"<[^>]+>", " ", r.text)
                                text = _re2.sub(r"\s+", " ", text).strip()
                                if len(text) > 200:
                                    full = text[:25000]
                                    break
                    except Exception:
                        pass
                if full and len(full.strip()) >= 500:
                    trust_meta = get_domain_trust(url, cat)
                    seed_pages.append({
                        "url":    url,
                        "title":  url,
                        "text":   full,
                        "source": "seed_url",
                        "trust":  trust_meta,
                    })
                    print(f"     [>>] {url[:65]}  ({len(full)} chars)")
                else:
                    # Seed URL failed or returned too little content (Cloudflare / paywall)
                    # Fallback: SearXNG query using seed_queries titles or slug
                    _fb_reason = f"only {len(full.strip()) if full else 0} chars" if full else "no content"
                    print(f"     [>>] {url[:65]}  ({_fb_reason}) -- trying SearXNG fallback...")

                    # Build fallback query from seed_queries (URL titles) or slug.
                    # Use the LONGEST seed query -- longer = more specific = better results.
                    # "Witcher 3 Songs of the Past DLC a proper big expansion" beats
                    # "The Witcher 3: Wild Hunt" even though both are seed queries.
                    _seed_qs = item.get("seed_queries", [])
                    _slug    = item.get("slug", "").replace("-", " ")
                    _fb_query = ""
                    if _seed_qs:
                        # Pick longest seed query (most specific)
                        _fb_query = max(_seed_qs, key=len)
                    if not _fb_query and _slug:
                        _fb_query = _slug

                    if _fb_query:
                        try:
                            _fb_results = fetch_all(
                                query    = _fb_query[:100],
                                category = cat,
                                deep     = False,
                                skip_rss = True,
                            )
                            _fb_new = [r for r in _fb_results
                                       if r.get("url") not in {p["url"] for p in seed_pages}]
                            if _fb_new:
                                print(f"       + SearXNG fallback: {len(_fb_new)} results for: {_fb_query[:60]}")
                                # Add to raw pool -- will be fetched and embedded with rest of research
                                if "_fallback_raw" not in dir():
                                    _fallback_raw = []
                                _fallback_raw.extend(_fb_new)
                            else:
                                print(f"       - SearXNG fallback: no new results")
                        except Exception as _fb_err:
                            print(f"       - SearXNG fallback failed: {_fb_err}")
                    else:
                        print(f"       - No fallback query available (no seed_queries or slug)")

        # Merge seed URL fallback results into raw pool if any
        if "_fallback_raw" in dir() and _fallback_raw:
            print(f"   [>>] Merging {len(_fallback_raw)} seed fallback results into research pool")
        else:
            _fallback_raw = []

        # 2. SearXNG + other sources ->?? LLM-generated search queries
        print(f"   [>>] ?? Generating search queries...")
        article_focus = item.get("article_focus", "")
        search_queries = _generate_search_queries(
            topic        = topic,
            category     = cat,
            article_focus = article_focus,
            slug         = item.get("slug", ""),
            seed_queries = item.get("seed_queries", []),
            article_type = item.get("article_type", ""),
        )
        print(f"   [>>] Queries: {search_queries}")
        print(f"   [>>] ?? Fetching sources...")

        # RSS disabled in research ->?? fetches generic feed items unrelated to topic
        # RSS is for Discovery only (finding new topics)
        raw       = fetch_all(query=search_queries[0], category=cat, deep=True, skip_rss=True)
        # Add fallback results that aren't already in raw
        if _fallback_raw:
            _fb_new_urls = {r["url"] for r in raw}
            for _fb_r in _fallback_raw:
                if _fb_r.get("url") not in _fb_new_urls:
                    raw.append(_fb_r)
                    _fb_new_urls.add(_fb_r["url"])
        seen_urls = {r["url"] for r in raw}

        # Run additional queries for extra coverage
        if len(search_queries) > 1:
            for sq in search_queries[1:]:
                extra = fetch_all(query=sq, category=cat, deep=False, skip_rss=True)
                new   = [e for e in extra if e["url"] not in seen_urls]
                for e in new:
                    seen_urls.add(e["url"])
                raw.extend(new)
            print(f"   [>>] ?? {len(raw)} total results from {len(search_queries)} queries")

        # seed_queries ->?? run extra fetches for merged topics
        # Extract meaningful keywords from titles, skip if too similar to primary
        seed_queries = item.get("seed_queries", [])
        if seed_queries:
            import re as _re
            # Build set of words already in primary topic (for dedup)
            primary_words = set(_re.sub(r"[^\w]", " ", topic.lower()).split())
            unique_queries = []
            seen_queries   = {topic.lower()}
            for sq in seed_queries:
                if sq.lower() in seen_queries:
                    continue
                # Extract keywords: remove stopwords, keep meaningful words
                words = _re.sub(r"[^\w\s]", "", sq.lower()).split()
                stopwords = {"the","a","an","and","or","of","for","in","on",
                             "at","to","is","it","its","this","that","with",
                             "from","by","as","are","was","were","be","been",
                             "has","have","had","not","but","what","how",
                             "wikipedia","wiki","reddit","youtube","steam"}
                keywords = [w for w in words
                            if w not in stopwords and len(w) > 3
                            and w not in primary_words]
                if not keywords:
                    continue
                # Build a targeted query from top 3 unique keywords
                query_str = " ".join(keywords[:3])
                if query_str not in seen_queries:
                    unique_queries.append(query_str)
                    seen_queries.add(query_str)
            if unique_queries:
                print(f"   [>>] ?? Running {len(unique_queries)} seed queries...")
                seen_urls = {r["url"] for r in raw}
                for sq in unique_queries:
                    extra = fetch_all(query=f"{topic} {sq}", category=cat, deep=False, skip_rss=True)
                    new   = [e for e in extra if e["url"] not in seen_urls]
                    for e in new:
                        seen_urls.add(e["url"])
                    raw.extend(new)
                    print(f"     + {len(new)} new results for: {sq}")

        # Leave room for seed pages in the total budget
        budget = RESEARCH_FETCH_MAX - len(seed_pages)
        raw = raw[:max(budget, 0)]
        print(f"   [>>] ?? {len(raw)} results from sources  +  {len(seed_pages)} seed URL(s)")
        if not raw and not seed_pages:
            queue.update_status(item_id, queue.STATUS_ERROR, "No results from fetch")
            continue

        # 3. Fetch full content for SearXNG results + merge with seed pages
        pages_text = list(seed_pages)  # seed pages already fetched
        _skipped_prefilter = 0
        for i, r in enumerate(raw):
            _progress(i + 1, len(raw))
            if _is_blocked(r["url"]):
                continue
            _skip, _skip_reason = _should_skip_url(r["url"])
            if _skip:
                _skipped_prefilter += 1
                continue
            full = _fetch_page(r["url"])
            if not full:
                continue  # skip snippets ->?? too short to be useful in Qdrant
            text = full
            # Relevance check: page must contain at least 2 topic keywords
            import re as _re
            topic_words = set(w for w in _re.sub(r"[^\w]", " ", topic.lower()).split()
                             if len(w) > 3)
            text_lower = text.lower()
            matches = sum(1 for w in topic_words if w in text_lower)
            if len(topic_words) >= 3 and matches < 2:
                continue  # Skip irrelevant pages
            trust_meta = get_domain_trust(r["url"], cat)
            pages_text.append({
                "url":    r["url"],
                "title":  r["title"],
                "text":   text,
                "source": r["source"],
                "trust":  trust_meta,
            })
        full_count    = len(pages_text)
        skipped_count = len(raw) - (full_count - len(seed_pages))
        if _skipped_prefilter:
            print(f"   [>>] Pre-filter skipped: {_skipped_prefilter} URLs (binary/nav/low-yield)")

        # Snippet fallback ->?? when very few full text pages, use snippets as backup
        SNIPPET_FALLBACK_THRESHOLD = 5  # use snippets if fewer than 5 full text pages
        if full_count < SNIPPET_FALLBACK_THRESHOLD:
            snippet_added = 0
            for r in raw:
                snippet = r.get("snippet", "").strip()
                if not snippet or len(snippet) < 80:
                    continue
                # Skip if URL already in pages_text
                existing_urls = {p["url"] for p in pages_text}
                if r["url"] in existing_urls:
                    continue
                trust_meta = get_domain_trust(r["url"], cat)
                # Only add snippets from non-blocked domains
                pages_text.append({
                    "url":     r["url"],
                    "title":   r["title"],
                    "text":    snippet,
                    "source":  r["source"],
                    "trust":   {**trust_meta,
                                "trust_score": trust_meta.get("trust_score", 0) * 0.4,
                                "retrieval_boost": 0.3},  # low boost for snippets
                })
                snippet_added += 1
                if snippet_added >= 20:  # max 20 snippets as fallback
                    break
            if snippet_added:
                print(f"   [>>] ?? Snippet fallback: +{snippet_added} snippets (low boost)")
        trust_counts = {}
        for p in pages_text:
            lvl = p["trust"].get("domain_trust", "unknown")
            trust_counts[lvl] = trust_counts.get(lvl, 0) + 1
        trust_str = "  ".join(f"{k}:{v}" for k, v in sorted(trust_counts.items()))
        print(f"\n ->?? Full text: {full_count}  | Skipped (no full text): {skipped_count}")
        print(f"   [>>] ?? Trust: {trust_str}")

        # 3. Chunk + embed + index ->?? with progress bar
        all_chunks  = []
        rejected_gc = 0
        for page in pages_text:
            # Pre-filter: reject PDF binary and garbage text before chunking
            if _is_garbage_text(page["text"]):
                rejected_gc += 1
                continue
            try:
                from langdetect import detect as _ld
                if _ld(page["text"][:500]) != "en":
                    rejected_gc += 1
                    continue
            except Exception:
                pass
            for chunk in _chunk(page["text"]):
                if not _is_garbage_text(chunk):
                    all_chunks.append((chunk, page))
                else:
                    rejected_gc += 1
        if rejected_gc:
            print(f"   [>>] ?? Rejected {rejected_gc} garbage/binary chunks (PDF binary, low-quality text)")
        total_chunks = len(all_chunks)
        print(f"   [>>] ?? Embedding {total_chunks} chunks...")
        EMBED_BATCH = 64
        points   = []
        point_id = 0
        import uuid as _uuid
        def _new_id() -> int:
            """Generate unique integer ID from UUID."""
            return _uuid.uuid4().int >> 64  # 64-bit positive int
        bar_len  = 30
        processed = 0
        for batch_start in range(0, total_chunks, EMBED_BATCH):
            batch = all_chunks[batch_start:batch_start + EMBED_BATCH]
            texts = [chunk for chunk, _ in batch]
            # Progress bar
            filled = int(bar_len * processed / max(total_chunks, 1))
            bar    = "#" * filled + "." * (bar_len - filled)
            sys.stdout.write(f"\r   Embedding  [{bar}]  {processed}/{total_chunks}")
            sys.stdout.flush()
            vecs = _embed_batch(texts)
            for (chunk, page), vec in zip(batch, vecs):
                processed += 1
                if not vec:
                    continue
                t = page["trust"]
                # Punkt 4: hybrid vector ->?? dense (semantic) + sparse (BM25 keywords)
                sparse = _sparse_vector(chunk)
                # Enriched metadata
                import datetime as _dt
                _now_iso = _dt.datetime.utcnow().isoformat()
                # Detect freshness type
                _domain = t.get("domain", "")
                if not _domain and page.get("url"):
                    import urllib.parse as _up
                    _domain = _up.urlparse(page["url"]).netloc.lower().removeprefix("www.")
                _tier   = t.get("domain_trust", "unknown")
                if _tier in ("press", "trusted"):
                    _freshness = "news"
                elif any(k in page["url"] for k in ["arxiv.org", "docs.", "wiki", "wikipedia"]):
                    _freshness = "reference"
                else:
                    _freshness = "news"
                # Extract content_date from page if available
                _content_date = page.get("published_date", "") or ""
                # Detect version tag (e.g. "5.8", "v2.0", "3.5")
                import re as _re2
                _ver_match = _re2.search(r'\b(\d+\.\d+(?:\.\d+)?)\b', topic)
                _version_tag = _ver_match.group(1) if _ver_match else ""
                # Subject = first 2-3 meaningful words from topic
                _stop = {"the","a","an","and","or","of","for","in","on","is","are","was"}
                _subj_words = [w for w in topic.split() if w.lower() not in _stop][:4]
                _subject = " ".join(_subj_words)
                points.append(PointStruct(
                    id     = _new_id(),
                    vector = {
                        "dense":  vec,
                        "sparse": sparse,
                    },
                    payload = {
                        "topic":           topic,
                        "category":        cat,
                        "item_id":         item_id,
                        "url":             page["url"],
                        "title":           page["title"],
                        "source":          page["source"],
                        "text":            chunk,
                        "domain":          _domain,
                        "domain_trust":    _tier,
                        "trust_score":     t.get("trust_score", 0),
                        "trust_reason":    t.get("trust_reason", ""),
                        "content_type":    t.get("content_type", "other"),
                        "subcategory":     t.get("subcategory", "other"),
                        "language":        t.get("language", "unknown"),
                        "retrieval_boost": t.get("retrieval_boost", 0.4),
                        # New enriched metadata
                        "indexed_at":      _now_iso,
                        "content_date":    _content_date,
                        "freshness":       _freshness,
                        "subject":         _subject,
                        "version_tag":     _version_tag,
                        "chunk_idx":       len(points),
                        "knowledge":       False,
                        "topic_slug":      slug,
                    },
                ))
                # point_id not needed ->?? using UUID

        # Ostatni update progress bara
        bar = "#" * bar_len
        sys.stdout.write(f"\r   Embedding  [{bar}]  {total_chunks}/{total_chunks}")
        sys.stdout.flush()
        print()  # nowa linia
        if not points:
            # Do NOT delete old chunks -- preserve them if research failed.
            queue.update_status(item_id, queue.STATUS_ERROR, "No vectors after embedding")
            continue
        # New data confirmed non-empty: now safe to replace old chunks.
        try:
            _old_scroll = client.scroll(
                collection_name = col_name,
                scroll_filter   = {"filter": {"must": [{"key": "item_id", "match": {"value": item_id}}]}},
                limit=1, with_payload=False, with_vectors=False,
            )
            if _old_scroll[0]:
                client.delete(
                    collection_name = col_name,
                    points_selector = {"filter": {"must": [{"key": "item_id", "match": {"value": item_id}}]}},
                )
                print(f"   [>>] Replaced old chunks for [{item_id[:8]}] (new: {len(points)})")
        except Exception:
            pass  # delete failed -- upsert will add alongside old, acceptable
        BATCH = 64
        for i in range(0, len(points), BATCH):
            client.upsert(collection_name=col_name, points=points[i:i+BATCH])
        elapsed = time.time() - t_start
        print(f"   [>>] ?? Indexed {len(points)} chunks to Qdrant  ({elapsed:.1f}s)")

        # knowledge_evergreen auto-populate DISABLED ? manual feed only via [K] Feed
        queue.update_status(item_id, queue.STATUS_RESEARCHED)
        print(f"   [>>] ?? Status ->?? researched")

        # Auto-clean garbage chunks immediately after research
        try:
            from menus.knowledge.review import _auto_clean as _do_clean
            clean_result = _do_clean(client, col_name, QDRANT_URL)
            removed = clean_result.get("removed", 0)
            if removed:
                print(f"   [>>] ?? Auto-clean: -{removed} garbage chunks")
        except Exception as _ce:
            pass  # non-critical -- skip silently

        done_count += 1
    print(f"\n ->?? Deep Research done: {done_count}/{len(items)} topics.\n")
    return done_count

def delete_vectors_for_items(item_ids: list) -> int:
    """
    Delete Qdrant vectors for given item_ids across all category collections.
    Returns number of items cleaned.
    """
    if not item_ids:
        return 0
    try:
        from qdrant_client import QdrantClient
        from qdrant_client.models import Filter, FieldCondition, MatchAny
        from config import RESEARCH_CATEGORIES, research_collection
        client = QdrantClient(url=QDRANT_URL)
        deleted = 0
        for cat in RESEARCH_CATEGORIES:
            col_name = research_collection(cat)
            existing = [c.name for c in client.get_collections().collections]
            if col_name not in existing:
                continue
            try:
                client.delete(
                    collection_name = col_name,
                    points_selector = Filter(
                        must=[FieldCondition(
                            key   = "item_id",
                            match = MatchAny(any=item_ids),
                        )]
                    ),
                )
                deleted += 1
            except Exception as e:
                print(f"  [qdrant] Error deleting from {col_name}: {e}")
        print(f"  [qdrant] Deleted vectors for {len(item_ids)} item(s) across {deleted} collection(s).")
        return len(item_ids)
    except Exception as e:
        print(f"  [qdrant] Error deleting vectors: {e}")
        return 0
