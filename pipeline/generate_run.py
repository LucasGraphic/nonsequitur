# pipeline/generate_run.py -- article generation phase
# For each "researched" item in queue:
#   1. Load persona file (data/personas/{persona}.md)
#   2. Retrieve top-K chunks from Qdrant (RAG)
#   3. Build prompt with persona context
#   4. Call LLM -- local Ollama or API provider
#   5. Save article to directory (article.md + metadata.json + sources.json)
#   6. Update status -> "done"

import os
import re
import time
import json
import requests
from datetime import datetime

import core.queue as queue
from pipeline.content_filter import is_garbage as _rag_is_garbage
from pipeline.suitability_gate import check_research_quality, print_gate_result, gate_prompt_interactive
from config import (
    OLLAMA_URL, OLLAMA_EMBED_URL, OUTPUT_DIR, QDRANT_URL,
    EMBED_MODEL, PERSONAS_DIR, PERSONAS, DEFAULT_PERSONA,
    API_PROVIDERS,
)

try:
    from qdrant_client import QdrantClient
except ImportError:
    QdrantClient = None


# -- Persona loader ---------------------------------------------------------

# -- RAG retrieval ----------------------------------------------------------

def _embed(text: str):
    try:
        r = requests.post(
            f"{OLLAMA_EMBED_URL}/api/embed",
            json={"model": EMBED_MODEL, "input": text},
            timeout=60,
        )
        data = r.json()
        embeddings = data.get("embeddings")
        if embeddings and len(embeddings) > 0:
            return embeddings[0]
        return data.get("embedding")
    except Exception:
        return None


# _rag_is_garbage moved to core/content_filter.py (S25)
from core.content_filter import _rag_is_garbage

def _retrieve_context(topic: str, item_id: str, category: str,
                      top_k: int = 35, seed_urls: list = None,
                      article_focus: str = "", persona: str = "lukasz",
                      topic_slug: str = "") -> tuple:
    """
    Multi-query hybrid RAG retrieval.
    Returns (context_str, sources_list, chunk_meta).
    """
    if QdrantClient is None:
        return "", [], [], []
    try:
        from qdrant_client.models import (
            Filter, FieldCondition, MatchValue,
            SparseVector, Prefetch, FusionQuery, Fusion,
        )
        from config import research_collection, knowledge_collection, KNOWLEDGE_EXPIRY_DAYS
        import re as _re
        import datetime as _dt

        client      = QdrantClient(url=QDRANT_URL)
        col_name    = research_collection(category)
        k_col       = knowledge_collection(category)

        existing = [c.name for c in client.get_collections().collections]
        if col_name not in existing:
            print(f"   [rag] Collection '{col_name}' not found")
            return "", [], [], []

        qfilter = Filter(
            must=[FieldCondition(key="item_id", match=MatchValue(value=item_id))]
        )

        _focus        = article_focus.strip() if article_focus else ""
        primary_query = _focus if _focus else topic

        sub_queries = [primary_query]
        if _focus:
            sub_queries.append(topic)
        words = _re.sub(r"[^\w\s]", "", primary_query).split()
        if len(words) >= 3:
            # Semantically diverse angles -- avoids returning the same Qdrant chunks
            # for near-identical suffix queries ("features and details" etc.)
            sub_queries += [
                f"{primary_query} technical architecture implementation",
                f"{primary_query} performance benchmarks results comparison",
                f"{primary_query} limitations problems criticism concerns",
                f"{primary_query} industry impact developers use cases",
            ]
        if _focus and topic.lower() not in _focus.lower():
            sub_queries.append(f"{topic} {_focus}")

        all_scored = []
        seen_texts = set()

        # -- Research retrieval: scroll + local BM25 ranking ------------------
        # HNSW with item_id post-filter returns only ~18% of chunks (72/408).
        # Scroll guarantees 100% coverage. BM25 pre-ranks for reranker.
        _scroll_chunks = []
        _scroll_offset = None
        try:
            while True:
                _batch, _next = client.scroll(
                    collection_name = col_name,
                    scroll_filter   = qfilter,
                    limit           = 500,
                    offset          = _scroll_offset,
                    with_payload    = True,
                    with_vectors    = False,
                )
                _scroll_chunks.extend(_batch)
                if _next is None or len(_batch) < 500:
                    break
                _scroll_offset = _next
            print(f"   [rag] Scroll: {len(_scroll_chunks)} research chunks for item_id={item_id[:8]}")
        except Exception as _sc_err:
            print(f"   [rag] Scroll failed: {_sc_err}")

        # Tokenize sub_queries for BM25
        _query_token_sets = []
        for _sq in sub_queries:
            _sq_toks = _re.findall(r"[a-z0-9]+", _sq.lower())
            _sq_tf: dict = {}
            for _t in _sq_toks:
                if len(_t) > 2:
                    _sq_tf[_t] = _sq_tf.get(_t, 0) + 1
            _sq_tot = max(len(_sq_toks), 1)
            _query_token_sets.append({_t: _c / _sq_tot for _t, _c in _sq_tf.items()})

        def _bm25(text: str) -> float:
            toks = _re.findall(r"[a-z0-9]+", text.lower())
            tf_map: dict = {}
            for tok in toks:
                if len(tok) > 2:
                    tf_map[tok] = tf_map.get(tok, 0) + 1
            dlen = max(len(toks), 1)
            sc = 0.0
            k1, b, avdl = 1.5, 0.75, 300
            for q_toks in _query_token_sets:
                for tok, qw in q_toks.items():
                    if tok in tf_map:
                        tf = tf_map[tok]
                        sc += qw * (tf * (k1 + 1)) / (tf + k1 * (1 - b + b * dlen / avdl))
            return sc

        # Sparse vector from primary query for knowledge/evergreen/persona
        _pq_toks = _re.findall(r"[a-z0-9]+", primary_query.lower())
        _pq_tf: dict = {}
        for _t in _pq_toks:
            if len(_t) > 2:
                _pq_tf[_t] = _pq_tf.get(_t, 0) + 1
        _pq_tot = max(len(_pq_toks), 1)
        _idx_map: dict = {}
        for _t, _c in _pq_tf.items():
            _idx = abs(hash(_t)) % (2**20)
            _idx_map[_idx] = _idx_map.get(_idx, 0.0) + round(_c / _pq_tot, 6)
        sp_indices = list(_idx_map.keys())
        sp_values  = [round(v, 6) for v in _idx_map.values()]

        # Dense embed for knowledge/evergreen/persona
        vec = _embed(primary_query)

        # Score + filter + dedup research chunks
        from config import RERANKER_FETCH_N as _FETCH_N
        _scored_r = []
        for _pt in _scroll_chunks:
            _txt = _pt.payload.get("text", "")
            if not _txt or _txt in seen_texts:
                continue
            if _rag_is_garbage(_txt):
                continue
            seen_texts.add(_txt)
            _boost = _pt.payload.get("retrieval_boost", 0.4)
            _trust = _pt.payload.get("domain_trust", "unknown")
            _url   = _pt.payload.get("url", "")
            _scored_r.append((_bm25(_txt) * _boost, _trust, _txt, _url, _pt.id, "research"))

        _scored_r.sort(key=lambda x: x[0], reverse=True)
        _r_pool = _scored_r[:_FETCH_N]
        all_scored.extend(_r_pool)
        print(f"   [rag] BM25 pre-rank: {len(_scored_r)} -> {len(_r_pool)} research chunks (top-{_FETCH_N})")
        # -- end scroll retrieval ----------------------------------------------

        # Knowledge base retrieval -- curated, permanent
        if k_col in existing:
            try:
                from qdrant_client.models import Filter, FieldCondition, MatchValue, IsNullCondition, IsEmptyCondition
                if topic_slug:
                    k_filter = Filter(must=[
                        FieldCondition(key="topic_slug", match=MatchValue(value=topic_slug))
                    ])
                else:
                    k_filter = None

                k_results = client.query_points(
                    collection_name = k_col,
                    prefetch = [
                        Prefetch(query=vec, using="dense", limit=top_k, filter=k_filter),
                        Prefetch(
                            query=SparseVector(indices=sp_indices, values=sp_values),
                            using="sparse", limit=top_k, filter=k_filter,
                        ),
                    ],
                    query        = FusionQuery(fusion=Fusion.RRF),
                    limit        = top_k // 3,
                    with_payload = True,
                ).points

                k_added    = 0
                k_neighbor = 0
                _k_hits    = []  # (final_score, payload, id)

                for r in k_results:
                    text = r.payload.get("text", "")
                    if not text or text in seen_texts:
                        continue
                    seen_texts.add(text)
                    boost       = r.payload.get("retrieval_boost", 0.6)
                    final_score = r.score * boost
                    trust       = r.payload.get("domain_trust", "trusted")
                    url         = r.payload.get("source", r.payload.get("url", ""))
                    all_scored.append((final_score, trust, text, url, r.id, "knowledge"))
                    k_added += 1
                    _k_hits.append((final_score, r.payload, r.id))

                # Parent-child: fetch chunk_idx ± 1 neighbors
                # Filter by (topic_slug, url) -- prevents mixing neighbors across sources
                _K_NEIGHBOR_PENALTY = 0.85
                from qdrant_client.models import Filter as _KF, FieldCondition as _KFC, MatchValue as _KMV
                for _k_score, _k_pay, _k_id in _k_hits:
                    _k_cidx = _k_pay.get("chunk_idx")
                    _k_slug = _k_pay.get("topic_slug", "")
                    _k_url  = _k_pay.get("url", _k_pay.get("source", ""))
                    if _k_cidx is None or _k_cidx < 0:
                        continue
                    for _k_nb_idx in [_k_cidx - 1, _k_cidx + 1]:
                        if _k_nb_idx < 0:
                            continue
                        try:
                            _k_nb_filter_must = [
                                _KFC(key="chunk_idx", match=_KMV(value=_k_nb_idx)),
                            ]
                            if _k_url:
                                _k_nb_filter_must.append(_KFC(key="url", match=_KMV(value=_k_url)))
                            elif _k_slug:
                                _k_nb_filter_must.append(_KFC(key="topic_slug", match=_KMV(value=_k_slug)))
                            _k_nb_res, _ = client.scroll(
                                collection_name = k_col,
                                scroll_filter   = _KF(must=_k_nb_filter_must),
                                limit           = 1,
                                with_payload    = True,
                                with_vectors    = False,
                            )
                            for _k_nb in _k_nb_res:
                                _k_nb_text  = _k_nb.payload.get("text", "")
                                _k_nb_url   = _k_nb.payload.get("url", _k_nb.payload.get("source", ""))
                                if not _k_nb_text or _k_nb_text in seen_texts:
                                    continue
                                seen_texts.add(_k_nb_text)
                                _k_nb_score = _k_score * _K_NEIGHBOR_PENALTY
                                _k_nb_trust = _k_nb.payload.get("domain_trust", "trusted")
                                all_scored.append((_k_nb_score, _k_nb_trust, _k_nb_text, _k_nb_url, _k_nb.id, "knowledge"))
                                k_neighbor += 1
                        except Exception:
                            pass  # neighbor fetch non-critical

                if k_added or k_neighbor:
                    slug_label = f" [{topic_slug}]" if topic_slug else ""
                    print(f"   [rag] {k_col}{slug_label}: +{k_added} chunks, +{k_neighbor} neighbors")

            except Exception:
                pass

        # knowledge_evergreen retrieval -- category filter, no slug filter
        # Parent-child: after HNSW retrieval, fetch sequential neighbors
        # (chunk_idx ± 1, same topic_slug) to preserve context continuity.
        ev_col = "knowledge_evergreen"
        if ev_col in existing:
            try:
                from qdrant_client.models import (
                    Filter as _EvF, FieldCondition as _EvFC, MatchValue as _EvMV,
                    Range as _EvRange,
                )
                ev_filter = _EvF(should=[
                    _EvFC(key="category", match=_EvMV(value=category)),
                    _EvFC(key="category", match=_EvMV(value="global")),
                ])
                ev_top_k  = max(5, top_k // 4)
                ev_results = client.query_points(
                    collection_name = ev_col,
                    prefetch = [
                        Prefetch(query=vec, using="dense", limit=ev_top_k, filter=ev_filter),
                        Prefetch(query=SparseVector(indices=sp_indices, values=sp_values),
                                 using="sparse", limit=ev_top_k, filter=ev_filter),
                    ],
                    query        = FusionQuery(fusion=Fusion.RRF),
                    limit        = ev_top_k,
                    with_payload = True,
                ).points

                ev_added    = 0
                ev_neighbor = 0

                # Collect retrieved chunks first
                _ev_hits = []  # (score, payload, id)
                for r in ev_results:
                    text = r.payload.get("text", "")
                    url  = r.payload.get("url", "")
                    if not text or _rag_is_garbage(text):
                        continue
                    seen_key = text[:120]
                    if seen_key in seen_texts:
                        continue
                    seen_texts.add(seen_key)
                    boost       = r.payload.get("retrieval_boost", 0.5)
                    final_score = getattr(r, "score", 0.5) * boost
                    all_scored.append((final_score, r.payload.get("domain_trust", "unknown"), text, f"evergreen://{url}", r.id, "evergreen"))
                    ev_added += 1
                    _ev_hits.append((final_score, r.payload, r.id))

                # Parent-child: fetch chunk_idx ± 1 neighbors for each hit
                # Only if chunk_idx is present (post-patch data)
                _NEIGHBOR_SCORE_PENALTY = 0.85  # neighbors score = hit_score * penalty
                for _hit_score, _hit_pay, _hit_id in _ev_hits:
                    _cidx = _hit_pay.get("chunk_idx")
                    _slug = _hit_pay.get("topic_slug", "")
                    if _cidx is None or _cidx < 0:
                        continue  # chunk_idx not set -- skip (pre-patch data)

                    # Fetch prev + next in one scroll per hit
                    _neighbor_range = [_cidx - 1, _cidx + 1]
                    for _nb_idx in _neighbor_range:
                        if _nb_idx < 0:
                            continue
                        try:
                            _nb_filter = _EvF(must=[
                                _EvFC(key="topic_slug", match=_EvMV(value=_slug)),
                                _EvFC(key="chunk_idx",  match=_EvMV(value=_nb_idx)),
                            ])
                            _nb_res, _ = client.scroll(
                                collection_name = ev_col,
                                scroll_filter   = _nb_filter,
                                limit           = 1,
                                with_payload    = True,
                                with_vectors    = False,
                            )
                            for _nb in _nb_res:
                                _nb_text = _nb.payload.get("text", "")
                                _nb_url  = _nb.payload.get("url", "")
                                if not _nb_text or _rag_is_garbage(_nb_text):
                                    continue
                                _nb_key = _nb_text[:120]
                                if _nb_key in seen_texts:
                                    continue
                                seen_texts.add(_nb_key)
                                _nb_score = _hit_score * _NEIGHBOR_SCORE_PENALTY
                                _nb_trust = _nb.payload.get("domain_trust", "unknown")
                                all_scored.append((_nb_score, _nb_trust, _nb_text, f"evergreen://{_nb_url}", _nb.id, "evergreen"))
                                ev_neighbor += 1
                        except Exception:
                            pass  # neighbor fetch failure is non-critical

                if ev_added or ev_neighbor:
                    print(f"   [rag] {ev_col} [{category}]: +{ev_added} chunks, +{ev_neighbor} neighbors")
            except Exception as _ev_err:
                print(f"   [rag] evergreen retrieval error: {_ev_err}")


        # Persona retrieval -- single unified collection (session 17+)
        # Called once after sub_queries loop -- not per query.
        # trigger_dense vector embedded at insert time -- no extra embed call here.
        from config import PERSONA_COLLECTION
        _item_persona = (persona or '').strip()
        if _item_persona:
            _persona_col = 'persona_' + _item_persona
        else:
            _persona_col = PERSONA_COLLECTION
        if _persona_col in existing:
            try:
                # Use last computed vec + sp_indices/sp_values from final sub_query
                p_results = client.query_points(
                    collection_name = _persona_col,
                    prefetch = [
                        Prefetch(query=vec, using="dense", limit=40),
                        Prefetch(
                            query  = SparseVector(indices=sp_indices, values=sp_values),
                            using  = "sparse",
                            limit  = 40,
                        ),
                    ],
                    query        = FusionQuery(fusion=Fusion.RRF),
                    limit        = 40,
                    with_payload = True,
                ).points
                _p_added = 0
                for r in p_results:
                    _ptext = r.payload.get("text", "")
                    if not _ptext or _ptext in seen_texts:
                        continue
                    seen_texts.add(_ptext)
                    _pfinal_score = r.score * 0.6
                    all_scored.append((_pfinal_score, "trusted", _ptext, _persona_col, r.id, "persona", r.payload))
                    _p_added += 1
                if _p_added:
                    print(f"   [rag] {_persona_col}: +{_p_added} chunks")
            except Exception as _pe:
                print(f"   [rag] persona retrieval error: {_pe}")

        if not all_scored:
            return "", [], [], []

        # --- Per-source score normalization ---
        # Qdrant RRF scores depend on collection size:
        # small collections (evergreen, knowledge) -> 0.93-0.99
        # large collections (research) -> 0.01-0.08
        # Normalize each source group independently, then apply priority weights.
        def _norm_group(chunks, weight):
            if not chunks:
                return []
            scores = [s for s, *_ in chunks]
            lo, hi = min(scores), max(scores)
            span = hi - lo if hi != lo else 1.0
            return [(weight * (s - lo) / span, *rest) for s, *rest in chunks]

        _g_research  = [c for c in all_scored if c[5] == "research"]
        _g_knowledge = [c for c in all_scored if c[5] == "knowledge"]
        _g_evergreen = [c for c in all_scored if c[5] == "evergreen"]
        _g_persona   = [c for c in all_scored if len(c) > 5 and c[5] == "persona"]
        print(f"   [rag] Score groups before norm: "
              f"research={len(_g_research)} knowledge={len(_g_knowledge)} "
              f"evergreen={len(_g_evergreen)} persona={len(_g_persona)}")
        all_scored = (
            _norm_group(_g_research,  1.00) +
            _norm_group(_g_knowledge, 0.85) +
            _norm_group(_g_evergreen, 0.70) +
            _norm_group(_g_persona,   0.60)
        )
        # --- end normalization ---

        all_scored.sort(key=lambda x: x[0], reverse=True)

        # _min_score filter removed -- RRF*boost scores are too low (~0.01-0.06)
        # and were cutting ~95% of valid chunks before the reranker.
        # Reranker (FETCH_N=100) is the correct place for quality selection.

        # -- Semantic dedup po normalizacji, przed rerankerem ----------------
        # Problem: 30 URLi na jeden temat -> 60-70% chunków to parafrazy
        # tego samego faktu z różnych źródeł (cross-URL duplikaty).
        # Reranker robi ranking ale nie dedup -- model dostaje 8 wersji tego
        # samego zdania. Fix: cosine similarity na wektorach Qdrant.
        # Dedup tylko research chunks -- knowledge/evergreen/persona nieruszone.
        _research_to_dedup = [(i, c) for i, c in enumerate(all_scored) if c[5] == "research"]
        _non_research      = [c for c in all_scored if c[5] != "research"]

        if len(_research_to_dedup) > 1:
            try:
                _dedup_ids = [c[4] for _, c in _research_to_dedup]  # qdrant IDs

                # Jeden call -- pobierz wektory dense dla całego poola research
                _vec_records = client.retrieve(
                    collection_name = col_name,
                    ids             = _dedup_ids,
                    with_vectors    = True,
                    with_payload    = False,
                )
                # id -> dense vector
                _id_to_vec = {}
                for rec in _vec_records:
                    v = rec.vector
                    if isinstance(v, dict):
                        v = v.get("dense") or v.get(next(iter(v), None))
                    if v:
                        _id_to_vec[rec.id] = v

                def _cosine_sim(a, b):
                    import math
                    if not a or not b or len(a) != len(b):
                        return 0.0
                    dot = sum(x * y for x, y in zip(a, b))
                    na  = math.sqrt(sum(x * x for x in a))
                    nb  = math.sqrt(sum(x * x for x in b))
                    return dot / (na * nb) if na and nb else 0.0

                DEDUP_THRESHOLD = 0.92

                # Greedy dedup: iteruj po score-desc, odrzuć chunki zbyt podobne
                # do już zaakceptowanych. Zachowujemy wyżej oceniony z grupy
                # (all_scored już posortowane desc po normalizacji).
                _accepted_research = []
                _accepted_vecs     = []
                _dedup_removed     = 0

                for _, chunk in _research_to_dedup:
                    qid = chunk[4]
                    vec = _id_to_vec.get(qid)
                    if vec is None:
                        # Brak wektora (nie znaleziono w retrieve) -- zachowaj
                        _accepted_research.append(chunk)
                        _accepted_vecs.append(None)
                        continue
                    is_dup = False
                    for av in _accepted_vecs:
                        if av is not None and _cosine_sim(vec, av) >= DEDUP_THRESHOLD:
                            is_dup = True
                            break
                    if is_dup:
                        _dedup_removed += 1
                    else:
                        _accepted_research.append(chunk)
                        _accepted_vecs.append(vec)

                all_scored = _accepted_research + _non_research
                all_scored.sort(key=lambda x: x[0], reverse=True)
                print(f"   [rag] Semantic dedup: {len(_research_to_dedup)} -> "
                      f"{len(_accepted_research)} research chunks "
                      f"(-{_dedup_removed} duplikatow, threshold={DEDUP_THRESHOLD})")

            except Exception as _dedup_err:
                print(f"   [rag] Semantic dedup skipped: {_dedup_err}")
        # -- end semantic dedup ------------------------------------------------

        available = len(all_scored)
        if available == 0:
            return "", [], [], []
        print(f"   [rag] Pool: {available} chunks -> reranker top-{min(available, 100)}")

        # Fallback: retry without item_id filter
        if len(all_scored) < 8 and col_name in existing:
            try:
                fallback_vec = _embed(topic)
                if fallback_vec:
                    fallback_results = client.query_points(
                        collection_name = col_name,
                        query           = fallback_vec,
                        using           = "dense",
                        limit           = top_k,
                        with_payload    = True,
                    ).points
                    added = 0
                    for r in fallback_results:
                        text = r.payload.get("text", "")
                        if not text or text in seen_texts:
                            continue
                        seen_texts.add(text)
                        boost       = r.payload.get("retrieval_boost", 0.4)
                        final_score = r.score * boost * 0.7
                        trust       = r.payload.get("domain_trust", "unknown")
                        url         = r.payload.get("url", "")
                        all_scored.append((final_score, trust, text, url, r.id, "research"))
                        added += 1
                    if added:
                        all_scored.sort(key=lambda x: x[0], reverse=True)
                        print(f"   [rag] Fallback: +{added} chunks (no item_id filter)")
            except Exception:
                pass

        top = all_scored[:top_k]

        # Reranker
        try:
            from config import RERANKER_URL, RERANKER_TOP_N, RERANKER_FETCH_N
            import requests as _rreq

            _rh = _rreq.get(f"{RERANKER_URL}/health", timeout=2)
            if _rh.status_code == 200:
                rerank_pool = all_scored[:RERANKER_FETCH_N]
                rerank_query = f"{topic}"
                if article_focus:
                    rerank_query += f" -- {article_focus}"

                # persona chunks are 7-tuples (with payload), research are 6-tuples
                persona_chunks  = [c for c in rerank_pool if c[5] == "persona"]
                research_chunks = [(c[0], c[1], c[2], c[3], c[4], c[5]) for c in rerank_pool if c[5] != "persona"]

                if research_chunks:
                    docs = [tx for _, _, tx, _, _, _ in research_chunks]
                    from config import PERSONA_MAX
                    payload = {
                        "query":     rerank_query,
                        "documents": docs,
                        "top_n":     max(RERANKER_TOP_N - PERSONA_MAX, 5),
                    }
                    import threading as _rt, itertools as _it
                    _rk_done = [False]
                    def _rk_spinner():
                        frames = ['|', '/', '-', '\\']
                        for f in _it.cycle(frames):
                            if _rk_done[0]: break
                            print(f"   [rag] Reranking {len(docs)} chunks... {f}", end='\r', flush=True)
                            import time as _rt2; _rt2.sleep(0.15)
                        print(' ' * 55, end='\r')  # clear spinner line
                    _spin = _rt.Thread(target=_rk_spinner, daemon=True)
                    _spin.start()
                    try:
                        resp = _rreq.post(f"{RERANKER_URL}/rerank", json=payload, timeout=600)
                    finally:
                        _rk_done[0] = True
                        _spin.join(timeout=1)
                    if resp.status_code == 200:
                        reranked   = resp.json()["results"]
                        elapsed_r  = resp.json()["time_ms"]
                        reranked_chunks = []
                        for r in reranked:
                            orig = research_chunks[r["index"]]
                            reranked_chunks.append((r["score"], orig[1], orig[2], orig[3], orig[4], orig[5]))

                        # -- Persona slot: coverage-based sampling (session 17+) --
                        # One chunk per dimension, prefer trigger match to article focus.
                        from config import PERSONA_MAX, PERSONA_MIN, PERSONA_THRESHOLD, PERSONA_TRIGGER_W

                        def _coverage_sample(chunks, focus_vec, max_n, min_n, threshold, trigger_w):
                            """
                            Select up to max_n persona chunks, one per dimension.
                            Scoring: combined = (1 - trigger_w) * chunk_score + trigger_w * trigger_sim
                            trigger_sim = cosine similarity between stored trigger_dense and focus_vec.
                            Falls back to chunk score if trigger_dense not available.
                            """
                            import math

                            def _cosine(a, b):
                                if not a or not b or len(a) != len(b):
                                    return 0.0
                                dot  = sum(x * y for x, y in zip(a, b))
                                na   = math.sqrt(sum(x * x for x in a))
                                nb   = math.sqrt(sum(x * x for x in b))
                                if na == 0 or nb == 0:
                                    return 0.0
                                return dot / (na * nb)

                            scored = []
                            for chunk in chunks:
                                payload      = chunk[6] if len(chunk) > 6 else {}
                                chunk_score  = chunk[0]
                                trigger_vec  = payload.get("trigger_dense_vector")
                                trigger_sim  = _cosine(trigger_vec, focus_vec) if trigger_vec else 0.0
                                combined     = (1 - trigger_w) * chunk_score + trigger_w * trigger_sim
                                scored.append((combined, chunk))

                            scored.sort(key=lambda x: x[0], reverse=True)

                            # Apply minimum trigger_sim threshold.
                            # Chunks below threshold are voice-irrelevant for this article.
                            above = [(s, c) for s, c in scored if s >= threshold]
                            below = [(s, c) for s, c in scored if s < threshold]

                            result = [chunk for _, chunk in above[:max_n]]

                            # Guarantee min_n: relax threshold if too few passed
                            if len(result) < min_n:
                                for _, chunk in below:
                                    result.append(chunk)
                                    if len(result) >= min_n:
                                        break

                            return result

                        # Build focus vector for trigger matching
                        _focus_vec = _embed(article_focus) if article_focus else _embed(topic)

                        persona_final  = _coverage_sample(
                            persona_chunks,
                            focus_vec = _focus_vec,
                            max_n     = PERSONA_MAX,
                            min_n     = PERSONA_MIN,
                            threshold = PERSONA_THRESHOLD,
                            trigger_w = PERSONA_TRIGGER_W,
                        )
                        research_slots = top_k - len(persona_final)
                        research_final = reranked_chunks[:max(research_slots, 5)]

                        top = research_final + persona_final

                        _dims_used = [c[6].get("dimension", "?") if len(c) > 6 else "?" for c in persona_final]
                        print(f"   [rag] Persona coverage: {len(persona_final)} chunks | dims: {_dims_used}")
                        print(f"   [rag] Reranker: {len(research_chunks)} -> {len(reranked_chunks)} chunks ({elapsed_r}ms)")
        except Exception as _re_err:
            print(f"   [rag] Reranker error: {_re_err}")
            from config import PERSONA_MAX, PERSONA_MIN, PERSONA_THRESHOLD, PERSONA_TRIGGER_W
            # Reranker failed -- apply persona/research split with coverage sampling
            _fb_persona   = [c for c in all_scored if c[5] == "persona"]
            _fb_research  = [c for c in all_scored if c[5] != "persona"]
            _fb_r_sorted  = sorted(_fb_research, key=lambda x: x[0], reverse=True)

            # Pure relevance fallback -- top-N by score, no per-dimension cap
            _fb_sorted  = sorted(_fb_persona, key=lambda x: x[0], reverse=True)
            _fb_p_final = _fb_sorted[:PERSONA_MAX]
            if len(_fb_p_final) < PERSONA_MIN:
                _fb_p_final = _fb_sorted[:PERSONA_MIN]

            _fb_r_slots = top_k - len(_fb_p_final)
            _fb_r_final = _fb_r_sorted[:max(_fb_r_slots, 5)]
            top = _fb_r_final + _fb_p_final
            _fb_dims = [c[6].get("dimension", "?") if len(c) > 6 else "?" for c in _fb_p_final]
            print(f"   [rag] Reranker fallback: {len(_fb_r_final)} research + {len(_fb_p_final)} persona | dims: {_fb_dims}")

        # Pin seed URL chunks -- only if chunk has meaningful content
        # Cloudflare-blocked pages return 200-400 chars of nav/cookie text
        # Pinning these displaces real content chunks from context
        PIN_MIN_CHARS = 300  # below this = nav/header/blocked page, skip
        if seed_urls:
            seed_domains = set()
            for su in seed_urls:
                try:
                    from urllib.parse import urlparse as _urlparse
                    seed_domains.add(_urlparse(su).netloc.lower().removeprefix("www."))
                except Exception:
                    pass

            pinned       = []
            pinned_texts = set()
            skipped_short = 0
            for chunk in top:
                score, trust, text, url, qid, src = chunk[0], chunk[1], chunk[2], chunk[3], chunk[4], chunk[5]
                try:
                    from urllib.parse import urlparse as _urlparse
                    dom = _urlparse(url).netloc.lower().removeprefix("www.")
                except Exception:
                    dom = ""
                if dom in seed_domains and text not in pinned_texts:
                    if len(text.strip()) < PIN_MIN_CHARS:
                        skipped_short += 1
                        continue  # skip empty/blocked chunks
                    pinned.append((score, trust, text, url, qid, src))
                    pinned_texts.add(text)
                    if len(pinned) >= min(len(seed_urls) * 2, top_k // 3):
                        break

            if pinned:
                non_pinned = [(c[0], c[1], c[2], c[3], c[4], c[5]) for c in top if c[2] not in pinned_texts]
                top = pinned + non_pinned[:top_k - len(pinned)]
                skip_note = f" ({skipped_short} short/blocked skipped)" if skipped_short else ""
                print(f"   [rag] Pinned {len(pinned)} seed chunks (guaranteed in context){skip_note}")
            elif skipped_short:
                print(f"   [rag] No seed chunks pinned -- {skipped_short} were too short (Cloudflare-blocked?)")

        # Normalize tuples -- persona chunks are 7-tuples (payload appended for
        # coverage sampling), all downstream code expects 6-tuples.
        top = [c[:6] for c in top]

        trust_dist  = {}
        source_dist = {}
        for _, trust, text, url, qid, src in top:
            trust_dist[trust] = trust_dist.get(trust, 0) + 1
            from urllib.parse import urlparse as _up
            domain = _up(url).netloc.removeprefix("www.") if url and url != "persona_lukasz" else (url or "unknown")
            source_dist[domain] = source_dist.get(domain, 0) + 1

        dist_str    = "  ".join(f"{k}:{v}" for k, v in sorted(trust_dist.items()))
        top_domains = sorted(source_dist.items(), key=lambda x: -x[1])[:5]
        dom_str     = "  ".join(f"{d}({c})" for d, c in top_domains)
        total_chars = sum(len(text) for _, _, text, _, _, _ in top if text)
        print(f"   [rag] Hybrid top-{len(top)}/{top_k} ({len(sub_queries)} queries): {dist_str}")
        print(f"   [rag] Sources: {dom_str}")
        print(f"   [rag] Total context: {total_chars} chars")
        print(f"   [rag] Chunks used:")
        for i, (score, trust, text, url, qid, src) in enumerate(top):
            from urllib.parse import urlparse as _up3
            if src == "persona":
                domain = url
                col_d  = url
            else:
                domain = _up3(url).netloc.removeprefix("www.")[:28] if url else "unknown"
                col_d  = col_name
            preview = text[:60].replace("\n", " ") if text else ""
            cid     = f"C{i+1:02d}"
            print(f"   {cid}  {score:>7.4f}  {trust:<8} {src:<10} {domain:<28} {preview}")

        # Split persona chunks from research chunks before building context strings
        persona_parts   = []
        evergreen_parts = []
        research_parts  = []
        for score, trust, text, url, qid, src in top:
            if src == "persona":
                persona_parts.append(text)
            elif src == "evergreen":
                evergreen_parts.append(text)
            else:
                research_parts.append(text)

        sources = list(dict.fromkeys(url for _, _, _, url, _, src in top
                       if src != "persona"))

        chunk_meta = []
        for i, (score, trust, text, url, qid, src) in enumerate(top):
            from urllib.parse import urlparse as _up2
            if src == "persona":
                domain = url
                col_d  = url
            else:
                domain = _up2(url).netloc.removeprefix("www.") if url else "unknown"
                col_d  = col_name
            chunk_meta.append({
                "id":         f"C{i+1:02d}",
                "qdrant_id":  str(qid),
                "score":      round(score, 6),
                "trust":      trust,
                "src":        src,
                "domain":     domain,
                "url":        url,
                "collection": col_d,
                "text":       text[:200],
            })

        context_persona   = "\n---\n".join(persona_parts)
        context_evergreen = "\n---\n".join(evergreen_parts)
        context_research  = "\n\n---\n\n".join(research_parts)
        return context_persona, context_research, context_evergreen, sources, chunk_meta

    except Exception as e:
        print(f"   [rag] retrieval error: {e}")
        return "", "", "", [], []


# -- Prompt builder ---------------------------------------------------------

def _build_prompt(topic: str, section: str,
                  context_persona: str = "", context_research: str = "",
                  sources: list = None, article_focus: str = "",
                  category: str = "other", model: str = "",
                  article_length: str = "medium", context_evergreen: str = "",
                  article_schema: dict = None,
                  chunk_meta: list = None) -> str:

    SECTION_INSTRUCTIONS = {
        "DATA":      "Section DATA -- technical articles, analyses, guides.",
        "LAB":       "Section LAB -- AI experiments, generative art, new technologies.",
        "PORTFOLIO": "Section PORTFOLIO -- photography, creative projects.",
    }
    section_instr = SECTION_INSTRUCTIONS.get(section, "")

    _model_lower = model.lower() if model else ""
    if any(x in _model_lower for x in ["122b", "70b", "super", "max", "27b", "35b"]):
        _ctx_limit = 20000
    elif any(x in _model_lower for x in ["32b", "normal", "gemma"]):
        _ctx_limit = 18000
    else:
        _ctx_limit = 10000

    context_block = ""
    if context_research or context_persona or context_evergreen:
        author_voice_block = ""
        if context_persona:
            author_voice_block = (
                "=== AUTHOR VOICE ===\n"
                "These are verbatim writing samples from this author.\n"
                "Study the register: sentence length variation, where irony lands,\n"
                "how opinions are stated (direct, not hedged), rhythm of short vs long sentences.\n"
                "When you would write a smooth transition sentence -- ask if this author\n"
                "would write a 5-word punch instead. When you would write 'it is worth noting'\n"
                "-- ask if this author would just state the thing, or skip it entirely.\n"
                "Do NOT copy phrases verbatim. Do NOT reproduce as standalone paragraphs.\n\n"
                + context_persona[:6000]
                + "\n=== END AUTHOR VOICE ===\n\n"
            )

        evergreen_block = ""
        if context_evergreen:
            evergreen_block = (
                "=== BACKGROUND KNOWLEDGE -- conceptual context only ===\n"
                "Use this to understand technical concepts referenced in the article.\n"
                "Do NOT write dedicated sections about these concepts.\n"
                "Do NOT reproduce definitions verbatim. Weave into analysis only where directly relevant.\n\n"
                + context_evergreen[:1500]
                + "\n=== END BACKGROUND KNOWLEDGE ===\n\n"
            )

        research_block = ""
        if context_research:
            _rc = [c for c in (chunk_meta or []) if c.get("src") != "persona"]
            _nt = len([c for c in _rc if c.get("trust") in ("trusted", "press")])
            _nu = len([c for c in _rc if c.get("trust") not in ("trusted", "press")])
            _trust_note = ""
            if _rc:
                _trust_note = f"[Source trust: {_nt} trusted/press, {_nu} unknown/aggregator]\n"
                if _nu > 0:
                    _trust_note += "Qualify claims from unknown sources only: 'reportedly', 'according to early reports'.\n"
            research_block = (
                "=== RESEARCH FACTS -- paraphrase, never copy verbatim ===\n"
                + _trust_note
                + "\n"
                + "Attribution rule: if a source does not name the reviewer or critic, do NOT write 'critics note', 'reviewers say', or 'one reviewer' -- state the observation directly as your own analysis or omit the attribution entirely.\n"
                + context_research
                + "\n\n=== END RESEARCH FACTS ==="
            )

        context_block = f"""
STRICT RULES for using the context below:
- AUTHOR VOICE = your tone signal. Write every sentence as this author would -- direct, opinionated, no hedging.
- RESEARCH FACTS = the substance of the article. Paraphrase everything in your own words.
- Copying 5+ consecutive words from any source = failed task.
- Synthesize across multiple sources -- never summarize one source at a time.
- Only state facts explicitly present in the context. Do NOT invent details.
- If a fact is uncertain -- omit it rather than guess.

{evergreen_block}{research_block}{author_voice_block}
"""
    else:
        context_block = """
WARNING: No research context available. Write only from general knowledge.
Be conservative -- avoid specific claims you cannot verify.
"""

    # Article schema block -- mandatory structure, placed before TASK in the prompt.
    # When schema is set: model gets explicit section names + purposes as a hard contract.
    # When no schema: fallback _length_instr used inside REQUIREMENTS as before.
    _schema_block = ""
    _length_instr = ""
    if article_schema and article_schema.get("sections"):
        _schema_note = article_schema.get("note", "")
        _sections_lines = "\n".join(
            f"  {i+1}. {s['name']}\n     Purpose: {s['purpose']}"
            for i, s in enumerate(article_schema["sections"])
        )
        _opening_rule = article_schema.get("opening_rule", "")
        _closing_rule = article_schema.get("closing_rule", "")
        _schema_block = (
            "=== ARTICLE STRUCTURE -- MANDATORY ===\n"
            "You MUST use EXACTLY these section names as H2 headers, in this order.\n"
            "Do NOT rename them. Do NOT reorder them. Do NOT add extra sections.\n"
            "Each section must cover only its stated purpose -- nothing else.\n"
            + (f"IMPORTANT: {_schema_note}\n" if _schema_note else "")
            + (f"\nOPENING RULE: {_opening_rule}\n" if _opening_rule else "")
            + (f"CLOSING RULE: {_closing_rule}\n" if _closing_rule else "")
            + "\nSections:\n"
            + _sections_lines
            + "\n\nLength rule: write as much as the available facts support -- no more.\n"
            "If facts are sparse, write a shorter article rather than padding with speculation.\n"
            "Voice rule: the author register defined in AUTHOR VOICE applies to every\n"
            "sentence of every section -- not just the opening and closing. Each section\n"
            "must sound like the same person who wrote the first paragraph.\n"
            "=== END ARTICLE STRUCTURE ===\n"
        )
        # Inject concrete word count from schema length fields
        _length_key = f"length_{article_length}"
        _word_range = article_schema.get(_length_key) or article_schema.get("length_medium", "1000-1400 words")
        _schema_block += f"\nTarget length: {_word_range}. Expand where facts allow, cut where they repeat.\n"
        _length_instr = "Follow the ARTICLE STRUCTURE block above -- section names and order are mandatory."
    else:
        _length_map = {
            "short":  "Write a focused piece of 600-900 words. Get to the point fast.",
            "medium": "Write a standard article of 1000-1400 words.",
            "long":   "Write a deep-dive analysis of 1800-2400 words. Thorough coverage.",
        }
        _length_instr = _length_map.get(article_length, _length_map["medium"])

    focus_block = ""
    if article_focus:
        focus_block = f"""
=== ARTICLE DIRECTION -- MANDATORY ===
The article MUST be built around this specific thesis. Do NOT change it. Do NOT ignore it.
Every paragraph must serve this argument:

{article_focus}

If the research context does not support this thesis directly, use the closest supporting facts.
Do NOT substitute your own angle. The thesis above is the assignment.
=== END ARTICLE DIRECTION ===
"""

    sources_note = ""
    if sources:
        unique = list(dict.fromkeys(sources))[:10]
        sources_note = "\n".join(f"{i+1}: {u}" for i, u in enumerate(unique))

    return f"""You are a professional copywriter and subject matter expert.
{section_instr}

{context_block}
{focus_block}
{_schema_block}
TASK: Write a complete, informative article about:
"{topic}"


REQUIREMENTS:
- {_length_instr}
- Language: English. Format: Markdown. H1 title, H2 section headers, prose paragraphs.

CONTENT RULES -- three rules, no exceptions:
1. DENSITY: Every paragraph must be grounded in at least one fact from research or a
   concrete evaluation. Within a paragraph, individual sentences may serve rhythm,
   emphasis, or voice -- a short punchy sentence, a dry observation, a sharp contrast.
   These are not filler -- they are craft. What IS filler: throat-clearing openers,
   transitions that restate the previous paragraph, sentences that say nothing happened.
   If you run out of facts -- stop writing. A shorter article built on facts beats a
   longer article padded with opinion fog.

2. STRUCTURE: Follow the ARTICLE STRUCTURE sections above exactly as named.
   First sentence of the article must be a direct argument or verdict -- not background,
   not context, not a description of what you are about to say.
   Each H2 section introduces a NEW argument not covered elsewhere in the article.

3. HONESTY: Never invent numbers, dates, names, or scores not present in the research.
   If the research is silent on something -- say so directly or omit it.
   Take a position and defend it. A "fair" article that hedges every claim is a failure.
   If your verdict is negative -- state it without softening.

HARD TECHNICAL RULES (non-negotiable):
- NEVER output HTML comments <!-- --> of any kind. Zero. Not one.
- The ARTICLE DIRECTION thesis above is mandatory -- do not write about a different angle.
- Do NOT output === TITLES ===, === EXCERPTS ===, or === END === -- generated separately.
- BANNED PHRASES -- never use these, not even paraphrased:
  "raises questions", "it remains to be seen", "only time will tell",
  "begs the question", "stands as a testament", "it is worth noting",
  "love it or hate it", "at the end of the day", "needless to say".
  If you catch yourself writing one -- delete the sentence and restate directly.
- End the article after the last paragraph. Output nothing after the final sentence.

Write now:"""


# -- LLM backends ----------------------------------------------------------

def _generate_ollama(prompt: str, model: str, num_predict_override: int = None) -> tuple:
    from config import LLM_THINKING, LLM_TEMPERATURE, LLM_TEMPERATURE_BY_MODEL

    temperature = LLM_TEMPERATURE_BY_MODEL.get(model, LLM_TEMPERATURE)
    options = {"temperature": temperature}
    options["num_predict"] = num_predict_override if num_predict_override is not None else 4500

    # think:false is a qwen3.x-specific parameter -- do not send to llama/mistral/gemma
    _THINK_MODELS = ("qwen3",)
    _model_supports_think = any(model.startswith(p) for p in _THINK_MODELS)
    think = False if (not LLM_THINKING and _model_supports_think) else None

    try:
        payload = {
            "model":   model,
            "messages": [{"role": "user", "content": prompt}],
            "stream":  True,
            "options": options,
        }
        if think is not None:
            payload["think"] = think

        r = requests.post(
            f"{OLLAMA_URL}/api/chat",
            json    = payload,
            stream  = True,
            timeout = 600,
        )
        r.raise_for_status()

        output     = []
        tokens_in  = 0
        tokens_out = 0

        for line in r.iter_lines():
            if not line:
                continue
            data = json.loads(line)
            msg   = data.get("message", {})
            token = msg.get("content", "")
            if msg.get("thinking"):
                continue
            output.append(token)
            print(token, end="", flush=True)
            if data.get("done"):
                tokens_in  = data.get("prompt_eval_count", len(prompt) // 4)
                tokens_out = data.get("eval_count", len("".join(output)) // 4)
                break

        print()
        return "".join(output), tokens_in, tokens_out

    except Exception as e:
        print(f"\n   [llm] Ollama error: {e}")
        return "", 0, 0

def _generate_api(prompt: str, provider_key: str) -> tuple:
    from config import API_PROVIDERS

    provider = API_PROVIDERS.get(provider_key)
    if not provider:
        print(f"   [api] Unknown provider: {provider_key}")
        return "", 0, 0

    api_key = os.environ.get(provider["env_key"], "")
    if not api_key:
        print(f"   [api] No API key -- set env var: {provider['env_key']}")
        print(f"   [api] Windows: setx {provider['env_key']} \"your-key\"")
        return "", 0, 0

    model = provider["model"]
    url   = provider["url"]
    headers = {"Content-Type": "application/json"}

    if provider_key == "claude":
        headers["x-api-key"]         = api_key
        headers["anthropic-version"] = "2023-06-01"
        payload = {
            "model":      model,
            "max_tokens": 8192,
            "messages":   [{"role": "user", "content": prompt}],
        }
    elif provider_key == "openai":
        headers["Authorization"] = f"Bearer {api_key}"
        payload = {
            "model":    model,
            "messages": [{"role": "user", "content": prompt}],
        }
    elif provider_key == "gemini":
        url = f"{url}/{model}:generateContent?key={api_key}"
        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
        }
    elif provider_key == "deepseek":
        headers["Authorization"] = f"Bearer {api_key}"
        payload = {
            "model":    model,
            "messages": [{"role": "user", "content": prompt}],
        }
    else:
        print(f"   [api] Provider '{provider_key}' not implemented")
        return "", 0, 0

    try:
        print(f"   -> Calling {provider['label']}...")
        t_start = time.time()

        r = requests.post(url, headers=headers, json=payload, timeout=300)
        elapsed = time.time() - t_start

        if r.status_code != 200:
            print(f"   [api] Error {r.status_code}: {r.text[:300]}")
            return "", 0, 0

        data = r.json()
        text = ""
        tokens_in  = 0
        tokens_out = 0

        if provider_key == "claude":
            for block in data.get("content", []):
                if block.get("type") == "text":
                    text += block.get("text", "")
            usage      = data.get("usage", {})
            tokens_in  = usage.get("input_tokens", 0)
            tokens_out = usage.get("output_tokens", 0)

        elif provider_key in ("openai", "deepseek"):
            choices = data.get("choices", [])
            if choices:
                text = choices[0].get("message", {}).get("content", "")
            usage      = data.get("usage", {})
            tokens_in  = usage.get("prompt_tokens", 0)
            tokens_out = usage.get("completion_tokens", 0)

        elif provider_key == "gemini":
            candidates = data.get("candidates", [])
            if candidates:
                parts = candidates[0].get("content", {}).get("parts", [])
                text  = "".join(p.get("text", "") for p in parts)
            usage      = data.get("usageMetadata", {})
            tokens_in  = usage.get("promptTokenCount", 0)
            tokens_out = usage.get("candidatesTokenCount", 0)

        cost_usd = (tokens_in / 1_000_000 * provider["price_input_per_m"] +
                    tokens_out / 1_000_000 * provider["price_output_per_m"])

        print(f"   [OK] {provider['label']}: {len(text)} chars | {elapsed:.1f}s")
        print(f"   📊 Tokens: {tokens_in:,} in + {tokens_out:,} out = "
              f"{tokens_in + tokens_out:,} total | Cost: ${cost_usd:.4f}")

        return text, tokens_in, tokens_out

    except Exception as e:
        print(f"   [api] Exception: {e}")
        return "", 0, 0


def _generate(prompt: str, model: str, api_provider: str = "", num_predict_override: int = None) -> tuple:
    if api_provider:
        return _generate_api(prompt, api_provider)
    else:
        return _generate_ollama(prompt, model, num_predict_override=num_predict_override)


# -- Output parser ----------------------------------------------------------

def _strip_html_comments(text: str) -> str:
    """Remove HTML comments from LLM output."""
    import re as _re
    text = _re.sub(r'<!--.*?-->', '', text, flags=_re.DOTALL)
    text = _re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


def _parse_output(text: str) -> dict:
    text = _strip_html_comments(text)
    sections = {
        "body":      "",
        "titles":    [],
        "excerpts":  [],
        "image_seo": [],
        "sources":   [],
        "tags":      [],
        "h1_title":  "",
    }

    normalized = text
    replacements = [
        (r"#{1,4}\s*TITLES\s*===",                          "=== TITLES ==="),
        (r"#{1,4}\s*EXCERPTS\s*===",                        "=== EXCERPTS ==="),
        (r"#{1,4}\s*IMAGE\s*SEO\s*TITLES\s*===",            "=== IMAGE SEO TITLES ==="),
        (r"#{1,4}\s*SOURCES\s*===",                         "=== SOURCES ==="),
        (r"(?m)^#{1,4}\s*TITLES\s*$",                       "=== TITLES ==="),
        (r"(?m)^#{1,4}\s*EXCERPTS\s*$",                     "=== EXCERPTS ==="),
        (r"(?m)^#{1,4}\s*IMAGE\s*SEO\s*TITLES\s*$",         "=== IMAGE SEO TITLES ==="),
        (r"(?m)^#{1,4}\s*SOURCES\s*$",                      "=== SOURCES ==="),
        (r"#{1,4}\s*SEO\s*Title\s*Variants?\s*\(?[^)]*\)?", "=== TITLES ==="),
        (r"#{1,4}\s*Title\s*Variants?\s*\(?[^)]*\)?",       "=== TITLES ==="),
        (r"#{1,4}\s*Excerpts?\s*\(?[^)]*\)?",               "=== EXCERPTS ==="),
        (r"#{1,4}\s*Meta\s*Descriptions?\s*\(?[^)]*\)?",    "=== EXCERPTS ==="),
        (r"#{1,4}\s*Image\s*SEO\s*Titles?\s*\(?[^)]*\)?",   "=== IMAGE SEO TITLES ==="),
        (r"#{1,4}\s*Image\s*Alt\s*Texts?\s*\(?[^)]*\)?",    "=== IMAGE SEO TITLES ==="),
        (r"#{1,4}\s*Sources?\s*\(?[^)]*\)?",                "=== SOURCES ==="),
    ]
    for pattern, replacement in replacements:
        normalized = re.sub(pattern, replacement, normalized, flags=re.IGNORECASE | re.MULTILINE)

    meta_match = re.search(r"<!--\s*META:\s*(.+?)\s*-->", normalized, re.IGNORECASE)
    if meta_match and "=== EXCERPTS ===" not in normalized:
        normalized = normalized.replace(meta_match.group(0), "")
        normalized += f"\n=== EXCERPTS ===\n1: {meta_match.group(1)}\n"

    markers = ["=== TITLES ===", "=== EXCERPTS ===",
               "=== IMAGE SEO TITLES ===", "=== SOURCES ==="]

    positions = {}
    for m in markers:
        idx = normalized.find(m)
        if idx != -1:
            positions[m] = idx

    if positions:
        first_pos = min(positions.values())
        sections["body"] = normalized[:first_pos].strip()
    else:
        sections["body"] = normalized.strip()

    for line in sections["body"].split("\n"):
        line = line.strip()
        if line.startswith("# "):
            sections["h1_title"] = line[2:].strip()
            break

    def _extract_section(marker, next_marker=None):
        if marker not in positions:
            return ""
        start = positions[marker] + len(marker)
        end   = positions[next_marker] if next_marker and next_marker in positions else len(normalized)
        return normalized[start:end].strip()

    def _parse_numbered_list(raw: str) -> list:
        items = []
        for line in raw.split("\n"):
            line = line.strip()
            if not line:
                continue
            line    = re.sub(r"\*{1,2}([^*]+)\*{1,2}", r"\1", line)
            cleaned = re.sub(r"^\d+[\.\:\)]\s*", "", line).strip().strip('"').strip("'")
            if cleaned and len(cleaned) > 3:
                items.append(cleaned)
        return items

    marker_order = ["=== TITLES ===", "=== EXCERPTS ===",
                    "=== IMAGE SEO TITLES ===", "=== SOURCES ==="]

    for i, marker in enumerate(marker_order):
        next_m = marker_order[i + 1] if i + 1 < len(marker_order) else None
        raw    = _extract_section(marker, next_m)
        items  = _parse_numbered_list(raw)

        if marker == "=== TITLES ===":
            sections["titles"] = items[:10]
        elif marker == "=== EXCERPTS ===":
            sections["excerpts"] = items[:6]
        elif marker == "=== IMAGE SEO TITLES ===":
            sections["image_seo"] = items[:5]
        elif marker == "=== SOURCES ===":
            sources = []
            for item in items:
                item = item.strip("<>").strip()
                if item:
                    sources.append(item)
            sections["sources"] = sources[:10]

    # Validate titles: 50-60 chars
    valid_titles = []
    for t in sections["titles"]:
        if len(t) > 60:
            t = t[:60].rsplit(" ", 1)[0]
        if 50 <= len(t) <= 60:
            valid_titles.append(t)
        elif len(t) < 50 and len(t) > 20:
            valid_titles.append(t)
    sections["titles"] = valid_titles[:10]

    # Validate excerpts: 120-150 chars
    valid_excerpts = []
    for e in sections["excerpts"]:
        if len(e) > 150:
            e = e[:150].rsplit(" ", 1)[0]
        if 120 <= len(e) <= 150:
            valid_excerpts.append(e)
        elif len(e) < 120 and len(e) > 50:
            valid_excerpts.append(e)
    sections["excerpts"] = valid_excerpts[:6]

    return sections


# -- Token log -------------------------------------------------------------

def _log_generation(item: dict, tokens_in: int, tokens_out: int,
                    elapsed: float, api_provider: str, body_chars: int) -> None:
    log_path = os.path.join(OUTPUT_DIR, "_generate_log.txt")
    stamp    = datetime.now().strftime("%Y-%m-%d %H:%M")

    from config import API_PROVIDERS
    if api_provider and api_provider in API_PROVIDERS:
        p        = API_PROVIDERS[api_provider]
        cost_usd = (tokens_in / 1_000_000 * p["price_input_per_m"] +
                    tokens_out / 1_000_000 * p["price_output_per_m"])
        provider_str = f"{api_provider:<10}"
        cost_str     = f"cost=${cost_usd:.4f}"
    else:
        provider_str = f"ollama/{item.get('model',''):<10}"
        cost_str     = "cost=$0.0000"

    line = (
        f"{stamp}  "
        f"id={item['id']}  "
        f"provider={provider_str}  "
        f"persona={item.get('persona', 'lukasz'):<10}  "
        f"in={tokens_in:>6,}  out={tokens_out:>6,}  "
        f"{cost_str}  "
        f"chars={body_chars:>6}  "
        f"time={int(elapsed)}s  "
        f"topic={item['topic'][:50]}\n"
    )

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(line)


# -- Save article -----------------------------------------------------------

def _save_article(item: dict, parsed: dict, api_provider: str = "",
                  tokens_in: int = 0, tokens_out: int = 0,
                  chunk_meta: list = None, rag_sources: list = None) -> str:
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    h1_title = parsed.get("h1_title") or item["topic"]
    slug     = re.sub(r"[^\w\s-]", "", h1_title.lower())
    slug     = re.sub(r"[\s_]+", "-", slug)[:60].strip("-")
    date     = datetime.now().strftime("%Y%m%d")
    dir_name = f"{date}-{slug}"
    article_dir = os.path.join(OUTPUT_DIR, dir_name)
    os.makedirs(article_dir, exist_ok=True)

    from config import API_PROVIDERS, DEFAULT_PERSONA
    if api_provider and api_provider in API_PROVIDERS:
        model_str = API_PROVIDERS[api_provider]["model"]
        cost_usd  = round(
            tokens_in  / 1_000_000 * API_PROVIDERS[api_provider]["price_input_per_m"] +
            tokens_out / 1_000_000 * API_PROVIDERS[api_provider]["price_output_per_m"],
            6
        )
    else:
        model_str = item["model"]
        cost_usd  = 0.0

    meta = parsed["excerpts"][0] if parsed["excerpts"] else ""
    meta = re.sub(r'\s*[-]\s*\d+\s*chars?.*$', "", meta).strip()
    meta = re.sub(r'\s*\(\d+\s*chars?[^)]*\)\s*$', "", meta).strip()
    meta = meta.replace('"', "'")

    image_seo = parsed.get("image_seo", [])
    if not image_seo:
        topic_words = re.sub(r"[^\w\s]", "", item["topic"]).strip()[:40]
        image_seo = [
            f"{topic_words} screenshot",
            f"{topic_words} gameplay",
            f"{topic_words} review image",
            f"{topic_words} detail",
            f"{topic_words} overview",
        ]

    article_sources = rag_sources or parsed.get("sources", [])

    # RAG context comment
    rag_summary = ""
    if chunk_meta:
        lines = ["", "<!-- RAG_CONTEXT"]
        lines.append(f"{'ID':<6} {'COLLECTION':<22} {'DOMAIN':<28} {'SCORE':>7}  {'TRUST':<8} PREVIEW")
        lines.append("-" * 90)
        for c in chunk_meta:
            cid     = c.get("id", "?")
            col     = c.get("collection", "?")[:21]
            domain  = c.get("domain", "?")[:27]
            score   = c.get("score", 0)
            trust   = c.get("trust", "?")[:7]
            preview = c.get("text", "")[:60].replace("\n", " ").replace("--", "-")
            lines.append(f"{cid:<6} {col:<22} {domain:<28} {score:>7.4f}  {trust:<8} {preview}")
        lines.append("-->")
        rag_summary = "\n".join(lines)

    # --- article.md ---
    article_path = os.path.join(article_dir, "article.md")
    with open(article_path, "w", encoding="utf-8") as f:
        f.write(parsed["body"] + rag_summary)

    # --- metadata.json ---
    metadata = {
        "title":            h1_title,
        "topic":            item["topic"],
        "category":         item.get("category", "other"),
        "article_type":     item.get("article_type", ""),
        "article_length":   item.get("article_length", "medium"),
        "section":          item["section"],
        "persona":          item.get("persona", DEFAULT_PERSONA),
        "model":            model_str,
        "provider":         api_provider if api_provider else "ollama",
        "queue_id":         item["id"],
        "generated_at":     datetime.now().isoformat(),
        "tokens_in":        tokens_in,
        "tokens_out":       tokens_out,
        "cost_usd":         cost_usd,
        "meta_description": meta,
        "titles":           parsed["titles"],
        "excerpts":         parsed["excerpts"],
        "image_seo_titles": image_seo,
        "sources":          article_sources,
        "tags":             parsed.get("tags", []),
    }

    metadata_path = os.path.join(article_dir, "metadata.json")
    with open(metadata_path, "w", encoding="utf-8") as f:
        json.dump(metadata, f, ensure_ascii=False, indent=2)

    # --- sources.json ---
    if chunk_meta:
        sources_path = os.path.join(article_dir, "sources.json")
        try:
            json.dump({
                "item_id":   item["id"],
                "topic":     item["topic"],
                "generated": datetime.now().isoformat(),
                "model":     model_str,
                "chunks":    chunk_meta,
                "domains":   {d: c for d, c in sorted(
                    {cm["domain"]: sum(1 for x in chunk_meta if x["domain"] == cm["domain"])
                     for cm in chunk_meta}.items(), key=lambda x: -x[1])},
            }, open(sources_path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
        except Exception as _e:
            print(f"   [sources] Could not save: {_e}")

    return article_dir


# -- Main function ----------------------------------------------------------


def _append_slug_registry(slug: str, category: str) -> None:
    """Append slug to data/slugs.json after successful article generation."""
    import json as _json
    import os as _os
    slugs_path = _os.path.join(
        _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))),
        "data", "slugs.json"
    )
    try:
        slugs = {}
        if _os.path.exists(slugs_path):
            with open(slugs_path, encoding="utf-8") as _f:
                slugs = _json.load(_f)
        cat_slugs = slugs.get(category, [])
        if slug and slug not in cat_slugs:
            cat_slugs.append(slug)
            slugs[category] = cat_slugs
            with open(slugs_path, "w", encoding="utf-8") as _f:
                _json.dump(slugs, _f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def build_upgrade_prompt(
    topic: str = "",
    existing_content: str = "",
    context_text: str = "",
    section: dict = None,
    upgrade_mode: str = "upgrade",
    notes: str = "",
) -> str:
    section_label = (section or {}).get("label", "DATA")

    MODE_INSTRUCTIONS = {
        "upgrade": (
            "Rewrite this article completely. Keep the topic and core facts. "
            "Improve structure, sharpen the argument, strengthen the opening, "
            "cut filler, increase specificity. The result must be significantly better."
        ),
        "expand": (
            "Keep the existing structure and main sections. "
            "Add new sections, deeper analysis, or missing angles. "
            "Do not remove existing content -- extend it."
        ),
        "refresh": (
            "Update facts and dates only. Keep structure and voice intact. "
            "Replace outdated information with current data from the research context. "
            "Minimal changes -- do not rewrite what does not need updating."
        ),
    }
    mode_instr = MODE_INSTRUCTIONS.get(upgrade_mode, MODE_INSTRUCTIONS["upgrade"])

    notes_block = ""
    if notes:
        notes_block = f"\n=== EDITOR NOTES ===\n{notes}\n=== END NOTES ===\n"

    existing_block = ""
    if existing_content:
        existing_block = (
            "=== EXISTING ARTICLE ===\n"
            + existing_content
            + "\n=== END EXISTING ARTICLE ===\n"
        )

    research_block = ""
    if context_text:
        research_block = (
            "=== RESEARCH CONTEXT -- paraphrase, never copy verbatim ===\n"
            + context_text
            + "\n=== END RESEARCH CONTEXT ==="
        )

    prompt = f"""You are rewriting an article for section {section_label}.

TASK: {mode_instr}

STRICT RULES:
- Paraphrase all research -- do not copy 5+ consecutive words from any source
- Only state facts present in the research context or existing article
- Do not invent quotes, statistics, or dates not in the context
- Output the article only -- no preamble, no commentary, no meta-notes
- Write in English

Topic: {topic}
{notes_block}
{existing_block}
{research_block}

Write the {upgrade_mode}d article now:"""

    return prompt


def _ensure_schema_and_focus(item: dict) -> None:
    """Run schema suggester and focus picker if not already set on item."""
    try:
        from menus.queue import _schema_suggester, _apply_schema, _set_schema_interactive
    except ImportError as e:
        print(f"  [warn] Could not import schema/focus helpers: {e}")
        return

    if not item.get("article_type"):
        print("  [schema] Analyzing research chunks...")
        suggested = _schema_suggester(item)
        if suggested:
            print(f"  [schema] Suggested: {suggested}")
            accept = input("  Accept? [Y/n/list]: ").strip().lower()
            if accept == "list":
                _set_schema_interactive(item)
            elif accept in ("", "y", "yes"):
                _apply_schema(item, suggested)
            else:
                _set_schema_interactive(item)
        else:
            print("  [schema] No suggestion -- pick manually or skip")
            _set_schema_interactive(item)




def run_generate(item_ids: list = None, api_provider: str = "", night_run: bool = False) -> int:
    if item_ids:
        all_items = queue.get_all()
        items = [i for i in all_items if i["id"] in item_ids
                 and i["status"] == queue.STATUS_RESEARCHED]
    if night_run:
        for item in items:
            item["night_run"] = True
    else:
        items = queue.get_researched()

    if not items:
        print("  No 'researched' topics in queue.")
        return 0

    provider_label = ""
    if api_provider:
        from config import API_PROVIDERS
        provider_label = f" via {API_PROVIDERS.get(api_provider, {}).get('label', api_provider)}"

    print("\n" + "=" * 60)
    print(f"  GENERATE  ({len(items)} articles{provider_label})")
    print("=" * 60)

    done_count = 0

    for idx, item in enumerate(items, 1):
        topic   = item["topic"]
        persona = item.get("persona", DEFAULT_PERSONA)
        section = item["section"]
        model   = item["model"]
        item_id = item["id"]

        # Schema suggester + focus picker (skipped in night_run)
        if not night_run:
            _ensure_schema_and_focus(item)
            # Reload fields that may have changed
            topic   = item.get("topic", topic)
            persona = item.get("persona", persona)
            model   = item.get("model", model)
        print(f"\n  [{idx}/{len(items)}] {topic[:65]}")
        print(f"   Persona: {persona}  |  Model: {model}  |  Section: {section}")
        if api_provider:
            from config import API_PROVIDERS
            print(f"   Provider: {API_PROVIDERS.get(api_provider, {}).get('label', api_provider)}")

        t_start = time.time()


        print("   -> Retrieving from Qdrant...")
        context_persona, context_research, context_evergreen, sources, chunk_meta = _retrieve_context(
            topic         = topic,
            item_id       = item_id,
            category      = item.get("category", "other"),
            seed_urls     = item.get("seed_urls", []),
            article_focus = item.get("article_focus", ""),
            persona       = item.get("persona", DEFAULT_PERSONA),
            topic_slug    = item.get("topic_slug", ""),
            top_k         = 20,
        )
        print(f"   -> Context: {len(context_persona)} persona (prompt cap:6000) + {len(context_research)} research chars  |  Sources: {len(sources)}")

        # -- Suitability gate (S32) ----------------------------------------
        try:
            from config import GATE_MIN_CHUNKS, GATE_MIN_AVG_SCORE, GATE_MIN_SOURCES
        except ImportError:
            GATE_MIN_CHUNKS, GATE_MIN_AVG_SCORE, GATE_MIN_SOURCES = 5, 0.25, 2

        _gate_passed, _gate_verdict, _gate_reason, _gate_stats = check_research_quality(
            chunk_meta       = chunk_meta,
            context_research = context_research,
            min_chunks       = GATE_MIN_CHUNKS,
            min_avg_score    = GATE_MIN_AVG_SCORE,
            min_sources      = GATE_MIN_SOURCES,
        )
        print_gate_result(_gate_passed, _gate_verdict, _gate_reason, _gate_stats)
        queue.update_field(item_id, "gate_stats", _gate_stats)

        if not _gate_passed:
            if item.get("night_run", False):
                # Night run: mark and skip -- do not generate
                print(f"   [gate] Night run: skipping topic (insufficient research).")
                queue.update_field(item_id, "gate_verdict", _gate_verdict)
                queue.update_status(item_id, queue.STATUS_RESEARCHED)
                continue
            else:
                _gate_choice = gate_prompt_interactive(_gate_verdict, _gate_reason)
                if _gate_choice == "skip":
                    print("   [gate] Skipped -- re-run research first.")
                    queue.update_status(item_id, queue.STATUS_RESEARCHED)
                    continue
                elif _gate_choice == "delete":
                    print("   [gate] Deleted from queue.")
                    queue.remove(item_id)
                    continue
                # else "continue" -- user accepted risk, proceed

        upgrade_url  = item.get("upgrade_url", "")
        upgrade_mode = item.get("upgrade_mode", "")

        article_length = item.get("article_length", "medium")
        if not article_length or article_length not in ("short", "medium", "long"):
            article_length = "medium"

        if upgrade_url and upgrade_mode:
            prompt = build_upgrade_prompt(
                topic            = topic,
                existing_content = item.get("existing_content", ""),
                context_text     = context_research,
                section          = {"nr": 1, "label": section},
                upgrade_mode     = upgrade_mode,
                notes            = item.get("notes", ""),
            )
        else:
            # -- Focus Picker (interactive only) --------------------------
            article_focus = item.get("article_focus", "").strip().strip('"')
            if not item.get("night_run", False):
                _fp_model = model
                try:
                    from pipeline.focus_picker import run_focus_picker
                    if article_focus:
                        # Focus already set -- ask user what to do
                        print(f"   [picker] Existing focus: {article_focus[:90]}")
                        _fp_ans = input("   [K]eep / [R]egenerate angles / [C]ustom: ").strip().lower()
                        if _fp_ans == "r":
                            article_focus = run_focus_picker(
                                topic            = topic,
                                context_research = context_research,
                                model            = _fp_model,
                                ollama_url       = OLLAMA_URL,
                            )
                            if article_focus:
                                item["article_focus"] = article_focus
                                queue.update_field(item_id, "article_focus", article_focus)
                        elif _fp_ans == "c":
                            _custom = input("   Custom focus: ").strip()
                            if _custom:
                                article_focus = _custom
                                item["article_focus"] = article_focus
                                queue.update_field(item_id, "article_focus", article_focus)
                        # else: keep existing -- article_focus unchanged
                    else:
                        # No focus -- run picker
                        article_focus = run_focus_picker(
                            topic            = topic,
                            context_research = context_research,
                            model            = _fp_model,
                            ollama_url       = OLLAMA_URL,
                        )
                        if article_focus:
                            item["article_focus"] = article_focus
                            queue.update_field(item_id, "article_focus", article_focus)
                except Exception as _fp_err:
                    print(f"   [picker] Error: {_fp_err} -- continuing without focus")
                    if not article_focus:
                        article_focus = ""
            # -- resolve article_length ------------------------------

            # -- resolve article_lang ---------------------------------
            article_lang = item.get("article_lang", "en")
            if article_lang not in ("en", "pl", "no"):
                article_lang = "en"

            # Resolve article schema from data/schemas/<type>.json
            import json as _sj, os as _so
            _schemas_dir = _so.path.join(_so.path.dirname(_so.path.dirname(_so.path.abspath(__file__))), "data", "schemas")
            def _load_schema(name):
                p = _so.path.join(_schemas_dir, f"{name}.json")
                if _so.path.exists(p):
                    try:
                        with open(p, encoding="utf-8") as _f: return _sj.load(_f)
                    except Exception: return None
                return None
            _article_type = item.get("article_type", "").strip()
            if _article_type:
                _article_schema = _load_schema(_article_type)
                if _article_schema:
                    print(f"   [schema] {_article_type} -> {_article_schema.get('label', '?')}")
                else:
                    print(f"   [schema] WARNING: {_article_type}.json not found -- using default")
                    _article_schema = _load_schema("default")
            else:
                print("   [schema] No article_type set -- using default schema")
                _article_schema = _load_schema("default")

            # -- Generate unique section names ----------------------------
            # Replaces generic schema section names with article-specific H2s.
            # Fast 27b call using research context + focus.
            if _article_schema and _article_schema.get("sections") and context_research and len(context_research.strip()) >= 500:
                try:
                    import requests as _rn, json as _jn, copy as _cn
                    _sections = _article_schema["sections"]
                    _n = len(_sections)
                    _slist = "\n".join(
                        str(_si+1) + ". " + _s["name"] + " -- " + _s["purpose"]
                        for _si, _s in enumerate(_sections)
                    )
                    _rpreview = context_research[:3000]
                    _focus_line = ("Article angle: " + article_focus + "\n") if article_focus else ""
                    _sn_prompt = (
                        "You are writing an article about: " + topic + "\n"
                        + _focus_line
                        + "\nThe article has " + str(_n) + " sections with these purposes:\n"
                        + _slist
                        + "\n\nResearch excerpt:\n" + _rpreview + "\n\n"
                        + "Generate a unique, specific H2 header name for each section.\n"
                        + "Rules:\n"
                        + "- Each name must reflect the ACTUAL content in the research, not a generic label\n"
                        + "- Use concrete details: names, facts, specific decisions or outcomes from the research\n"
                        + "- Never use dates or calendar references in section names\n"
                        + "- 3-8 words per name. No questions. No colons.\n"
                        + "- Keep the order of sections unchanged\n"
                        + "- Output ONLY a JSON array of strings, one per section, nothing else\n"
                        + "Example for " + str(_n) + " sections: " + '["Name 1", "Name 2"]' + "\n"
                        + "Do not include any explanation or markdown."
                    )
                    from config import OLLAMA_EMBED_URL as _SN_URL, META_MODEL as _sn_model
                    _sn_r = _rn.post(
                        _SN_URL + "/api/chat",
                        json={
                            "model": _sn_model, "think": False, "stream": False,
                            "messages": [{"role": "user", "content": _sn_prompt}],
                            "options": {"num_predict": 400, "temperature": 0.4},
                        },
                        timeout=120,
                    )
                    _sn_r.raise_for_status()
                    _sn_raw = _sn_r.json().get("message", {}).get("content", "").strip()
                    if "</think>" in _sn_raw:
                        _sn_raw = _sn_raw.split("</think>")[-1].strip()
                    # Strip markdown code fences
                    import re as _sn_re
                    _sn_raw = _sn_re.sub(r"```[a-z]*", "", _sn_raw).strip("`").strip()
                    # Try direct parse first
                    try:
                        _sn_names = _jn.loads(_sn_raw)
                    except Exception:
                        # Fallback: extract first [...] array from response
                        _sn_match = _sn_re.search(r'\[.*?\]', _sn_raw, _sn_re.DOTALL)
                        if _sn_match:
                            _sn_names = _jn.loads(_sn_match.group(0))
                        else:
                            raise ValueError("No JSON array found in response")
                    if isinstance(_sn_names, list) and len(_sn_names) == _n:
                        _new_schema = _cn.deepcopy(_article_schema)
                        _last_idx = _n - 1
                        for _sni, _snname in enumerate(_sn_names):
                            # Last section gets a verdict-framing name, not a question
                            # Purpose is preserved via schema, only the label changes
                            _snname = str(_snname).strip()
                            if _snname and len(_snname) > 3:
                                _new_schema["sections"][_sni]["name"] = _snname
                        _article_schema = _new_schema
                        print("   [sections] Generated unique section names:")
                        for _sn_s in _article_schema["sections"]:
                            print("     - " + _sn_s["name"])
                    else:
                        print("   [sections] Wrong count -- using defaults")
                except Exception as _sn_e:
                    print("   [sections] Failed: " + str(_sn_e) + " -- using defaults")

            # -- Focus validator ------------------------------------------
            if article_focus:
                try:
                    from pipeline.focus_validator import run_focus_validator, print_validation
                    from config import OLLAMA_URL as _FV_URL
                    _fv = run_focus_validator(
                        topic            = topic,
                        article_focus    = article_focus,
                        context_research = context_research,
                        model            = model,
                        ollama_url       = OLLAMA_URL,
                        night_run        = item.get("night_run", False),
                    )
                    print_validation(_fv)
                    queue.update_field(item_id, "focus_validation", _fv)
                except Exception as _fv_err:
                    print(f"   [focus-validator] Error: {_fv_err} -- skipping")
            # -- end focus validator -------------------------------------

            prompt = _build_prompt(
                topic            = topic,
                section          = section,
                context_persona  = context_persona,
                context_research  = context_research,
                context_evergreen = context_evergreen,
                sources          = sources,
                article_focus    = article_focus,
                category         = item.get("category", "other"),
                model            = model,
                article_length   = article_length,
                article_schema   = _article_schema,
                chunk_meta       = chunk_meta,
            )

        # -- two_pass: pass 1 -- body ----------------------------------
        _length_predict = {"short": 1500, "medium": 4500, "long": 6000}
        _pass1_predict  = _length_predict.get(article_length, 2500)

        print(f"   -> Generating [{article_length}] pass 1/2 body ({model})...\n")
        body_text, tokens_in, tokens_out_1 = _generate(
            prompt, model, api_provider, num_predict_override=_pass1_predict
        )

        if not body_text:
            queue.update_status(item_id, queue.STATUS_ERROR, "LLM returned empty output (pass 1)")
            continue

        # strip HTML comments from body
        body_text = _strip_html_comments(body_text)

        # -- two_pass: pass 2 -- TITLES + EXCERPTS --------------------
        _titles_prompt = f"""You are an SEO editor. Given the article below, generate:

=== TITLES ===
10 SEO title variants, EXACTLY 50-60 characters each, numbered 1-10.
Each title MUST contain a thesis or argument -- not just a topic name.

GOOD titles (have a point of view):
- "Lego Batman Dark Knight Trades Combat For Open World Charm"
- "Doom Dark Ages Bold Vision Beats Safe Franchise Rehashes"

BAD titles (generic, no argument):
- "Lego Batman Legacy Of The Dark Knight Tips And Tricks"
- "Doom Dark Ages Review"

Rules: no source/publication names, no "Review" or "Guide" as the only noun, no questions.

=== EXCERPTS ===
6 meta description variants, EXACTLY 120-150 characters each, numbered 1-6.
Each must describe what the article actually argues -- not a generic teaser.
No CTAs ("Read more now", "Check it out", etc.)

After the last excerpt, output exactly this line and nothing else:
=== END ===

ARTICLE:
{body_text[:4000]}
"""
        print(f"   -> Generating pass 2/2 titles+excerpts...\n")
        meta_text, _, tokens_out_2 = _generate(
            _titles_prompt, model, api_provider, num_predict_override=900
        )
        tokens_out = tokens_out_1 + tokens_out_2

        # -- pass 3 (optional): translation ----------------------
        # Triggered by item flag translate=True -- never asks interactively
        # Saves to article_pl.md (or article_no.md) -- English body unchanged
        _lang_name = {"pl": "Polish", "no": "Norwegian"}
        _translate_flag = item.get("translate", False)
        _trans_lang     = item.get("article_lang", "en")

        if _translate_flag and _trans_lang in _lang_name:
            _lang_full = _lang_name[_trans_lang]
            _trans_prompt = f"""Translate the following article to {_lang_full}.
Preserve all Markdown formatting exactly -- headers (##), bold, italics, line breaks.
Translate only the text content. Do not add, remove, or rewrite anything.
Output only the translated article, nothing else.

ARTICLE:
{body_text}"""
            print(f"   -> Translating to {_lang_full} (pass 3/3)...\n")
            _trans_text, _, _tokens_trans = _generate(
                _trans_prompt, model, api_provider, num_predict_override=4000
            )
            if _trans_text and len(_trans_text) > 200:
                tokens_out_1 += _tokens_trans
                tokens_out = tokens_out_1 + tokens_out_2
                print(f"   [OK] Translation: {len(_trans_text)} chars -> article_{_trans_lang}.md")
                # Save translation to separate file after _save_article
                _pending_translation = (_trans_text, _trans_lang)
            else:
                print(f"   ⚠ Translation failed or too short -- skipping")
                _pending_translation = None
        else:
            _pending_translation = None

        # combine for parser
        combined_text = body_text + "\n\n" + (meta_text or "")
        parsed = _parse_output(combined_text)

        title_lens   = [len(t) for t in parsed["titles"]]
        excerpt_lens = [len(e) for e in parsed["excerpts"]]
        print(f"\n   -> Parsed: {len(parsed['titles'])} titles {title_lens}  "
              f"{len(parsed['excerpts'])} excerpts {excerpt_lens}  "
              f"{len(parsed['image_seo'])} img  {len(parsed['sources'])} sources  "
              f"{len(parsed.get('tags', []))} tags")

        elapsed     = time.time() - t_start
        article_dir = _save_article(item, parsed, api_provider, tokens_in, tokens_out,
                                    chunk_meta=chunk_meta, rag_sources=sources)

        _log_generation(item, tokens_in, tokens_out, elapsed, api_provider,
                        len(parsed["body"]))

        print(f"   [OK] Saved: {article_dir}")

        # -- Scoring pass -------------------------------------------------------
        try:
            from pipeline.scoring_pass import score_article
            from config import OLLAMA_URL as _SCORING_OLLAMA_URL
            _scoring = score_article(
                article_dir  = article_dir,
                item         = item,
                ollama_url   = _SCORING_OLLAMA_URL,
                article_type = item.get("article_type", ""),
                model        = model,
            )
            # Flag for human review -- does not block pipeline
            if _scoring.get("verdict") in ("fail", "weak"):
                import core.queue as _q
                _q.update_field(item_id, "scoring_flag", True)
                _q.update_field(item_id, "scoring_verdict", _scoring.get("verdict", ""))
                _q.update_field(item_id, "scoring_score", _scoring.get("score"))
        except Exception as _sc_err:
            print(f"   [scoring] Error: {_sc_err} -- skipping")

        # Save translation file if requested
        if _pending_translation:
            _trans_body, _trans_lang_code = _pending_translation
            _trans_path = os.path.join(article_dir, f"article_{_trans_lang_code}.md")
            try:
                with open(_trans_path, "w", encoding="utf-8") as _tf:
                    _tf.write(_trans_body)
                print(f"   [OK] Translation saved: article_{_trans_lang_code}.md")
            except Exception as _te:
                print(f"   âš  Translation save failed: {_te}")
        _item_slug = item.get("slug") or item.get("topic_slug", "")
        if _item_slug:
            _append_slug_registry(_item_slug, item.get("category", "other"))
        print(f"   [OK] Time: {elapsed:.1f}s  |  Body: {len(parsed['body'])} chars  "
              f"|  Tokens: {tokens_in:,}+{tokens_out:,}")

        # Research chunks kept until item is removed from queue
        # Cleanup happens via queue [x] remove

        queue.update_status(item_id, queue.STATUS_DONE)
        done_count += 1

    print(f"\n  -> Generate done: {done_count}/{len(items)} articles.\n")
    return done_count