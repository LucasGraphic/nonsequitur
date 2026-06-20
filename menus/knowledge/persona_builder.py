# menus/knowledge/persona_builder.py
# Persona Builder -- AI-assisted persona chunk creation via Ollama.
#
# Two modes:
#   [1] Paste    -- user pastes text (any language), AI proposes all chunk fields
#   [2] Converse -- AI asks targeted questions, user answers, AI proposes chunk
#
# Draft file: data/personas/lukasz_chunks_draft.json
# Sync to Qdrant: Feed > [P] > [1] Sync from JSON -> point to draft file
#
# Uses Ollama (local LLM) -- model selectable from available list.

import os, sys, json, textwrap, re, hashlib, math

_DIMS_FALLBACK = {
    "argument":     {"tooltip": "Questioning the dominant framing -- both sides have part of the truth",         "example_trigger": "when a transition is mistaken for a replacement"},
    "critique":     {"tooltip": "Exposing the mechanism by which a system fails or protects itself",             "example_trigger": "when institutional incentives corrupt the product"},
    "skepticism":   {"tooltip": "Claim exceeds evidence -- hype, benchmarks, demos vs reality",                  "example_trigger": "when product demos substitute for product reality"},
    "reference":    {"tooltip": "An older work or pattern describes exactly what is happening now",               "example_trigger": "when the warning was ignored because it arrived as fiction"},
    "appreciation": {"tooltip": "Something overlooked deserves recognition -- niche wins, underdogs",            "example_trigger": "when a small studio punches above its weight"},
    "humor":        {"tooltip": "Comic register is the right analytical tool -- wit, irony, absurdism",          "example_trigger": "when the critique requires the thing being critiqued"},
    "personal":     {"tooltip": "Speaking from direct experience, not from analytical distance",                  "example_trigger": "when a purchase is actually a thesis"},
}

def _get_dims():
    try:
        from config import PERSONA_DIMENSIONS
        if isinstance(PERSONA_DIMENSIONS, dict): return PERSONA_DIMENSIONS
    except Exception: pass
    return _DIMS_FALLBACK

def _get_config():
    try:
        from config import QDRANT_URL, EMBED_DIM, OLLAMA_URL
        col = "persona_lukasz"
        try:
            from config import PERSONA_COLLECTION; col = PERSONA_COLLECTION
        except Exception: pass
        return QDRANT_URL, EMBED_DIM, OLLAMA_URL, col
    except Exception as e:
        print(f"  ERROR loading config: {e}"); return None, None, None, None

def _draft_path(collection="persona_lukasz"):
    base = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "data", "personas"))
    os.makedirs(base, exist_ok=True)
    # persona_lukasz -> lukasz_chunks_draft.json
    # persona_schopenhauer -> schopenhauer_chunks_draft.json
    name = collection.replace("persona_", "", 1) if collection.startswith("persona_") else collection
    return os.path.join(base, f"{name}_chunks_draft.json")

# --- Qdrant -----------------------------------------------------------------

def _ensure_collection(qdrant_url, embed_dim, collection):
    try:
        from qdrant_client import QdrantClient
        from qdrant_client.models import VectorParams, Distance, SparseVectorParams, SparseIndexParams
        client = QdrantClient(url=qdrant_url)
        existing = [c.name for c in client.get_collections().collections]
        if collection not in existing:
            client.create_collection(
                collection_name=collection,
                vectors_config={"dense": VectorParams(size=embed_dim, distance=Distance.COSINE),
                                "trigger_dense": VectorParams(size=embed_dim, distance=Distance.COSINE)},
                sparse_vectors_config={"sparse": SparseVectorParams(index=SparseIndexParams(on_disk=False))},
            )
            print(f"  Created collection: {collection}")
        return True
    except Exception as e:
        print(f"  ERROR creating collection: {e}"); return False

def _fetch_dim_counts(qdrant_url, collection, dims):
    try:
        import requests
        counts = {}
        for dim in dims:
            r = requests.post(f"{qdrant_url}/collections/{collection}/points/count",
                json={"filter": {"must": [{"key": "dimension", "match": {"value": dim}}]}, "exact": True}, timeout=5)
            counts[dim] = r.json().get("result", {}).get("count", 0)
        return counts
    except Exception: return {d: 0 for d in dims}

def _cosine(a, b):
    if not a or not b or len(a) != len(b): return 0.0
    dot = sum(x*y for x,y in zip(a,b))
    na = math.sqrt(sum(x*x for x in a)); nb = math.sqrt(sum(x*x for x in b))
    return dot/(na*nb) if na and nb else 0.0

def _fetch_existing(qdrant_url, collection):
    try:
        from qdrant_client import QdrantClient
        client = QdrantClient(url=qdrant_url)
        hashes = set(); vectors = []; offset = None
        while True:
            result = client.scroll(collection_name=collection, limit=200, offset=offset,
                                   with_payload=True, with_vectors=["dense"])
            for pt in result[0]:
                h = pt.payload.get("text_hash","")
                if h: hashes.add(h)
                v = (pt.vectors or {}).get("dense")
                if v: vectors.append(v)
            offset = result[1]
            if offset is None: break
        return hashes, vectors
    except Exception: return set(), []

# --- Ollama -----------------------------------------------------------------

def _fetch_models(ollama_url):
    try:
        import urllib.request, json as _j
        with urllib.request.urlopen(ollama_url.rstrip("/")+"/api/tags", timeout=5) as r:
            data = _j.loads(r.read())
        skip = ("embed","embedding","rerank")
        return [m["name"] for m in data.get("models",[]) if not any(s in m["name"].lower() for s in skip)]
    except Exception: return []

def _ollama_call(prompt, model, ollama_url, num_predict=1200):
    try:
        import urllib.request, json as _j
        payload = _j.dumps({"model": model, "prompt": prompt, "stream": False,
                             "options": {"num_predict": num_predict, "temperature": 0.3}, "think": False}).encode()
        req = urllib.request.Request(ollama_url.rstrip("/")+"/api/generate", data=payload,
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=180) as r:
            return _j.loads(r.read()).get("response","").strip()
    except Exception as e:
        print(f"  Ollama error: {e}"); return None

def _embed_text(text, embed_url, embed_model):
    try:
        import urllib.request, json as _j
        payload = _j.dumps({"model": embed_model, "input": text}).encode()
        req = urllib.request.Request(embed_url.rstrip("/")+"/api/embed", data=payload,
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=30) as r:
            emb = _j.loads(r.read()).get("embeddings",[])
        return emb[0] if emb else None
    except Exception: return None

def _pick_model(models, ollama_url):
    if not models: print("  No models available."); return None
    print()
    print("  Available models:")
    for i, m in enumerate(models, 1): print(f"    [{i}] {m}")
    default_idx = next((i for i,m in enumerate(models) if "27b" in m), 0)
    raw = input(f"  Select [Enter = {models[default_idx]}]: ").strip()
    if not raw: return models[default_idx]
    if raw.isdigit() and 1<=int(raw)<=len(models): return models[int(raw)-1]
    if raw in models: return raw
    return models[default_idx]

# --- Parse & display --------------------------------------------------------

def _parse(response):
    result = {}
    for field in ("dimension","trigger","intensity","topic"):
        m = re.search(rf"(?im)^{field}\s*:\s*(.+)$", response)
        if m: result[field] = m.group(1).strip()
    m = re.search(r"(?im)^TEXT\s*:\s*(.+)", response, re.DOTALL)
    if m: result["text"] = m.group(1).strip()
    if not all(k in result for k in ("dimension","trigger","text")): return None
    result["intensity"] = result.get("intensity","mid").lower()
    if result["intensity"] not in ("low","mid","high"): result["intensity"] = "mid"
    result["topic"] = result.get("topic","").lower().replace(" ","-").strip("-")
    result["slug"] = ""; result["source"] = "conversation"
    t = result["trigger"]
    if not t.lower().startswith("when"): result["trigger"] = "when "+t.lstrip()
    return result

def _wrap(text, width=80, indent="  "):
    out = []
    for para in text.split("\n"):
        if not para.strip(): out.append(""); continue
        for line in textwrap.wrap(para.strip(), width-len(indent)): out.append(indent+line)
    return "\n".join(out)

def _show_proposal(chunk):
    print()
    print("  +------------------------------------------------------------+")
    print("  | PROPOSED CHUNK                                             |")
    print("  +------------------------------------------------------------+")
    print(f"  dimension : {chunk.get('dimension','?')}")
    print(f"  trigger   : {chunk.get('trigger','?')}")
    print(f"  intensity : {chunk.get('intensity','?')}")
    print(f"  topic     : {chunk.get('topic','') or '(none)'}")
    print()
    print(_wrap(chunk.get("text",""), width=78))
    print()

def _show_stats(dim_counts, dims, draft, collection):
    total = sum(dim_counts.values())
    print()
    print(f"  -- {collection} --")
    for d in dims:
        n = dim_counts.get(d,0); bar = "#"*min(n,30)
        tag = "  <- EMPTY" if n==0 else ("  <- low" if n<4 else "")
        print(f"  {d:<14} {n:>3}  {bar}{tag}")
    print(f"  {'':14}      total: {total}")
    if draft: print(f"  draft: {len(draft)} unsync'd")
    print()

# --- Dedup ------------------------------------------------------------------

def _check_dedup(chunk, existing_hashes, existing_vectors, embed_url, embed_model, threshold=0.92):
    text = chunk.get("text","")
    h = hashlib.md5(text.encode()).hexdigest()
    if h in existing_hashes: return True, "exact duplicate (hash match)"
    if existing_vectors and embed_url and embed_model:
        vec = _embed_text(text, embed_url, embed_model)
        if vec:
            sims = [_cosine(vec, ev) for ev in existing_vectors]
            max_sim = max(sims) if sims else 0.0
            if max_sim >= threshold: return True, f"semantic duplicate (similarity {max_sim:.3f})"
    return False, ""

# --- Prompts ----------------------------------------------------------------

def _dim_list_text(dims, dim_counts):
    return "\n".join(f'{d} ({dim_counts.get(d,0)}): {m.get("tooltip","")}. Example: "{m.get("example_trigger","")}"'
                     for d, m in dims.items())

def _make_paste_prompt(dims, dim_counts, input_text):
    return f"""You are a persona chunk extractor for a writing voice system.

Given text (any language), produce:
1. Condensed English paragraph of 200-400 words capturing the core voice and argument.
   First person if source is first person. Preserve specific examples. No generalisations.
   If Polish: translate and condense. Preserve directness.
2. Best DIMENSION from the list below.
3. TRIGGER starting with "when" activating this pattern.
4. INTENSITY: low/mid/high.
5. TOPIC: kebab-case slug (empty if general).

Dimensions:
{_dim_list_text(dims, dim_counts)}

Respond in this exact format only:
DIMENSION: <name>
TRIGGER: when <phrase>
INTENSITY: low|mid|high
TOPIC: <slug or empty>
TEXT: <condensed paragraph>

Input text:
{input_text}"""

def _make_question_prompt(target_dim, target_meta, count):
    return f"""You are building a persona chunk collection for an author's writing voice.
Target dimension: {target_dim}
Description: {target_meta.get("tooltip","")}
Example trigger: {target_meta.get("example_trigger","")}
Current chunks: {count}

Ask the author ONE focused question in English for this dimension.
Specific, concrete. Reference: tech, games, photography, AI, media criticism, Norway, career.
Invite personal anecdote, specific example, or strong opinion. 2-3 sentences max.

Respond with only the question."""

def _make_extract_prompt(target_dim, target_meta, dims, dim_counts, answer):
    return f"""You are a persona chunk extractor. Author answered a targeted question.
Target dimension: {target_dim} ({target_meta.get("tooltip","")})

Extract a persona chunk. Condense to 200-400 words in English. First person. Preserve details.

Dimensions:
{_dim_list_text(dims, dim_counts)}

Respond in this exact format only:
DIMENSION: <name>
TRIGGER: when <phrase>
INTENSITY: low|mid|high
TOPIC: <slug or empty>
TEXT: <condensed paragraph>

Author's answer:
{answer}"""

# --- Negotiation ------------------------------------------------------------

def _negotiate(chunk, dims, model, ollama_url, existing_hashes, existing_vectors, embed_url, embed_model, dim_counts):
    valid = list(dims.keys())
    while True:
        _show_proposal(chunk)
        print("  [T]ext  [D]imension  [R]trigger  [I]ntensity  [O]topic")
        print("  [S]ave    [A]nother proposal    [X] Discard")
        cmd = input("  > ").strip().lower()
        if cmd == "s":
            if chunk.get("dimension") not in valid: print(f"  ERROR: invalid dimension."); continue
            if not chunk.get("trigger","").lower().startswith("when"): print("  ERROR: trigger needs 'when'"); continue
            if len(chunk.get("text","")) < 100: print("  ERROR: text too short"); continue
            is_dup, reason = _check_dedup(chunk, existing_hashes, existing_vectors, embed_url, embed_model)
            if is_dup:
                print(f"  WARNING: {reason}")
                if input("  Save anyway? [y/N]: ").strip().lower() not in ("y","yes"): continue
            return chunk
        elif cmd == "x": return None
        elif cmd == "a": return "regenerate"
        elif cmd == "t":
            import tempfile, subprocess
            _current_text = chunk.get("text", "")
            try:
                with tempfile.NamedTemporaryFile(
                    mode="w", suffix=".txt", encoding="utf-8",
                    delete=False, prefix="persona_chunk_"
                ) as _tmp:
                    _tmp.write(_current_text)
                    _tmp_path = _tmp.name
                subprocess.run(["notepad.exe", _tmp_path], check=False)
                with open(_tmp_path, encoding="utf-8") as _f:
                    _edited = _f.read().strip()
                os.unlink(_tmp_path)
                if len(_edited) >= 100:
                    chunk["text"] = _edited
                    print("  Text updated.")
                elif _edited:
                    print(f"  Too short ({len(_edited)} chars), not saved.")
                else:
                    print("  Empty, not saved.")
            except Exception as _e:
                print(f"  Notepad failed ({_e}), fallback to inline edit:")
                print("  New text (two empty lines to finish):")
                _lines, _blanks = [], 0
                while _blanks < 2:
                    _ln = input()
                    if _ln == "": _blanks += 1
                    else: _blanks = 0
                    _lines.append(_ln)
                _text = "\n".join(_lines).rstrip()
                if _text: chunk["text"] = _text
        elif cmd == "d":
            print()
            for i, d in enumerate(valid, 1):
                print(f"  [{i}] {d:<14} ({dim_counts.get(d,0)})  {dims[d].get('tooltip','')}")
            raw = input("  Number or name: ").strip()
            if raw.isdigit() and 1<=int(raw)<=len(valid): chunk["dimension"]=valid[int(raw)-1]
            elif raw in valid: chunk["dimension"]=raw
            else: print("  Invalid.")
        elif cmd == "r":
            raw = input("  New trigger: ").strip()
            if raw:
                if not raw.lower().startswith("when"): raw = "when "+raw.lstrip()
                chunk["trigger"] = raw
        elif cmd == "i":
            raw = input("  Intensity [low/mid/high]: ").strip().lower()
            if raw in ("low","mid","high"): chunk["intensity"]=raw
            else: print("  Invalid.")
        elif cmd == "o":
            chunk["topic"] = input("  Topic slug: ").strip().lower().replace(" ","-")
        else: print("  Unknown command.")

# --- Modes ------------------------------------------------------------------

def _read_multiline(prompt=""):
    if prompt: print(prompt)
    lines, blanks = [], 0
    try:
        while blanks < 2:
            ln = input()
            if ln == "": blanks += 1
            else: blanks = 0
            lines.append(ln)
    except EOFError: pass
    return "\n".join(lines).strip()

def _mode_paste(dims, model, ollama_url, existing_hashes, existing_vectors, embed_url, embed_model, dim_counts):
    print("\n  -- PASTE MODE -- (any language, two empty lines to finish)\n")
    raw_text = _read_multiline()
    if len(raw_text) < 50: print("  Too short, cancelled."); return None
    print(f"\n  {len(raw_text)} chars. Generating with {model}...")
    history = []
    while True:
        prompt = _make_paste_prompt(dims, dim_counts, raw_text)
        if history: prompt += "\n\n" + "\n\n".join(history)
        response = _ollama_call(prompt, model, ollama_url)
        if not response: print("  Generation failed."); return None
        chunk = _parse(response)
        if not chunk:
            print("  Parse error:"); print(response[:400])
            if input("  Retry? [Y/n]: ").strip().lower() == "n": return None
            continue
        result = _negotiate(chunk, dims, model, ollama_url, existing_hashes, existing_vectors, embed_url, embed_model, dim_counts)
        if result == "regenerate":
            note = input("  What to change? (Enter=as-is): ").strip()
            if note: history.append(f"Revise: {note}")
            continue
        return result

def _mode_converse(dims, model, ollama_url, existing_hashes, existing_vectors, embed_url, embed_model, dim_counts):
    print("\n  -- CONVERSATION MODE --")
    sorted_dims = sorted(dim_counts.items(), key=lambda x: (x[1], x[0]))
    target_dim = sorted_dims[0][0]; target_meta = dims.get(target_dim, {})
    print(f"\n  Weakest: {target_dim} ({dim_counts.get(target_dim,0)} chunks) -- {target_meta.get('tooltip','')}")
    raw = input("  Override dimension (Enter to accept): ").strip().lower()
    if raw in dims: target_dim = raw; target_meta = dims[target_dim]
    print("\n  Generating question...")
    question = _ollama_call(_make_question_prompt(target_dim, target_meta, dim_counts.get(target_dim,0)), model, ollama_url, num_predict=300)
    if not question: print("  Generation failed."); return None
    print(f"\n  {'='*64}")
    print(_wrap(question, width=70))
    print(f"  {'='*64}\n")
    answer = _read_multiline("  Your answer (two empty lines to finish):")
    if len(answer) < 30: print("  Too short, cancelled."); return None
    print("\n  Extracting chunk...")
    history = []
    while True:
        prompt = _make_extract_prompt(target_dim, target_meta, dims, dim_counts, answer)
        if history: prompt += "\n\n" + "\n\n".join(history)
        response = _ollama_call(prompt, model, ollama_url)
        if not response: print("  Generation failed."); return None
        chunk = _parse(response)
        if not chunk:
            print("  Parse error:"); print(response[:400])
            if input("  Retry? [Y/n]: ").strip().lower() == "n": return None
            continue
        result = _negotiate(chunk, dims, model, ollama_url, existing_hashes, existing_vectors, embed_url, embed_model, dim_counts)
        if result == "regenerate":
            note = input("  What to change? (Enter=as-is): ").strip()
            if note: history.append(f"Revise: {note}")
            continue
        return result

# --- Draft ------------------------------------------------------------------

def _load_draft(path):
    if not os.path.exists(path): return []
    try:
        with open(path, encoding="utf-8") as f: return json.load(f)
    except Exception: return []

def _save_draft(path, chunks):
    try:
        with open(path, "w", encoding="utf-8") as f: json.dump(chunks, f, ensure_ascii=False, indent=2)
        return True
    except Exception as e:
        print(f"  ERROR: {e}"); return False

# --- Create persona ---------------------------------------------------------

def _create_persona_flow(qdrant_url, embed_dim):
    print("\n  -- CREATE PERSONA --")
    print("  Name -> collection: persona_<name>  (lowercase, underscores only)")
    raw = input("  Persona name (Enter to cancel): ").strip().lower().replace(" ","_")
    if not raw: return None
    raw = re.sub(r"[^a-z0-9_]","",raw)
    if not raw: print("  Invalid name."); return None
    col = f"persona_{raw}"
    if _ensure_collection(qdrant_url, embed_dim, col):
        print(f"  OK: {col} created.")
        print(f"  To activate: set PERSONA_COLLECTION = \"{col}\" in config.py")
        return col
    return None

# --- Entry point ------------------------------------------------------------


def _select_collection(qdrant_url, embed_dim):
    """
    List all persona_* collections from Qdrant and let user pick one.
    Option [N] creates a new collection.
    Returns selected collection name or None to abort.
    """
    try:
        from qdrant_client import QdrantClient
        client = QdrantClient(url=qdrant_url)
        all_cols = [c.name for c in client.get_collections().collections]
        persona_cols = sorted([c for c in all_cols if c.startswith("persona_")])
    except Exception as e:
        print(f"  ERROR connecting to Qdrant: {e}")
        return None

    print()
    print("  -- SELECT PERSONA COLLECTION --")
    if not persona_cols:
        print("  No persona_* collections found.")
    else:
        for i, col in enumerate(persona_cols, 1):
            try:
                n = client.get_collection(col).points_count
            except Exception:
                n = 0
            print(f"  [{i}] {col}  ({n} chunks)")
    print("  [N] Create new")
    print("  [Enter] Cancel")
    print()

    raw = input("  Choice: ").strip().lower()

    if raw == "":
        return None

    if raw == "n":
        return _create_persona_flow(qdrant_url, embed_dim)

    if raw.isdigit():
        idx = int(raw) - 1
        if 0 <= idx < len(persona_cols):
            return persona_cols[idx]

    # Accept direct name
    candidate = raw if raw.startswith("persona_") else f"persona_{raw}"
    if candidate in persona_cols:
        return candidate

    print("  Invalid selection.")
    return None


def _sync_draft_to_qdrant(draft_path, collection, qdrant_url, embed_url, embed_model):
    """
    Embed and upsert draft chunks directly to Qdrant.
    Self-contained -- no dependency on feed.py.
    """
    import json, hashlib, uuid, re as _re
    try:
        from qdrant_client import QdrantClient
        from qdrant_client.models import PointStruct, SparseVector
    except Exception as e:
        print(f"  ERROR: qdrant_client not available: {e}"); return 0

    if not os.path.exists(draft_path):
        print("  Draft file not found."); return 0

    with open(draft_path, encoding="utf-8") as f:
        chunks = json.load(f)

    if not chunks:
        print("  Draft is empty."); return 0

    client = QdrantClient(url=qdrant_url)

    # Load existing hashes for dedup
    existing_hashes = set()
    try:
        offset = None
        while True:
            result = client.scroll(collection_name=collection, limit=500,
                                   offset=offset, with_payload=True, with_vectors=False)
            for pt in result[0]:
                h = pt.payload.get("text_hash", "")
                if h: existing_hashes.add(h)
            offset = result[1]
            if offset is None: break
    except Exception:
        pass

    def _sparse(text):
        tokens = _re.findall(r"[a-z0-9]+", text.lower())
        tf = {}
        for t in tokens:
            if len(t) > 2: tf[t] = tf.get(t, 0) + 1
        total = max(len(tokens), 1)
        idx_map = {}
        for t, count in tf.items():
            idx = abs(hash(t)) % (2 ** 20)
            idx_map[idx] = idx_map.get(idx, 0.0) + round(count / total, 6)
        return list(idx_map.keys()), [round(v, 6) for v in idx_map.values()]

    inserted = 0; skipped = 0; errors = 0

    for i, chunk in enumerate(chunks):
        text    = chunk.get("text", "").strip()
        trigger = chunk.get("trigger", "").strip()
        dim     = chunk.get("dimension", "unknown")

        if not text or len(text) < 100:
            print(f"  [{i+1}] SKIP -- too short"); skipped += 1; continue

        text_hash = hashlib.md5(text.encode()).hexdigest()
        if text_hash in existing_hashes:
            print(f"  [{i+1}] SKIP -- duplicate"); skipped += 1; continue

        dense_vec   = _embed_text(text,    embed_url, embed_model)
        trigger_vec = _embed_text(trigger, embed_url, embed_model) if trigger else None

        if not dense_vec:
            print(f"  [{i+1}] ERROR -- embed failed"); errors += 1; continue

        sp_idx, sp_val = _sparse(text)
        vectors = {"dense": dense_vec, "sparse": SparseVector(indices=sp_idx, values=sp_val)}
        if trigger_vec:
            vectors["trigger_dense"] = trigger_vec

        payload = {
            "text":                 text,
            "dimension":            dim,
            "trigger":              trigger,
            "intensity":            chunk.get("intensity", "mid"),
            "topic":                chunk.get("topic", ""),
            "source":               chunk.get("source", "conversation"),
            "text_hash":            text_hash,
            "trigger_dense_vector": trigger_vec,
            "persona":              collection.replace("persona_", ""),
        }

        try:
            client.upsert(collection_name=collection,
                          points=[PointStruct(id=str(uuid.uuid4()), vector=vectors, payload=payload)])
            existing_hashes.add(text_hash)
            inserted += 1
            print(f"  [{i+1}] OK -- {dim} / {trigger[:45]}")
        except Exception as e:
            print(f"  [{i+1}] ERROR -- {e}"); errors += 1

    print(f"  Done: {inserted} inserted, {skipped} skipped, {errors} errors")
    return inserted

def _persona_builder_menu(collection: str = None):
    dims = _get_dims()
    qdrant_url, embed_dim, ollama_url, _default_col = _get_config()
    if qdrant_url is None: return

    try:
        from config import OLLAMA_EMBED_URL, EMBED_MODEL
        embed_url = OLLAMA_EMBED_URL; embed_model = EMBED_MODEL
    except Exception:
        embed_url = None; embed_model = None

    # Use pre-selected collection or ask user
    if not collection:
        collection = _select_collection(qdrant_url, embed_dim)
        if not collection:
            return

    _ensure_collection(qdrant_url, embed_dim, collection)

    draft_path = _draft_path(collection)
    draft = _load_draft(draft_path)

    models = _fetch_models(ollama_url)
    model = _pick_model(models, ollama_url)
    if not model: print("  No model selected."); return

    print(f"\n  Model: {model}  |  Collection: {collection}")

    while True:
        dim_counts = _fetch_dim_counts(qdrant_url, collection, list(dims.keys()))
        existing_hashes, existing_vectors = _fetch_existing(qdrant_url, collection)

        _show_stats(dim_counts, dims, draft, collection)

        print("  +------------------------------------------+")
        print("  | PERSONA BUILDER                          |")
        print("  +------------------------------------------+")
        print(f"  | model: {model[:32]:<32} |")
        print("  +------------------------------------------+")
        print("  | [1] Paste    -- analyze pasted text     |")
        print("  | [2] Converse -- AI asks, you answer     |")
        print("  | [S] Sync draft -> Qdrant                |")
        print("  | [N] New persona collection              |")
        print("  | [M] Change model                        |")
        print("  | [V] View draft                          |")
        print("  | [C] Clear draft                         |")
        print("  | [Enter] Back                            |")
        print("  +------------------------------------------+")
        print()

        cmd = input("  Choice: ").strip().lower()
        if cmd in ("","q"): break

        elif cmd == "1":
            result = _mode_paste(dims, model, ollama_url, existing_hashes, existing_vectors, embed_url, embed_model, dim_counts)
            if result and isinstance(result, dict):
                draft.append(result); _save_draft(draft_path, draft)
                print(f"  Saved to draft. Total: {len(draft)}")
            else: print("  Discarded.")

        elif cmd == "2":
            result = _mode_converse(dims, model, ollama_url, existing_hashes, existing_vectors, embed_url, embed_model, dim_counts)
            if result and isinstance(result, dict):
                draft.append(result); _save_draft(draft_path, draft)
                print(f"  Saved to draft. Total: {len(draft)}")
            else: print("  Discarded.")

        elif cmd == "s":
            if not draft:
                print("  Draft is empty."); continue
            print(f"  Syncing {len(draft)} chunk(s) to {collection}...")
            _n = _sync_draft_to_qdrant(draft_path, collection, qdrant_url, embed_url, embed_model)
            if _n > 0:
                draft = []; _save_draft(draft_path, draft)
                print(f"  Draft cleared.")

        elif cmd == "n":
            _create_persona_flow(qdrant_url, embed_dim)

        elif cmd == "m":
            new_model = _pick_model(models, ollama_url)
            if new_model: model = new_model; print(f"  Model: {model}")

        elif cmd == "v":
            if not draft: print("  Draft is empty."); continue
            print()
            for i, c in enumerate(draft, 1):
                print(f"  [{i}] {c.get('dimension','?'):<14} {c.get('trigger','')[:55]}")
            raw = input("\n  View number (Enter to skip): ").strip()
            if raw.isdigit():
                idx = int(raw)-1
                if 0<=idx<len(draft): _show_proposal(draft[idx]); input("  Enter to continue...")

        elif cmd == "c":
            if not draft: print("  Already empty."); continue
            if input(f"  Clear {len(draft)} chunks? [y/N]: ").strip().lower() in ("y","yes"):
                draft = []; _save_draft(draft_path, draft); print("  Cleared.")

        else: print("  Unknown command.")
