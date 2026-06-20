# menus/knowledge/feed.py -- Feed menu: Clip, Paste, Manual JSON, Persona

import os
import sys
import subprocess
import pathlib

from .chunk_utils import _clean_markdown, _edit_notepad, _embed_and_upsert_chunks
from .persona_builder import _persona_builder_menu


def _ask_slug_and_category() -> tuple:
    """
    Ask for category, destination, and topic_slug.
    Returns (category, slug, collection, extra_categories) where:
      - collection = "knowledge_{category}" for topic-specific chunks (slug required)
      - collection = "knowledge_evergreen"  for evergreen chunks
      - extra_categories = list of additional categories (for multi-category evergreen)

    ℹ  knowledge_{cat}    -- title/product-specific facts (REPLACED lore, UE6 features)
       knowledge_evergreen -- reusable concepts across articles (Nanite, DLSS, ray tracing)
                             NOT for title-specific facts
    """
    try:
        from config import RESEARCH_CATEGORIES_DATA
    except Exception:
        RESEARCH_CATEGORIES_DATA = ["games","hardware","software","security","ai-data","other"]

    print()
    print("  ℹ  knowledge_{cat}     = title/product facts (REPLACED lore, UE6 features)")
    print("     knowledge_evergreen = reusable concepts (Nanite, DLSS, ray tracing)")
    print()
    print("  Category:")
    for i, cat in enumerate(RESEARCH_CATEGORIES_DATA, 1):
        print(f"    [{i}] {cat}")
    cat_raw  = input("  > ").strip()
    category = RESEARCH_CATEGORIES_DATA[int(cat_raw)-1] if cat_raw.isdigit() and 1 <= int(cat_raw) <= len(RESEARCH_CATEGORIES_DATA) else "other"

    print()
    print("  Destination:")
    print(f"  [1] knowledge_{category}  -- topic-specific (requires slug)")
    print(f"  [2] knowledge_evergreen   -- single category ({category})")
    print(f"  [3] knowledge_evergreen   -- multi-category (select below)")
    print(f"  [4] knowledge_evergreen   -- global (all categories)")
    dest_raw = input("  > ").strip()

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

    def _ask_slug(prompt_text, col):
        _raw = input(prompt_text).strip().lower().replace(" ", "-")
        if _raw:
            _suggestions = _slug_ac(_raw, col)
            if _suggestions:
                print("  Matching slugs:")
                for _i, _s in enumerate(_suggestions[:8], 1):
                    print(f"    [{_i}] {_s}")
                print("  [Enter] keep: " + _raw)
                _pick = input("  > ").strip()
                if _pick.isdigit() and 1 <= int(_pick) <= len(_suggestions[:8]):
                    _raw = _suggestions[int(_pick) - 1]
        return _raw

    if dest_raw == "1":
        slug = _ask_slug(
            "  Topic slug (e.g. 'baldurs-gate-2', 'nvidia-dlss'): ",
            f"knowledge_{category}",
        )
        return category, slug, f"knowledge_{category}", []

    if dest_raw == "2":
        _ev_slug = _ask_slug(
            "  Topic slug (e.g. 'transformer-architecture', 'dlss') [Enter=none]: ",
            "knowledge_evergreen",
        )
        return category, _ev_slug, "knowledge_evergreen", [category]

    if dest_raw == "3":
        print()
        print("  Select categories (e.g. 1,2 or 1-3):")
        for i, cat in enumerate(RESEARCH_CATEGORIES_DATA, 1):
            marker = " <-" if cat == category else ""
            print(f"    [{i}] {cat}{marker}")
        sel_raw = input("  > ").strip()
        selected_cats = []
        try:
            if "-" in sel_raw and "," not in sel_raw:
                a, b = sel_raw.split("-")
                indices = list(range(int(a)-1, int(b)))
            elif "," in sel_raw:
                indices = [int(x.strip())-1 for x in sel_raw.split(",")]
            else:
                indices = [int(sel_raw)-1]
            selected_cats = [RESEARCH_CATEGORIES_DATA[i] for i in indices
                             if 0 <= i < len(RESEARCH_CATEGORIES_DATA)]
        except Exception:
            pass
        if not selected_cats:
            selected_cats = [category]
        _ev_slug = _ask_slug(
            "  Topic slug (e.g. 'transformer-architecture', 'dlss') [Enter=none]: ",
            "knowledge_evergreen",
        )
        return category, _ev_slug, "knowledge_evergreen", selected_cats

    if dest_raw == "4":
        _ev_slug = _ask_slug(
            "  Topic slug (e.g. 'transformer-architecture', 'dlss') [Enter=none]: ",
            "knowledge_evergreen",
        )
        return category, _ev_slug, "knowledge_evergreen", ["global"]

    # default fallback
    _ev_slug = _ask_slug(
        "  Topic slug (e.g. 'transformer-architecture', 'dlss') [Enter=none]: ",
        "knowledge_evergreen",
    )
    return category, _ev_slug, "knowledge_evergreen", [category]


def _paste_to_knowledge() -> None:
    """Paste clipboard text directly into knowledge collection with topic_slug."""
    from urllib.parse import urlparse as _up
    # Knowledge chunks are larger than research chunks:
    # evergreen concepts need to be self-contained (2000-2500 chars target).
    KNOWLEDGE_CHUNK_TARGET = 2200
    KNOWLEDGE_CHUNK_MIN    = 300

    url = input("  Source URL (Enter to skip): ").strip()
    if not url:
        url = "manual://paste"

    category, slug, collection_dest, extra_categories = _ask_slug_and_category()
    if not slug and collection_dest != "knowledge_evergreen":
        print("  Slug is required for topic-specific knowledge feed.")
        return

    text = _edit_notepad("Paste your article text here, then save and close Notepad.", label="paste_input")
    if not text or len(text) < 100:
        print(f"  Too little text or cancelled.")
        return

    # Garbage check
    from .chunk_utils import _garbage_label
    gc = _garbage_label(text[:500], url)
    if gc:
        print(f"  ⚠ Garbage detected: {gc} -- check content before saving")
        confirm_gc = input("  Continue anyway? [y/N]: ").strip().lower()
        if confirm_gc not in ("y", "yes"):
            return

    print(f"  [OK] {len(text)} chars loaded")

    # Show save targets
    if extra_categories:
        cats_str = ", ".join(extra_categories)
        print(f"  Save to {collection_dest} [{cats_str}]? [Y/n]: ", end="")
    else:
        print(f"  Save to {collection_dest} [slug: {slug}]? [Y/n]: ", end="")
    confirm = input("").strip().lower()
    if confirm == "n":
        return

    text = _clean_markdown(text)

    # Paragraph-aware split:
    # 1. Split on double newline boundaries
    # 2. Accumulate paragraphs until chunk reaches target size
    # 3. Carry last paragraph into next chunk as overlap
    import re as _re
    _paragraphs = [p.strip() for p in _re.split(r'\n{2,}', text) if p.strip()]

    chunks_list = []
    current_parts = []
    current_len   = 0
    overlap_para  = None

    for para in _paragraphs:
        para_len = len(para)

        # Single paragraph longer than target: split it by sentence
        if para_len > KNOWLEDGE_CHUNK_TARGET and not current_parts:
            sentences = _re.split(r'(?<=[.!?])\s+', para)
            sent_buf  = []
            sent_len  = 0
            for sent in sentences:
                if sent_len + len(sent) > KNOWLEDGE_CHUNK_TARGET and sent_buf:
                    chunks_list.append(' '.join(sent_buf))
                    # overlap: last sentence
                    sent_buf = [sent_buf[-1], sent]
                    sent_len = sum(len(s) for s in sent_buf)
                else:
                    sent_buf.append(sent)
                    sent_len += len(sent)
            if sent_buf:
                overlap_para = ' '.join(sent_buf)
                current_parts = [overlap_para]
                current_len   = len(overlap_para)
            continue

        if current_len + para_len > KNOWLEDGE_CHUNK_TARGET and current_parts:
            # Flush current chunk
            chunks_list.append('\n\n'.join(current_parts))
            # Overlap: carry last paragraph into next chunk
            overlap_para  = current_parts[-1]
            current_parts = [overlap_para, para]
            current_len   = len(overlap_para) + para_len
        else:
            current_parts.append(para)
            current_len += para_len

    # Flush remainder
    if current_parts:
        chunks_list.append('\n\n'.join(current_parts))

    chunks_list = [c.strip() for c in chunks_list if len(c.strip()) >= KNOWLEDGE_CHUNK_MIN]

    if url.startswith("manual://"):
        domain = "manual"
        title  = slug.replace("-", " ").title() if slug else "evergreen"
    else:
        domain = _up(url).netloc.removeprefix("www.")
        title  = slug.replace("-", " ").title() if slug else domain

    if collection_dest == "knowledge_evergreen" and extra_categories:
        # Multi-category: upsert once per category (same vectors, Valkey cache hits)
        total_saved = 0
        for cat in extra_categories:
            saved = _embed_and_upsert_chunks(
                chunks_list, url, cat, slug, domain, title, collection_dest
            )
            total_saved += saved
            print(f"  [OK] {saved} chunks -> {collection_dest} [category: {cat}]")
        print(f"  [OK] Total: {total_saved} chunks across {len(extra_categories)} categories")
    else:
        _embed_and_upsert_chunks(chunks_list, url, category, slug, domain, title, collection_dest)


def _sync_persona_from_json(json_path: str = None, collection: str = None) -> None:
    """
    Sync persona chunks from a JSON file into persona_lukasz collection.
    File format:
    [
      {
        "text":      "...",
        "dimension": "criticism_style",
        "trigger":   "when corporate efficiency replaces human craft",
        "intensity": "high",       (optional, default: mid)
        "topic":     "diablo-4",   (optional)
        "slug":      "",           (optional)
        "source":    "conversation" (optional, default: written)
      },
      ...
    ]
    Deduplication: skips chunks whose text_hash already exists in Qdrant.
    """
    import json
    import hashlib
    import uuid
    import re as _re
    import requests as _req

    try:
        from config import QDRANT_URL, OLLAMA_EMBED_URL, EMBED_MODEL
        from qdrant_client import QdrantClient
        from qdrant_client.models import PointStruct, SparseVector
    except Exception as e:
        print(f"  Error: {e}")
        return

    # -- Resolve target persona collection --------------------------------
    if collection:
        COLLECTION = collection
        print(f"  Target collection: {COLLECTION}")
    else:
        _qc_sel      = QdrantClient(url=QDRANT_URL)
        _all_cols    = [c.name for c in _qc_sel.get_collections().collections]
        _pcols       = sorted([c for c in _all_cols if c.startswith("persona_")])
        if not _pcols:
            print("  No persona_* collections found. Create one via Builder > [N].")
            return
        elif len(_pcols) == 1:
            COLLECTION = _pcols[0]
            print(f"  Target collection: {COLLECTION}")
        else:
            print()
            print("  -- SELECT TARGET COLLECTION --")
            for _i, _col in enumerate(_pcols, 1):
                try:
                    _n = _qc_sel.get_collection(_col).points_count
                except Exception:
                    _n = 0
                print(f"  [{_i}] {_col}  ({_n} chunks)")
            print()
            _raw_col = input("  Target [number or name]: ").strip().lower()
            if not _raw_col:
                return
            if _raw_col.isdigit() and 1 <= int(_raw_col) <= len(_pcols):
                COLLECTION = _pcols[int(_raw_col) - 1]
            elif _raw_col in _pcols:
                COLLECTION = _raw_col
            else:
                _candidate = f"persona_{_raw_col}" if not _raw_col.startswith("persona_") else _raw_col
                if _candidate in _pcols:
                    COLLECTION = _candidate
                else:
                    print(f"  '{_raw_col}' not found."); return
    try:
        from config import PERSONA_DIMENSIONS as _PD
        DIMENSIONS = list(_PD.keys())
    except Exception:
        DIMENSIONS = ["argument","critique","skepticism","reference","appreciation","humor","personal"]
    PERSONAS_DIR = os.path.normpath(os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "..", "..", "data", "personas"
    ))
    DEFAULT_FILE = os.path.join(PERSONAS_DIR, "lukasz_chunks.json")

    if not json_path:
        suggested = PERSONAS_DIR
        raw = input(f"  JSON file or directory (Enter = {suggested}): ").strip().strip('"')
        json_path = raw if raw else suggested

    # -- Collect all JSON files to load -----------------------------------
    json_files = []
    if os.path.isdir(json_path):
        all_json = sorted(
            p for p in pathlib.Path(json_path).glob("*.json")
            if p.is_file()
        )
        if not all_json:
            print(f"  No *.json files found in: {json_path}")
            return
        if len(all_json) == 1:
            json_files = all_json
            print(f"  Found 1 JSON file: {all_json[0].name}")
        else:
            print(f"  Found {len(all_json)} JSON file(s) in {json_path}:")
            for _i, jf in enumerate(all_json, 1):
                print(f"    [{_i}] {jf.name}")
            print(f"    [A] All files")
            print()
            _pick = input("  Select file(s) [number or A]: ").strip().lower()
            if _pick == "a":
                json_files = all_json
            elif _pick.isdigit() and 1 <= int(_pick) <= len(all_json):
                json_files = [all_json[int(_pick) - 1]]
            else:
                print("  Invalid selection."); return
    elif os.path.isfile(json_path):
        json_files = [pathlib.Path(json_path)]
    else:
        print(f"  Path not found: {json_path}")
        return

    # -- Load and merge all files ------------------------------------------
    chunks = []
    for jf in json_files:
        try:
            with open(jf, encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                chunks.extend(data)
                print(f"  + {len(data):>3} chunks from {jf.name}")
            else:
                print(f"  SKIP {jf.name} -- not a JSON array")
        except Exception as e:
            print(f"  SKIP {jf.name} -- parse error: {e}")

    if not chunks:
        print("  No valid chunks loaded.")
        return

    print(f"  Total loaded: {len(chunks)} chunks")

    # Load existing hashes from Qdrant for dedup
    client = QdrantClient(url=QDRANT_URL)
    existing = [c.name for c in client.get_collections().collections]
    if COLLECTION not in existing:
        print(f"  Collection '{COLLECTION}' not found. Create it via Builder > [N].")
        return

    existing_hashes = set()
    try:
        offset = None
        while True:
            result = client.scroll(
                collection_name=COLLECTION, limit=500,
                offset=offset, with_payload=True,
            )
            for point in result[0]:
                h = point.payload.get("text_hash", "")
                if h:
                    existing_hashes.add(h)
            offset = result[1]
            if offset is None:
                break
    except Exception as e:
        print(f"  Warning: could not read existing hashes: {e}")

    print(f"  Existing chunks in {COLLECTION}: {len(existing_hashes)}")

    def _embed(text):
        try:
            r = _req.post(
                f"{OLLAMA_EMBED_URL}/api/embed",
                json={"model": EMBED_MODEL, "input": text},
                timeout=120,
            )
            data = r.json()
            emb = data.get("embeddings")
            if emb and len(emb) > 0:
                return emb[0]
            return data.get("embedding")
        except Exception as e:
            print(f"  [embed] Error: {e}")
            return None

    def _sparse(text):
        tokens = _re.findall(r"[a-z0-9]+", text.lower())
        tf = {}
        for t in tokens:
            if len(t) > 2:
                tf[t] = tf.get(t, 0) + 1
        total = max(len(tokens), 1)
        idx_map = {}
        for t, count in tf.items():
            idx = abs(hash(t)) % (2 ** 20)
            idx_map[idx] = idx_map.get(idx, 0.0) + round(count / total, 6)
        return list(idx_map.keys()), [round(v, 6) for v in idx_map.values()]

    inserted = 0
    skipped  = 0
    errors   = 0

    for i, chunk in enumerate(chunks):
        text = chunk.get("text", "").strip()
        if not text or len(text) < 100:
            print(f"  [{i+1}] SKIP -- too short")
            skipped += 1
            continue

        text_hash = hashlib.md5(text.encode()).hexdigest()
        if text_hash in existing_hashes:
            print(f"  [{i+1}] SKIP -- already indexed: {text[:50]}...")
            skipped += 1
            continue

        dimension = chunk.get("dimension", "").strip()
        if dimension not in DIMENSIONS:
            print(f"  [{i+1}] SKIP -- invalid dimension '{dimension}'")
            skipped += 1
            continue

        trigger = chunk.get("trigger", "").strip()
        if not trigger.startswith("when"):
            print(f"  [{i+1}] SKIP -- trigger must start with 'when': {trigger}")
            skipped += 1
            continue

        intensity = chunk.get("intensity", "mid").strip()
        if intensity not in ("low", "mid", "high"):
            intensity = "mid"

        print(f"  [{i+1}/{len(chunks)}] Embedding: {text[:60]}...")

        text_vec = _embed(text)
        if not text_vec:
            print(f"  [{i+1}] ERROR -- text embed failed")
            errors += 1
            continue

        trigger_vec = _embed(trigger)
        if not trigger_vec:
            trigger_vec = text_vec

        sp_indices, sp_values = _sparse(text)
        point_id = str(uuid.uuid4())

        payload = {
            "text":       text,
            "text_hash":  text_hash,
            "dimension":  dimension,
            "trigger":    trigger,
            "intensity":  intensity,
            "topic":      chunk.get("topic", ""),
            "slug":       chunk.get("slug", ""),
            "source":     chunk.get("source", "written"),
        }

        try:
            client.upsert(
                collection_name = COLLECTION,
                points = [
                    PointStruct(
                        id      = point_id,
                        vector  = {
                            "dense":         text_vec,
                            "trigger_dense": trigger_vec,
                            "sparse":        SparseVector(
                                indices = sp_indices,
                                values  = sp_values,
                            ),
                        },
                        payload = payload,
                    )
                ],
            )
            existing_hashes.add(text_hash)
            inserted += 1
            print(f"  [{i+1}] OK -- {dimension} / {trigger[:40]}")
        except Exception as e:
            print(f"  [{i+1}] ERROR -- upsert failed: {e}")
            errors += 1

    print()
    print(f"  Sync complete: {inserted} inserted, {skipped} skipped, {errors} errors")
    print(f"  Total in {COLLECTION}: {len(existing_hashes)}")


def _export_persona_to_json(output_path: str = None, collection: str = None) -> None:
    """
    Export all chunks from persona_lukasz Qdrant collection to a JSON file.
    Output format matches the import schema used by _sync_persona_from_json().
    """
    import json as _json
    import requests as _req

    try:
        from config import QDRANT_URL, PERSONA_COLLECTION
        from qdrant_client import QdrantClient
    except Exception as e:
        print(f"  Error: {e}")
        return

    PERSONAS_DIR = os.path.normpath(os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "..", "..", "data", "personas"
    ))
    DEFAULT_OUT = os.path.join(PERSONAS_DIR, "lukasz_chunks_export.json")

    if not output_path:
        suggested = DEFAULT_OUT
        raw = input(f"  Output file (Enter = {suggested}): ").strip().strip('"')
        output_path = raw if raw else suggested

    try:
        client = QdrantClient(url=QDRANT_URL)
        chunks = []
        offset = None
        while True:
            result = client.scroll(
                collection_name = PERSONA_COLLECTION,
                limit           = 500,
                offset          = offset,
                with_payload    = True,
                with_vectors    = False,
            )
            for point in result[0]:
                p = point.payload
                chunks.append({
                    "text":      p.get("text", ""),
                    "dimension": p.get("dimension", ""),
                    "trigger":   p.get("trigger", ""),
                    "intensity": p.get("intensity", "mid"),
                    "topic":     p.get("topic", ""),
                    "slug":      p.get("slug", ""),
                    "source":    p.get("source", "written"),
                })
            offset = result[1]
            if offset is None:
                break

        if not chunks:
            print(f"  No chunks found in {PERSONA_COLLECTION}.")
            return

        # Sort by dimension then trigger for readable output
        chunks.sort(key=lambda c: (c["dimension"], c["trigger"]))

        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            _json.dump(chunks, f, ensure_ascii=False, indent=2)

        print(f"  Exported {len(chunks)} chunks to {output_path}")

    except Exception as e:
        print(f"  Export error: {e}")


def _persona_dimension_stats(collection: str = None) -> None:
    """Fetch and print per-dimension chunk counts from persona_lukasz."""
    try:
        from config import QDRANT_URL, PERSONA_COLLECTION, PERSONA_DIMENSIONS
        dimensions = list(PERSONA_DIMENSIONS.keys())
        if not collection:
            collection = PERSONA_COLLECTION
    except ImportError:
        dimensions = ["argument","critique","skepticism","reference","appreciation","humor","personal"]
    try:
        from config import QDRANT_URL, PERSONA_COLLECTION
    except ImportError:
        print("  [stats] config not available.")
        return

    try:
        import requests as _req
        counts = {}
        total  = 0
        for dim in dimensions:
            url  = f"{QDRANT_URL}/collections/{collection}/points/count"
            body = {
                "filter": {"must": [{"key": "dimension", "match": {"value": dim}}]},
                "exact": True,
            }
            r = _req.post(url, json=body, timeout=4)
            r.raise_for_status()
            n = r.json().get("result", {}).get("count", 0)
            counts[dim] = n
            total += n

        LOW = 2
        filled = sum(1 for n in counts.values() if n > 0)

        print()
        print(f"  -- {collection} stats ------------------------------")
        for dim, n in counts.items():
            bar  = "#" * n
            note = "   <- needs more" if 0 < n < LOW else ("   <- empty" if n == 0 else "")
            print(f"  {dim:<24} {n:>2}  {bar}{note}")
        print("  " + "-" * 47)
        print(f"  Total: {total} chunks / {filled}/{len(dimensions)} dimensions populated")
        print()

    except Exception as e:
        print(f"  [stats] Qdrant unavailable: {e}")
        print()


def _feed_persona_to_knowledge() -> None:
    """Feed persona chunks from JSON file into persona_lukasz."""
    _persona_dimension_stats()
    _sync_persona_from_json()


# -- Legacy stubs (agent.py imports these by name) -----------------------------

def _paste_text_to_qdrant() -> None:
    """Legacy stub -- redirects to Feed menu."""
    print("  Use Feed menu [2] -> Paste.")


def _feed_persona_menu() -> None:
    """Legacy stub -- redirects to Feed menu."""
    print("  Use Feed menu [P] -> Feed persona.")


# -- Feed menu -----------------------------------------------------------------

def _feed_menu() -> None:
    _chunker_available = False
    try:
        from knowledge.knowledge_chunker import (
            slug_autocomplete, tag_autocomplete, prompt_slug_and_tags,
        )
        _chunker_available = True
    except ImportError:
        pass

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
                from knowledge.knowledge_chunker import prompt_slug_and_tags
                slug, tags = prompt_slug_and_tags(category)
            else:
                slug, tags = "", []
            clip_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "clip.py")
            if not os.path.exists(clip_path):
                print(f"  clip.py not found: {clip_path}")
                continue
            cmd_args = [sys.executable, clip_path, "--url", url, "--category", category]
            if tags:
                cmd_args += ["--tag", tags[0]]
            elif slug:
                cmd_args += ["--tag", slug]
            subprocess.call(cmd_args)
        elif raw == "2":
            _paste_to_knowledge()
        elif raw == "p":
            # -- Select persona collection before showing submenu ----------
            try:
                from qdrant_client import QdrantClient as _QC
                from config import QDRANT_URL as _QU
                _pc_all = [c.name for c in _QC(url=_QU).get_collections().collections]
                _pc_list = sorted([c for c in _pc_all if c.startswith("persona_")])
            except Exception:
                _pc_list = []
            if not _pc_list:
                print("  No persona_* collections found. Create one via Builder > [N].")
                continue
            _active_col = None
            if len(_pc_list) == 1:
                _active_col = _pc_list[0]
            else:
                print()
                print("  -- SELECT PERSONA --")
                for _pi, _pc in enumerate(_pc_list, 1):
                    try:
                        _pn = _QC(url=_QU).get_collection(_pc).points_count
                    except Exception:
                        _pn = 0
                    print(f"  [{_pi}] {_pc}  ({_pn} chunks)")
                print("  [Enter] Cancel")
                print()
                _praw = input("  Persona: ").strip().lower()
                if not _praw:
                    continue
                if _praw.isdigit() and 1 <= int(_praw) <= len(_pc_list):
                    _active_col = _pc_list[int(_praw) - 1]
                elif _praw in _pc_list:
                    _active_col = _praw
                elif not _praw.startswith("persona_") and f"persona_{_praw}" in _pc_list:
                    _active_col = f"persona_{_praw}"
                else:
                    print("  Invalid."); continue
            _persona_dimension_stats(collection=_active_col)
            print("  +==========================================+")
            print(f"  |  PERSONA: {_active_col.replace('persona_',''):<30}|")
            print("  +==========================================+")
            print("  |  [1]  Sync from JSON / directory        |")
            print("  |  [2]  Export to JSON                    |")
            print("  |  [3]  Persona Builder (AI-assisted)     |")
            print("  |  [Enter]  Back                          |")
            print("  +==========================================+")
            print()
            pcmd = input("  Choice: ").strip()
            if pcmd == "1":
                _sync_persona_from_json(collection=_active_col)
            elif pcmd == "2":
                _export_persona_to_json(collection=_active_col)
            elif pcmd == "3":
                _persona_builder_menu(collection=_active_col)
        elif raw == "3":
            path = input("  Path to .json file or directory: ").strip().strip('"')
            if not path:
                continue
            feed_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "manual_feed.py")
            if not os.path.exists(feed_path):
                print(f"  manual_feed.py not found: {feed_path}")
                continue
            if os.path.isdir(path):
                subprocess.call([sys.executable, feed_path, "--dir", path])
            else:
                subprocess.call([sys.executable, feed_path, "--file", path])
        else:
            print("  Unknown option.")
