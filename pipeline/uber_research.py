# pipeline/uber_research.py
# Gap analysis + targeted second research pass.
# Called from research_run.py when suitability gate returns THIN.
#
# Flow:
#   1. Scroll research chunks from Qdrant for item_id
#   2. Build context string from top-N chunks (BM25)
#   3. LLM gap analysis -> list of missing questions
#   4. For each question: SearXNG search -> fetch -> chunk -> embed -> index
#   5. Return stats: {questions, new_chunks, total_after}
#
# Night run: runs automatically, no prompts.
# Interactive: shows questions, confirms before searching.

from __future__ import annotations

import re
import sys
import time
import uuid
import requests
from typing import Any

# ---------------------------------------------------------------------------
# Gap analysis
# ---------------------------------------------------------------------------

_GAP_PROMPT = """You are a research editor. You have partial research on a topic.
Your job: identify what is MISSING, not what is present.

Topic: {topic}
Focus: {focus}

Research collected so far ({chunk_count} chunks):
---
{context}
---

List exactly {n_questions} specific questions that:
- Are directly relevant to the topic and focus
- Are NOT answered by the research above
- Would provide essential missing facts, context, or perspectives for a complete article

Output format: numbered list only, one question per line, no commentary.
Example:
1. What are the specific hardware requirements for running X on consumer GPUs?
2. How does X compare to Y in benchmark Z released after 2024?

Questions:"""

def _build_gap_context(chunks: list[dict], max_chars: int = 8000) -> str:
    """Build context string from chunk payloads for gap analysis prompt."""
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
    """
    Call LLM to identify missing research questions.
    Returns list of question strings.
    """
    context = _build_gap_context(chunks)
    if not context:
        print("   [uber] No context for gap analysis -- skipping.")
        return []

    prompt = _GAP_PROMPT.format(
        topic       = topic,
        focus       = focus or "none specified",
        chunk_count = len(chunks),
        context     = context,
        n_questions = n_questions,
    )

    try:
        from config import model_supports_thinking
        _think = model_supports_thinking(model)
        payload = {
            "model":       model,
            "prompt":      prompt,
            "stream":      False,
            "options":     {"temperature": 0.3, "num_predict": 600},
        }
        if _think:
            payload["think"] = False  # gap analysis does not need CoT

        r = requests.post(
            f"{ollama_url}/api/generate",
            json    = payload,
            timeout = 120,
        )
        r.raise_for_status()
        raw = r.json().get("response", "").strip()

        # Parse numbered list
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
# Targeted search + index
# ---------------------------------------------------------------------------

def _searxng_search(query: str, category: str, searxng_url: str, max_results: int = 15) -> list[dict]:
    """Single SearXNG search, returns list of {url, title} dicts."""
    try:
        params = {
            "q":        query,
            "format":   "json",
            "language": "en",
            "categories": "general",
        }
        r = requests.get(f"{searxng_url}/search", params=params, timeout=15)
        r.raise_for_status()
        results = r.json().get("results", [])
        return [{"url": res["url"], "title": res.get("title", "")} for res in results[:max_results]]
    except Exception as e:
        print(f"   [uber] SearXNG error for '{query[:50]}': {e}")
        return []


def run_uber_research(
    item: dict,
    col_name: str,
    client: Any,                 # QdrantClient
    ollama_url: str,
    model: str,
    *,
    is_night_run: bool = False,
    n_questions: int   = 4,
    max_urls_per_q: int = 8,
) -> dict:
    """
    Run gap analysis + targeted search pass for a queue item.

    Parameters
    ----------
    item         : queue item dict (must have id, topic, category, article_focus)
    col_name     : Qdrant research collection name
    client       : QdrantClient instance
    ollama_url   : Ollama base URL
    model        : model name for gap analysis (default: NORMAL from config)
    is_night_run : if True, no interactive prompts
    n_questions  : number of gap questions to generate
    max_urls_per_q: max URLs to fetch per question

    Returns
    -------
    dict with keys: questions, searched, new_chunks, total_after, skipped
    """
    from config import QDRANT_URL, OLLAMA_EMBED_URL, EMBED_MODEL
    from config import RESEARCH_CHUNK_SIZE, RESEARCH_CHUNK_OVERLAP

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

    # 1. Scroll existing chunks from Qdrant
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
        print("   [uber] No existing chunks -- skipping uber research.")
        return stats

    # Simple BM25-style sort by topic keyword frequency for context selection
    topic_toks = set(re.findall(r"[a-z0-9]+", topic.lower()))

    def _bm25_score(c):
        txt = (c.get("text") or "").lower()
        return sum(1 for t in topic_toks if t in txt)

    chunks_sorted = sorted(chunks_raw, key=_bm25_score, reverse=True)[:60]

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

    # Interactive confirmation (skip in night_run)
    if not is_night_run:
        print()
        ans = input("   [uber] Search for these gaps? [Y/n]: ").strip().lower()
        if ans == "n":
            print("   [uber] Uber Research cancelled.")
            return stats

    # 3. Targeted search per question
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

    seen_urls: set = set()
    # Pre-populate seen_urls from existing chunks to avoid re-fetching
    for c in chunks_raw:
        url = c.get("url", "")
        if url:
            seen_urls.add(url)

    new_points = []

    for qi, question in enumerate(questions, 1):
        query = f"{topic} {question}"
        print(f"\n   [uber] [{qi}/{len(questions)}] Searching: {question[:70]}")

        results = _searxng_search(query, category, SEARXNG_URL, max_results=max_urls_per_q)
        stats["searched"] += len(results)

        fetched = 0
        for res in results:
            url = res.get("url", "")
            if not url or url in seen_urls:
                continue
            if _is_blocked(url):
                continue
            skip, _ = _should_skip_url(url)
            if skip:
                stats["skipped"] += 1
                continue

            text = _fetch_page(url, timeout=12)
            if not text or len(text) < 200:
                continue

            # Relevance filter: page must contain at least 2 topic keywords
            # Same logic as research_run.py -- prevents word lists, CRPG books,
            # shop pages and other off-topic content from entering Qdrant.
            _topic_words = set(
                w for w in re.sub(r"[^\w]", " ", topic.lower()).split()
                if len(w) > 3
            )
            _text_lower = text.lower()
            _matches = sum(1 for w in _topic_words if w in _text_lower)
            if len(_topic_words) >= 3 and _matches < 2:
                stats["skipped"] += 1
                continue

            seen_urls.add(url)
            trust_meta = get_domain_trust(url, category)

            # Chunk + embed
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

            _tier   = trust_meta.get("domain_trust", "unknown")
            _now    = _dt.datetime.utcnow().isoformat()

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
                    },
                ))

            fetched += 1
            print(f"     + {url[:60]} ({len(page_chunks)} chunks)")

        if fetched == 0:
            print(f"     (no new content found)")

    # 4. Index new points
    if new_points:
        BATCH = 64
        for i in range(0, len(new_points), BATCH):
            client.upsert(collection_name=col_name, points=new_points[i:i+BATCH])
        stats["new_chunks"] = len(new_points)
        print(f"\n   [uber] Indexed {len(new_points)} new chunks to Qdrant")
    else:
        print("\n   [uber] No new chunks found.")

    # Count total chunks after
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
