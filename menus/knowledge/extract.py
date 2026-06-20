# menus/knowledge/extract.py -- LLM-assisted fact extraction from research chunks
#
# Flow:
#   1. Select research collection + item
#   2. For each URL: qwen3.5:4b classifies if chunks have extractable facts
#   3. qwen3.6:27b extracts 3-6 fact paragraphs per URL
#   4. User reviews: [k] keep all / [d N] delete / [e N] edit / [s] skip
#   5. Approved facts embedded + upserted to knowledge_{cat}

import re
import uuid
import requests
from datetime import datetime, timezone

from .chunk_utils import _garbage_label, _clean_markdown
from .qdrant_ops import _ensure_collection, _load_all_points, _delete_ids


# -- Config --------------------------------------------------------------------

_EXTRACTOR_MODEL  = "qwen3.6:27b"


def _ollama_generate(model: str, prompt: str, think: bool = False) -> str:
    from config import OLLAMA_URL
    try:
        r = requests.post(
            f"{OLLAMA_URL}/api/chat",
            json={
                "model":    model,
                "think":    think,
                "messages": [{"role": "user", "content": prompt}],
                "stream":   False,
                "options":  {"num_predict": 2048, "temperature": 0.1},
            },
            timeout=120,
        )
        data = r.json()
        return data.get("message", {}).get("content", "").strip()
    except Exception as e:
        print(f"  [llm] Error: {e}")
        return ""


# -- Step 1: Extract facts -----------------------------------------------------

def _extract_facts(chunks: list, slug: str, category: str) -> list[str]:
    """qwen3.6:27b -- extract 3-6 fact paragraphs from URL chunks."""
    # Combine all chunk texts, cap at 6000 chars
    combined = ""
    for c in chunks:
        text = c.payload.get("text", "")
        if len(combined) + len(text) > 6000:
            break
        combined += text + "\n\n"

    prompt = f"""You are extracting factual knowledge for a knowledge base.
Topic: {slug} (category: {category})

Source text:
{combined.strip()}

Extract 3 to 6 key facts from this text that are:
- Directly relevant to the topic "{slug}"
- Factual and specific (dates, names, features, quotes, numbers)
- Useful context for writing articles about this topic
- NOT opinions, marketing language, or navigation text

If the text contains no relevant facts about the topic, respond with exactly: NONE

Format: one fact per paragraph, 3-5 sentences each.
Separate facts with a blank line.
Write only the facts, no headers, no numbering, no preamble, no explanation."""

    result = _ollama_generate(_EXTRACTOR_MODEL, prompt, think=False)
    if not result or result.strip().upper() == "NONE":
        return []

    # Filter out meta-responses where model explains it found nothing
    _META_PATTERNS = [
        r"the (provided |source )?text does not contain",
        r"no factual (information|knowledge|details)",
        r"cannot be extracted",
        r"it is impossible to extract",
        r"the (content|material|text) (exclusively|focuses|discusses)",
        r"consequently,",
    ]
    facts = [f.strip() for f in re.split(r"\n{2,}", result) if f.strip()]
    facts = [f for f in facts if len(f) > 80]
    facts = [f for f in facts if not any(
        re.search(p, f.lower()) for p in _META_PATTERNS
    )]
    return facts[:8]


# -- Step 3: Embed + upsert facts ----------------------------------------------

def _embed_text(text: str) -> list | None:
    from config import OLLAMA_EMBED_URL, EMBED_MODEL
    try:
        r = requests.post(
            f"{OLLAMA_EMBED_URL}/api/embed",
            json={"model": EMBED_MODEL, "input": text},
            timeout=60,
        )
        embs = r.json().get("embeddings", [])
        return embs[0] if embs else None
    except Exception as e:
        print(f"  [embed] Error: {e}")
        return None


def _sparse_vector(text: str) -> dict:
    tokens = re.findall(r"[a-z0-9]+", text.lower())
    if not tokens:
        return {"indices": [0], "values": [0.0]}
    tf: dict = {}
    for t in tokens:
        if len(t) >= 2:
            tf[t] = tf.get(t, 0) + 1
    idx_map: dict = {}
    total = max(len(tokens), 1)
    for token, count in tf.items():
        idx = abs(hash(token)) % (2**20)
        idx_map[idx] = idx_map.get(idx, 0.0) + round(count / total, 6)
    return {
        "indices": list(idx_map.keys()),
        "values":  [round(v, 6) for v in idx_map.values()],
    }


def _upsert_facts(qdrant_url: str, collection: str, facts: list[str],
                  slug: str, category: str, source_url: str,
                  source_domain: str, item_id: str,
                  chunk_idx_offset: int = 0) -> int:
    from config import QDRANT_URL
    now_iso = datetime.now(timezone.utc).isoformat()
    points  = []

    for fact_idx, fact in enumerate(facts):
        vec = _embed_text(fact)
        if not vec:
            continue
        points.append({
            "id":     uuid.uuid4().int >> 64,
            "vector": {
                "dense":  vec,
                "sparse": _sparse_vector(fact),
            },
            "payload": {
                "text":           _clean_markdown(fact),
                "topic":          slug,
                "topic_slug":     slug,
                "category":       category,
                "item_id":        item_id,
                "url":            source_url,
                "domain":         source_domain,
                "source":         "extracted",
                "knowledge":      True,
                "evergreen":      False,
                "accepted_at":    now_iso,
                "indexed_at":     now_iso,
                "domain_trust":   "trusted",
                "trust_score":    0.85,
                "retrieval_boost": 0.85,
                "chunk_idx":      chunk_idx_offset + fact_idx,
            },
        })

    if not points:
        return 0

    try:
        r = requests.put(
            f"{qdrant_url}/collections/{collection}/points",
            json={"points": points},
            timeout=60,
        )
        return len(points) if r.status_code in (200, 201) else 0
    except Exception as e:
        print(f"  [upsert] Error: {e}")
        return 0


# -- Review UI for one URL -----------------------------------------------------

def _review_facts(facts: list[str], domain: str, url_num: int, url_total: int) -> list[str]:
    """
    Show extracted facts, let user keep/delete/edit.
    Returns list of approved fact texts.
    """
    approved = list(facts)

    while True:
        print()
        print(f"  {'='*60}")
        print(f"  [{url_num}/{url_total}] {domain} -- {len(approved)} facts extracted")
        print(f"  {'='*60}")
        print()

        for i, fact in enumerate(approved, 1):
            print(f"  [F{i:02d}]")
            words = fact.split()
            line  = "  "
            for word in words:
                if len(line) + len(word) + 1 > 88:
                    print(line)
                    line = "  " + word + " "
                else:
                    line += word + " "
            if line.strip():
                print(line)
            print()

        print(f"  {'-'*60}")
        print(f"  k        keep all and continue")
        print(f"  d N      delete fact N  (d 1 3 = delete F01 and F03)")
        print(f"  e N      edit fact N in notepad")
        print(f"  s        skip this URL")
        print(f"  q        quit extract session")
        print()

        raw   = input("  > ").strip()
        parts = raw.split()
        cmd0  = parts[0].lower() if parts else ""

        if not raw or cmd0 == "k":
            return approved

        if cmd0 == "s":
            return []

        if cmd0 == "q":
            return None  # signal quit

        if cmd0 == "d" and len(parts) > 1:
            to_del = set()
            for token in parts[1:]:
                if token.lstrip("fF").isdigit():
                    n = int(token.lstrip("fF")) - 1
                    if 0 <= n < len(approved):
                        to_del.add(n)
            if to_del:
                approved = [f for i, f in enumerate(approved) if i not in to_del]
                print(f"  Deleted {len(to_del)} fact(s). {len(approved)} remaining.")
            continue

        if cmd0 == "e" and len(parts) == 2:
            token = parts[1].lstrip("fF")
            if token.isdigit():
                n = int(token) - 1
                if 0 <= n < len(approved):
                    from .chunk_utils import _edit_notepad
                    edited = _edit_notepad(approved[n], label=f"fact_{n+1}")
                    if edited:
                        approved[n] = edited
                        print(f"  F{n+1:02d} updated.")
            continue

        print("  k = keep all | d N = delete | e N = edit | s = skip | q = quit")


# -- Main extract session ------------------------------------------------------

def _extract_menu(client) -> None:
    """[e] Extract -- LLM fact extraction from research to knowledge."""
    from config import QDRANT_URL

    try:
        all_cols      = [c.name for c in client.get_collections().collections]
        research_cols = sorted([c for c in all_cols if c.startswith("research_")
                                and client.get_collection(c).points_count > 0])
    except Exception as e:
        print(f"  x Qdrant error: {e}")
        return

    if not research_cols:
        print("  No research collections with data.")
        return

    print()
    print("  Select research collection:")
    for i, c in enumerate(research_cols, 1):
        n = client.get_collection(c).points_count
        print(f"  [{i}] {c:<35} {n:>6} chunks")
    print()
    raw = input("  > ").strip()
    if not raw.isdigit() or not (1 <= int(raw) <= len(research_cols)):
        return
    col = research_cols[int(raw) - 1]
    category = col.replace("research_", "")

    # Load all points, group by item_id -> url
    print(f"\n  Loading {col}...")
    all_points = _load_all_points(client, col)
    if not all_points:
        print("  Empty.")
        return
    print(f"  {len(all_points)} chunks loaded.")

    # Group by item_id
    items: dict = {}
    for p in all_points:
        pay  = p.payload
        iid  = pay.get("item_id", "unknown")
        url  = pay.get("url", "")
        if iid not in items:
            items[iid] = {
                "topic":    pay.get("topic", ""),
                "category": pay.get("category", category),
                "slug":     pay.get("topic_slug", ""),
                "urls":     {},
            }
        if url not in items[iid]["urls"]:
            items[iid]["urls"][url] = []
        items[iid]["urls"][url].append(p)

    # Enrich slugs from queue -- queue has correct slugs, Qdrant payload may not
    try:
        import core.queue as _queue
        _queue_items = {i["id"]: i for i in _queue.get_all()}
        for iid in items:
            if not items[iid]["slug"] and iid in _queue_items:
                items[iid]["slug"] = _queue_items[iid].get("topic_slug", "")
    except Exception:
        pass

    # Enrich slugs from queue -- queue has correct slugs, Qdrant payload may not
    try:
        import core.queue as _queue
        _queue_items = {i["id"]: i for i in _queue.get_all()}
        for iid in items:
            if not items[iid]["slug"] and iid in _queue_items:
                items[iid]["slug"] = _queue_items[iid].get("topic_slug", "")
    except Exception:
        pass

    # Sort chunks within each URL
    for iid in items:
        for url in items[iid]["urls"]:
            items[iid]["urls"][url].sort(key=lambda p: p.payload.get("chunk_idx", 0))

    item_list = sorted(items.items(),
                       key=lambda x: sum(len(v) for v in x[1]["urls"].values()),
                       reverse=True)

    # Item selection
    print()
    print(f"  {'='*60}")
    print(f"  {col} -- {len(item_list)} items")
    print(f"  {'='*60}")
    print()
    for i, (iid, data) in enumerate(item_list, 1):
        total  = sum(len(v) for v in data["urls"].values())
        n_urls = len(data["urls"])
        topic  = data["topic"][:55]
        print(f"  [{i}]  {topic:<55} {total:>4} chunks  {n_urls} URLs")
    print()
    print("  [1-N] select item  [a] all items  [Enter] back")
    print()
    raw = input("  > ").strip().lower()
    if not raw or raw == "q":
        return

    if raw == "a":
        selected = [(iid, data) for iid, data in item_list]
    elif raw.isdigit() and 1 <= int(raw) <= len(item_list):
        idx = int(raw) - 1
        selected = [item_list[idx]]
    else:
        return

    # Process selected items
    stats = {"extracted": 0, "skipped": 0, "urls": 0}
    target_col = f"knowledge_{category}"
    _ensure_collection(client, target_col)

    for iid, data in selected:
        topic    = data["topic"]
        slug     = data["slug"] or data["topic"] or iid[:8]
        url_list = list(data["urls"].items())

        print()
        print(f"  {'='*60}")
        print(f"  {topic[:60]}")
        print(f"  slug={slug}  {len(url_list)} URLs")
        print(f"  {'='*60}")

        # Resume: find URLs already extracted to knowledge_{cat}
        done_urls: set = set()
        try:
            existing = _load_all_points(client, target_col)
            for p in existing:
                if p.payload.get("item_id") == iid:
                    done_urls.add(p.payload.get("url", ""))
        except Exception:
            pass
        if done_urls:
            print(f"  Resume: {len(done_urls)} URLs already extracted -- skipping")

        url_idx = 0
        while url_idx < len(url_list):
            url, chunks = url_list[url_idx]
            domain = chunks[0].payload.get("domain", "?") if chunks else "?"

            # Resume skip
            if url in done_urls:
                url_idx += 1
                continue

            # Skip garbage URLs
            garbage_count = sum(1 for c in chunks
                                if _garbage_label(c.payload.get("text", ""), url))
            if garbage_count == len(chunks):
                print(f"  - [{url_idx+1}/{len(url_list)}] {domain} -- all garbage, skip")
                url_idx += 1
                continue

            clean_chunks = [c for c in chunks
                            if not _garbage_label(c.payload.get("text", ""), url)]

            print(f"\n  [{url_idx+1}/{len(url_list)}] {domain}  ({len(clean_chunks)} clean chunks)")

            print(f"  Extracting... ", end="", flush=True)
            facts = _extract_facts(clean_chunks, slug, category)
            if not facts:
                print(f"nothing extracted -- skip")
                stats["skipped"] += 1
                url_idx += 1
                continue
            print(f"{len(facts)} facts")

            # -- Auto-dedup via reranker: skip facts already in knowledge ----
            # Cosine similarity on qwen3-embedding gives 0.75-0.84 for clear
            # paraphrases -- not enough separation for reliable threshold.
            # Cross-encoder reranker (BAAI/bge-reranker-v2-m3) gives much
            # better signal for semantic duplicates.
            #
            # Strategy: for each new fact, query reranker with fact as query
            # and existing knowledge chunks as documents. If top score >= 0.75
            # the fact is a duplicate of existing knowledge.
            # Intra-batch dedup: accepted facts added to existing_texts pool.
            _RERANK_DUP_THRESHOLD = 0.85
            _unique_facts         = []
            _dup_count            = 0

            # Load existing knowledge texts for this slug
            _existing_texts = []
            try:
                from qdrant_client.models import Filter as _DFilter, FieldCondition as _DFC, MatchValue as _DMV
                _kpoints, _ = client.scroll(
                    collection_name = target_col,
                    scroll_filter   = _DFilter(must=[
                        _DFC(key="topic_slug", match=_DMV(value=slug))
                    ]),
                    limit           = 500,
                    with_payload    = ["text"],
                    with_vectors    = False,
                )
                for _kp in _kpoints:
                    _t = _kp.payload.get("text", "")
                    if _t:
                        _existing_texts.append(_t)
            except Exception:
                pass

            # Reranker-based dedup
            if _existing_texts:
                try:
                    import requests as _rreq
                    from config import RERANKER_URL
                    _rh = _rreq.get(f"{RERANKER_URL}/health", timeout=2)
                    _reranker_ok = _rh.status_code == 200
                except Exception:
                    _reranker_ok = False

                if _reranker_ok:
                    print(f"  Dedup check ({len(facts)} facts vs {len(_existing_texts)} existing)... ", end="", flush=True)
                    for _fact in facts:
                        try:
                            _rresp = _rreq.post(
                                f"{RERANKER_URL}/rerank",
                                json={
                                    "query":     _fact,
                                    "documents": _existing_texts,
                                    "top_n":     1,
                                },
                                timeout=30,
                            )
                            _top_score = 0.0
                            if _rresp.status_code == 200:
                                _results = _rresp.json().get("results", [])
                                if _results:
                                    _top_score = _results[0].get("score", 0.0)
                        except Exception:
                            _top_score = 0.0

                        if _top_score >= _RERANK_DUP_THRESHOLD:
                            _dup_count += 1
                        else:
                            _unique_facts.append(_fact)
                            _existing_texts.append(_fact)  # intra-batch dedup

                    print(f"{_dup_count} dup(s) removed, {len(_unique_facts)} unique")
                    facts = _unique_facts
                else:
                    # Reranker unavailable -- skip dedup, show all facts
                    print(f"  [dedup] Reranker unavailable -- showing all facts")

            if not facts:
                print(f"  All facts are duplicates -- skipping URL")
                stats["skipped"] += 1
                url_idx += 1
                continue
            # -- end auto-dedup ------------------------------------------------

            # Review
            approved = _review_facts(facts, domain, url_idx + 1, len(url_list))

            if approved is None:  # quit signal
                print()
                print(f"  -- extract stopped: saved={stats['extracted']} skipped={stats['skipped']}")
                return

            if not approved:
                stats["skipped"] += 1
                url_idx += 1
                continue

            # Embed + upsert
            print(f"  Embedding {len(approved)} facts... ", end="", flush=True)
            # chunk_idx_offset: count facts already saved for this url
            # so idx is sequential across multiple extract sessions
            _existing_count = sum(
                1 for _kp in _kpoints
                if _kp.payload.get("url", "") == url
            ) if '_kpoints' in dir() else 0
            saved = _upsert_facts(
                qdrant_url        = QDRANT_URL,
                collection        = target_col,
                facts             = approved,
                slug              = slug,
                category          = category,
                source_url        = url,
                source_domain     = domain,
                item_id           = iid,
                chunk_idx_offset  = _existing_count,
            )
            print(f"{saved} saved -> {target_col}")
            stats["extracted"] += saved
            stats["urls"] += 1
            url_idx += 1

    print()
    print(f"  -- done: {stats['extracted']} facts extracted from {stats['urls']} URLs  ({stats['skipped']} skipped)")
    print()
    input("  [Enter] continue ->")
