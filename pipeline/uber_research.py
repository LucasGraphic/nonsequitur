# pipeline/uber_research.py
# Gap analysis + targeted second research pass.
# Called from research_run.py when suitability gate returns THIN,
# or manually via queue [r] -> [3].
#
# Improvements (S32 v2):
#   - Gap prompt explicitly uses focus angle
#   - Query construction: extracts keywords from question instead of full sentence
#   - BM25 URL pre-scoring before fetching (skip low-relevance URLs)
#   - seen_urls shared across ALL questions (true global dedup)
#   - max_urls reduced to 5 (fewer but better)
#   - relevance threshold raised to 3 keyword matches
#   - auto-clean after indexing

from __future__ import annotations

import re
import uuid
import requests
from typing import Any

# ---------------------------------------------------------------------------
# Gap analysis
# ---------------------------------------------------------------------------

_GAP_PROMPT = """You are a research editor. You have partial research on a topic.
Your job: identify what is MISSING, not what is present.

Topic: {topic}
Article focus (the specific angle being argued): {focus}

Research collected so far ({chunk_count} chunks):
---
{context}
---

List exactly {n_questions} specific questions that:
- Are directly relevant to the topic AND the focus angle above
- Are NOT answered by the research above
- Would provide concrete facts, data, or quotes that SUPPORT the focus angle
- Are narrow enough to find in a single article or interview

Output format: numbered list only, one question per line, no preamble.
1. [question]
2. [question]

Questions:"""


def _build_gap_context(chunks: list[dict], max_chars: int = 8000) -> str:
    parts = []
    total = 0
    for c in chunks:
        text = c.get("text", "") or c.get("payload", {}).get("text", "")
        if not text:
            continue
        snippet = text[:300].replace("\n", " ").strip()
        parts.append(snippet)
        total += len(snippet)
        if total >= max_chars:
            break
    return "\n---\n".join(parts)


def _run_gap_analysis(
    topic: str,
    focus: str,
    chunks: list[dict],
    model: str,
    ollama_url: str,
    n_questions: int = 4,
) -> list[str]:
    context = _build_gap_context(chunks)
    if not context:
        print("   [uber] No context for gap analysis -- skipping.")
        return []

    prompt = _GAP_PROMPT.format(
        topic       = topic,
        focus       = focus if focus else "general coverage of the topic",
        chunk_count = len(chunks),
        context     = context,
        n_questions = n_questions,
    )

    try:
        from config import model_supports_thinking
        payload = {
            "model":   model,
            "prompt":  prompt,
            "stream":  False,
            "options": {"temperature": 0.3, "num_predict": 600},
        }
        if model_supports_thinking(model):
            payload["think"] = False

        r = requests.post(f"{ollama_url}/api/generate", json=payload, timeout=120)
        r.raise_for_status()
        raw = r.json().get("response", "").strip()

        questions = []
        for line in raw.split("\n"):
            line = line.strip()
            m = re.match(r"^\d+[\.\)]\s+(.+)$", line)
            if m:
                q = m.group(1).strip()
                if len(q) > 20:
                    questions.append(q)
        return questions[:n_questions]

    except Exception as e:
        print(f"   [uber] Gap analysis error: {e}")
        return []


# ---------------------------------------------------------------------------
# Query construction -- extract keywords from question
# ---------------------------------------------------------------------------

_STOPWORDS = {
    "what", "which", "where", "when", "who", "how", "why", "does", "did",
    "are", "were", "will", "would", "could", "should", "have", "has", "had",
    "been", "being", "that", "this", "these", "those", "the", "and", "or",
    "but", "for", "with", "from", "into", "onto", "upon", "than", "then",
    "specific", "specifically", "evidence", "exists", "based", "compared",
    "comparison", "between", "versus", "impact", "affect", "effect", "prove",
    "suggest", "indicate", "demonstrate", "show", "whether", "not", "any",
    "some", "more", "less", "most", "least", "very", "also", "such", "each",
    "their", "they", "them", "its", "our", "your", "there", "here", "about",
    "after", "before", "during", "since", "while", "although", "because",
}

def _question_to_query(topic: str, question: str, max_words: int = 6) -> str:
    """
    Extract the most distinctive keywords from a gap question.
    Combines topic keywords with question-specific terms for a tight SearXNG query.
    """
    # Topic keywords (short, 1-2 key terms)
    topic_words = [
        w for w in re.sub(r"[^\w\s]", "", topic.lower()).split()
        if len(w) > 3 and w not in _STOPWORDS
    ][:2]

    # Question keywords (content nouns, not question words)
    q_words = [
        w for w in re.sub(r"[^\w\s]", "", question.lower()).split()
        if len(w) > 3 and w not in _STOPWORDS
    ]

    # Combine: topic first, then most distinctive question words
    combined = topic_words[:]
    for w in q_words:
        if w not in combined:
            combined.append(w)
        if len(combined) >= max_words:
            break

    return " ".join(combined)


# ---------------------------------------------------------------------------
# BM25 URL pre-scoring
# ---------------------------------------------------------------------------

def _bm25_url_score(title: str, snippet: str, query_tokens: set) -> float:
    """
    Quick relevance score for a SearXNG result before fetching.
    Uses title + snippet text only (no full page fetch).
    """
    text = (title + " " + snippet).lower()
    text_tokens = re.findall(r"[a-z0-9]+", text)
    if not text_tokens:
        return 0.0
    matches = sum(1 for t in query_tokens if t in text)
    return matches / max(len(query_tokens), 1)


# ---------------------------------------------------------------------------
# SearXNG search
# ---------------------------------------------------------------------------

def _searxng_search(
    query: str,
    searxng_url: str,
    max_results: int = 5,
) -> list[dict]:
    try:
        params = {
            "q":          query,
            "format":     "json",
            "language":   "en",
            "categories": "general",
        }
        r = requests.get(f"{searxng_url}/search", params=params, timeout=15)
        r.raise_for_status()
        results = r.json().get("results", [])
        return [
            {
                "url":     res["url"],
                "title":   res.get("title", ""),
                "snippet": res.get("content", ""),
            }
            for res in results[:max_results]
        ]
    except Exception as e:
        print(f"   [uber] SearXNG error for '{query[:50]}': {e}")
        return []


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def run_uber_research(
    item: dict,
    col_name: str,
    client: Any,
    ollama_url: str,
    model: str,
    *,
    is_night_run: bool  = False,
    n_questions: int    = 4,
    max_urls_per_q: int = 5,
) -> dict:
    """
    Run gap analysis + targeted search pass for a queue item.

    Returns dict: {questions, searched, new_chunks, total_after, skipped}
    """
    from config import QDRANT_URL

    item_id  = item["id"]
    topic    = item["topic"]
    focus    = item.get("article_focus", "")
    category = item.get("category", "other")

    stats = {
        "questions":   [],
        "searched":    0,
        "new_chunks":  0,
        "total_after": 0,
        "skipped":     0,
    }

    print()
    print("   [uber] Starting gap analysis...")

    # 1. Scroll existing chunks
    from qdrant_client.models import Filter, FieldCondition, MatchValue
    chunks_raw = []
    try:
        offset = None
        while True:
            batch, next_offset = client.scroll(
                collection_name = col_name,
                scroll_filter   = Filter(must=[FieldCondition(
                    key="item_id", match=MatchValue(value=item_id)
                )]),
                limit        = 500,
                offset       = offset,
                with_payload = True,
                with_vectors = False,
            )
            chunks_raw.extend([p.payload for p in batch])
            if next_offset is None or len(batch) < 500:
                break
            offset = next_offset
        print(f"   [uber] Loaded {len(chunks_raw)} existing chunks for gap analysis")
    except Exception as e:
        print(f"   [uber] Could not load chunks: {e}")
        return stats

    if not chunks_raw:
        print("   [uber] No existing chunks -- skipping.")
        return stats

    # BM25 sort for context selection
    topic_toks = set(re.findall(r"[a-z0-9]+", topic.lower()))

    def _bm25(c):
        txt = (c.get("text") or "").lower()
        return sum(1 for t in topic_toks if t in txt)

    chunks_sorted = sorted(chunks_raw, key=_bm25, reverse=True)[:60]

    # 2. Gap analysis
    questions = _run_gap_analysis(
        topic       = topic,
        focus       = focus,
        chunks      = chunks_sorted,
        model       = model,
        ollama_url  = ollama_url,
        n_questions = n_questions,
    )

    if not questions:
        print("   [uber] Gap analysis returned no questions.")
        return stats

    stats["questions"] = questions

    print(f"   [uber] Gap questions ({len(questions)}):")
    for i, q in enumerate(questions, 1):
        print(f"     [{i}] {q}")

    if not is_night_run:
        print()
        ans = input("   [uber] Search for these gaps? [Y/n]: ").strip().lower()
        if ans == "n":
            print("   [uber] Uber Research cancelled.")
            return stats

    # 3. Targeted search
    try:
        from config import SEARXNG_URL
    except ImportError:
        print("   [uber] SEARXNG_URL not in config -- cannot search.")
        return stats

    from pipeline.research_run import (
        _fetch_page, _is_blocked, _should_skip_url,
        _embed_batch, _sparse_vector, _chunk,
    )
    from pipeline.content_filter import is_garbage as _is_garbage
    from domain_config import get_domain_trust
    import datetime as _dt

    # Global seen_urls -- shared across ALL questions
    seen_urls: set = set()
    for c in chunks_raw:
        url = c.get("url", "")
        if url:
            seen_urls.add(url)

    # Topic words for relevance filter
    _topic_words = set(
        w for w in re.sub(r"[^\w]", " ", topic.lower()).split()
        if len(w) > 3
    )
    _RELEVANCE_MIN_MATCHES = 3  # raised from 2

    new_points = []

    for qi, question in enumerate(questions, 1):
        # Build tight query from keywords
        query = _question_to_query(topic, question, max_words=6)
        query_toks = set(re.findall(r"[a-z0-9]+", query.lower()))

        print(f"\n   [uber] [{qi}/{len(questions)}] Q: {question[:70]}")
        print(f"   [uber]   -> query: '{query}'")

        results = _searxng_search(query, SEARXNG_URL, max_results=max_urls_per_q)
        stats["searched"] += len(results)

        # BM25 pre-score and sort results before fetching
        scored_results = []
        for res in results:
            score = _bm25_url_score(res["title"], res["snippet"], query_toks)
            scored_results.append((score, res))
        scored_results.sort(key=lambda x: x[0], reverse=True)

        fetched = 0
        for score, res in scored_results:
            url = res.get("url", "")
            if not url or url in seen_urls:
                continue
            if _is_blocked(url):
                continue
            skip, _ = _should_skip_url(url)
            if skip:
                stats["skipped"] += 1
                continue

            # Skip low-relevance URLs based on title+snippet before fetching
            if score < 0.2 and len(query_toks) >= 3:
                stats["skipped"] += 1
                continue

            text = _fetch_page(url, timeout=12)
            if not text or len(text) < 200:
                continue

            # Relevance filter on full page text
            _text_lower = text.lower()
            _matches = sum(1 for w in _topic_words if w in _text_lower)
            if len(_topic_words) >= 3 and _matches < _RELEVANCE_MIN_MATCHES:
                stats["skipped"] += 1
                continue

            seen_urls.add(url)
            trust_meta = get_domain_trust(url, category)

            page_chunks = [c for c in _chunk(text) if not _is_garbage(c)]
            if not page_chunks:
                continue

            vecs = _embed_batch(page_chunks)
            _domain = ""
            try:
                from urllib.parse import urlparse as _up
                _domain = _up(url).netloc.lower().removeprefix("www.")
            except Exception:
                pass

            _tier = trust_meta.get("domain_trust", "unknown")
            _now  = _dt.datetime.utcnow().isoformat()

            for chunk_text, vec in zip(page_chunks, vecs):
                if not vec:
                    continue
                sparse = _sparse_vector(chunk_text)
                from qdrant_client.models import PointStruct
                new_points.append(PointStruct(
                    id     = uuid.uuid4().int >> 64,
                    vector = {"dense": vec, "sparse": sparse},
                    payload = {
                        "topic":           topic,
                        "category":        category,
                        "item_id":         item_id,
                        "url":             url,
                        "title":           res.get("title", ""),
                        "source":          "uber_research",
                        "text":            chunk_text,
                        "domain":          _domain,
                        "domain_trust":    _tier,
                        "trust_score":     trust_meta.get("trust_score", 0),
                        "trust_reason":    trust_meta.get("trust_reason", ""),
                        "content_type":    trust_meta.get("content_type", "other"),
                        "retrieval_boost": trust_meta.get("retrieval_boost", 0.4),
                        "indexed_at":      _now,
                        "freshness":       "news",
                        "knowledge":       False,
                        "uber_question":   question,
                        "uber_query":      query,
                    },
                ))

            fetched += 1
            print(f"     + {url[:60]} ({len(page_chunks)} chunks)")

        if fetched == 0:
            print(f"     (no new content found)")

    # 4. Index
    if new_points:
        BATCH = 64
        for i in range(0, len(new_points), BATCH):
            client.upsert(collection_name=col_name, points=new_points[i:i+BATCH])
        stats["new_chunks"] = len(new_points)
        print(f"\n   [uber] Indexed {len(new_points)} new chunks to Qdrant")
    else:
        print("\n   [uber] No new chunks found.")

    # 5. Auto-clean garbage after indexing
    try:
        from menus.knowledge.review import _auto_clean as _do_clean
        clean_result = _do_clean(client, col_name, QDRANT_URL)
        removed = clean_result.get("removed", 0)
        if removed:
            print(f"   [uber] Auto-clean: -{removed} garbage chunks")
    except Exception:
        pass

    # 6. Count total after
    try:
        count_r = requests.post(
            f"{QDRANT_URL}/collections/{col_name}/points/count",
            json    = {"exact": False,
                       "filter": {"must": [{"key": "item_id", "match": {"value": item_id}}]}},
            timeout = 5,
        )
        stats["total_after"] = count_r.json().get("result", {}).get("count", 0)
    except Exception:
        stats["total_after"] = len(chunks_raw) + len(new_points)

    return stats
