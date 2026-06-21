# menus/queue.py  --  Queue management, item inspection, RAG sources view


import os
import json

import core.queue as queue

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# ---------------------------------------------------------------------------
# Private helpers shared by [g] and [r] commands
# ---------------------------------------------------------------------------

def _clear_focus(item: dict) -> None:
    """Clear article_focus on item in queue and in local dict."""
    import core.queue as _q
    q = _q.load()
    for qi in q["items"]:
        if qi["id"] == item["id"]:
            qi["article_focus"] = ""
            item["article_focus"] = ""
            break
    _q.save(q)
    print("  [OK] Focus cleared.")


def _run_generate(item: dict) -> None:
    """Run generate pipeline for item and refresh local dict."""
    import core.queue as _q
    from pipeline.generate_run import run_generate
    run_generate(item_ids=[item["id"]], api_provider="")
    refreshed = [i for i in _q.get_all() if i["id"] == item["id"]]
    if refreshed:
        item.update(refreshed[0])


def _reset_to_researched(item: dict) -> None:
    """Reset item status to researched and clear error/done_at."""
    import core.queue as _q
    q = _q.load()
    for qi in q["items"]:
        if qi["id"] == item["id"]:
            qi["status"]  = _q.STATUS_RESEARCHED
            qi["done_at"] = None
            qi["error"]   = None
            item.update(qi)
            break
    _q.save(q)
    print("  [OK] Reset to researched.")





def _apply_schema(item: dict, schema_name: str) -> None:
    import os as _os, json as _json
    schemas_dir = _os.path.join(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))), "data", "schemas")
    length_override = ""
    schema_path = _os.path.join(schemas_dir, f"{schema_name}.json")
    if _os.path.exists(schema_path):
        try:
            with open(schema_path, encoding="utf-8") as f:
                data = _json.load(f)
                length_override = data.get("default_length", "")
        except Exception:
            pass
    q = queue.load()
    for qi in q["items"]:
        if qi["id"] == item["id"]:
            qi["article_type"] = schema_name
            item["article_type"] = schema_name
            if length_override:
                qi["article_length"] = length_override
                item["article_length"] = length_override
            break
    queue.save(q)
    msg = f"  [OK] Schema: {schema_name}"
    if length_override:
        msg += f"  (length: {length_override})"
    print(msg)


def _save_focus(item: dict, focus: str) -> None:
    q = queue.load()
    for qi in q["items"]:
        if qi["id"] == item["id"]:
            qi["article_focus"] = focus
            item["article_focus"] = focus
            break
    queue.save(q)
    print("  [OK] Focus set.")


def _set_schema_interactive(item: dict) -> None:
    import os as _os, json as _json
    schemas_dir = _os.path.join(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))), "data", "schemas")
    schemas = {}
    if _os.path.exists(schemas_dir):
        for f in sorted(_os.listdir(schemas_dir)):
            if f.endswith(".json") and f != "default.json":
                try:
                    with open(_os.path.join(schemas_dir, f), encoding="utf-8") as fh:
                        data = _json.load(fh)
                        schemas[f[:-5]] = data.get("label", f[:-5])
                except Exception:
                    pass
    schema_list = list(schemas.items())
    print()
    for i, (k, v) in enumerate(schema_list, 1):
        print(f"  [{i:>2}] {k:<22}  --  {v}")
    print("  [ 0] No schema (default)")
    raw = input("  Schema (Enter = 0): ").strip()
    if raw.isdigit() and 1 <= int(raw) <= len(schema_list):
        _apply_schema(item, schema_list[int(raw) - 1][0])
    else:
        print("  Schema: default (unchanged)")


def _schema_suggester(item: dict) -> str:
    import requests as _req, json as _json, os as _os
    from rank_bm25 import BM25Okapi
    from config import OLLAMA_URL, QDRANT_URL, research_collection
    item_id  = item["id"]
    category = item.get("category", "other")
    topic    = item.get("topic", "")
    col      = research_collection(category)

    # -- Scroll ALL research chunks for this item (paginated) ----------
    all_points = []
    next_offset = None
    while True:
        body = {
            "filter":       {"must": [{"key": "item_id", "match": {"value": item_id}}]},
            "limit":        100,
            "with_payload": True,
            "with_vector":  False,
        }
        if next_offset is not None:
            body["offset"] = next_offset
        try:
            r = _req.post(
                f"{QDRANT_URL}/collections/{col}/points/scroll",
                json=body,
                timeout=15,
            )
            result      = r.json().get("result", {})
            points      = result.get("points", [])
            next_offset = result.get("next_page_offset")
        except Exception as e:
            print(f"  [schema] Qdrant error: {e}")
            return ""
        all_points.extend(points)
        if not next_offset:
            break

    if not all_points:
        print("  [schema] No research chunks found")
        return ""

    print(f"  [schema] {len(all_points)} chunks -> BM25 pre-rank")

    # -- BM25 pre-rank against topic -----------------------------------
    texts = [p["payload"].get("text", "") for p in all_points]
    tokenized_corpus = [t.lower().split() for t in texts]
    bm25 = BM25Okapi(tokenized_corpus)
    query_tokens = topic.lower().split()
    scores = bm25.get_scores(query_tokens)

    ranked = sorted(zip(scores, all_points), key=lambda x: x[0], reverse=True)
    top_points = [p for _, p in ranked[:12]]

    # -- Build context from best chunks --------------------------------
    chunks = []
    for p in top_points:
        text = p["payload"].get("text", "")[:500]
        url  = p["payload"].get("url", "")[:40]
        if text:
            chunks.append(f"[{url}] {text}")

    context = "\n\n".join(chunks)

    # -- Load available schemas ----------------------------------------
    schemas_dir = _os.path.join(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))), "data", "schemas")
    schema_labels = {}
    if _os.path.exists(schemas_dir):
        for f in sorted(_os.listdir(schemas_dir)):
            if f.endswith(".json") and f != "default.json":
                try:
                    with open(_os.path.join(schemas_dir, f), encoding="utf-8") as fh:
                        data = _json.load(fh)
                        schema_labels[f[:-5]] = data.get("label", f[:-5])
                except Exception:
                    pass

    schema_list_str = "\n".join(f"- {k}: {v}" for k, v in schema_labels.items())
    category = item.get("category", "other")

    prompt = (
        f"You are an editorial assistant. Based on the research chunks and topic, "
        f"select the BEST matching article schema.\n\n"
        f"Topic: {topic}\nCategory: {category}\n\n"
        f"Available schemas:\n{schema_list_str}\n\n"
        f"Research sample (BM25 top chunks):\n{context[:2500]}\n\n"
        f"Decision rules (apply in order, stop at first match):\n"
        f"1. Research contains hands-on gameplay, player impressions, bug reports, or early access build coverage -> games_early_access\n"
        f"2. Product is released and research contains scored reviews or verdict language -> review schema\n"
        f"3. Research is primarily benchmarks, specs, performance numbers -> hardware schema\n"
        f"4. Research is AI model release, paper, or capability announcement -> ai_news or ai_technical\n"
        f"5. Research has very limited confirmed facts, mostly trailer/reveal coverage -> announcement schema\n"
        f"6. Research supports a deep analytical argument about design, industry, or culture -> analysis schema\n"
        f"7. Default: pick the schema whose sections best match what the research actually contains\n\n"
        f"Output ONLY the schema name, exactly one line, nothing else."
    )

    try:
        r = _req.post(
            f"{OLLAMA_URL}/api/chat",
            json={
                "model":    "qwen3.5:35b-a3b",
                "think":    True,
                "messages": [{"role": "user", "content": prompt}],
                "stream":   False,
                "options":  {"temperature": 0.1, "num_predict": 200},
            },
            timeout=90,
        )
        r.raise_for_status()
        response = r.json().get("message", {}).get("content", "").strip()
        if "</think>" in response:
            response = response.split("</think>")[-1].strip()
        suggested = response.strip().lower().strip(".-_").strip()
        if suggested in schema_labels:
            return suggested
        for k in schema_labels:
            if k in suggested or suggested in k:
                return k
    except Exception as e:
        print(f"  [schema] LLM error: {e}")
    return ""

def _focus_picker(item: dict) -> str:
    import requests as _req
    from config import OLLAMA_URL, QDRANT_URL, research_collection
    item_id  = item["id"]
    category = item.get("category", "other")
    topic    = item.get("topic", "")
    col      = research_collection(category)
    existing = item.get("article_focus", "")
    try:
        r = _req.post(
            f"{QDRANT_URL}/collections/{col}/points/scroll",
            json={
                "filter":       {"must": [{"key": "item_id", "match": {"value": item_id}}]},
                "limit":        15,
                "with_payload": True,
                "with_vector":  False,
            },
            timeout=15,
        )
        points = r.json().get("result", {}).get("points", [])
    except Exception:
        points = []
    sep = "\n\n"
    context = ""
    if points:
        chunks = [p["payload"].get("text", "")[:400] for p in points[:12] if p["payload"].get("text")]
        context = sep.join(chunks)
    suggestions = []
    if context:
        prompt = (
            f"You are an editorial assistant for a personal tech/gaming blog.\n"
            f"Based on the research below, suggest exactly 10 editorial focus angles.\n\n"
            f"Topic: {topic}\n\n"
            f"Rules:\n"
            f"- Each angle: one sentence, 50-100 chars, states a clear argument (X proves Y because Z)\n"
            f"- Mix positive, critical, and contrarian angles\n"
            f"- Be specific -- reference actual details from the research\n"
            f"- No clickbait, no questions, no overview of\n"
            f"- Output exactly 10 lines, no numbers, no bullets\n\n"
            f"Research:\n{context[:2500]}"
        )
        try:
            r = _req.post(
                f"{OLLAMA_URL}/api/chat",
                json={
                    "model":    "qwen3.5:35b-a3b",
                    "think":    True,
                    "messages": [{"role": "user", "content": prompt}],
                    "stream":   False,
                    "options":  {"temperature": 0.8, "num_predict": 1200},
                },
                timeout=120,
            )
            r.raise_for_status()
            response = r.json().get("message", {}).get("content", "").strip()
            if "</think>" in response:
                response = response.split("</think>")[-1].strip()
            for line in response.split("\n"):
                line = line.strip().strip("--*123456789.)").strip()
                if line and len(line) > 20:
                    suggestions.append(line[:120])
                if len(suggestions) == 10:
                    break
        except Exception as e:
            print(f"  [focus] LLM error: {e}")
    print()
    print("  +== FOCUS PICKER ===========================================+")
    print(f"  |  Topic: {topic[:55]:<55} |")
    print("  +============================================================+")
    offset = 0
    if existing:
        print(f"  [ 0] KEEP: {existing[:72]}")
        offset = 1
    if suggestions:
        for i, s in enumerate(suggestions, 1):
            print(f"  [{i:>2}] {s[:75]}")
    else:
        print("  (No suggestions -- enter manually)")
    print()
    max_n = len(suggestions) + offset
    print(f"  [0-{max_n}] Pick  |  [c] Custom  |  [s] Skip")
    print()
    while True:
        raw = input("  > ").strip().lower()
        if raw == "s":
            return ""
        if raw == "c":
            custom = input("  Custom focus: ").strip()
            if custom:
                print(f"  Focus: {custom[:100]}")
                return custom
            continue
        if raw == "0" and existing:
            return existing
        if raw.isdigit():
            idx = int(raw)
            real_idx = idx - offset
            if existing and idx == 0:
                return existing
            if 1 <= real_idx <= len(suggestions):
                chosen = suggestions[real_idx - 1]
                print(f"  Focus: {chosen[:100]}")
                return chosen
        print("  Invalid. Enter number, [c] custom, or [s] skip.")


def _fmt_list(items: list, show_hints: bool = True) -> None:
    if not items:
        print("  Queue is empty.")
        return
    print()
    print(f"  {'#':<4} {'ID':<10} {'S':<2} {'STATUS':<12} {'SCORE':<9} {'MODEL':<14} {'CAT':<10} {'SCHEMA/LEN':<20} TOPIC")
    print(f"  {'-'*130}")
    for idx, item in enumerate(items, 1):
        icon    = queue.STATUS_ICON.get(item["status"], "?")
        status  = item["status"]
        _m = item.get("model", "")
        model   = _m.split(":")[-1][:13] if ":" in _m else _m[:13]
        cat     = item.get("category", "")[:9]
        topic   = item["topic"][:52]
        focus   = " [F]" if item.get("article_focus") else ""

        # Scoring display
        # Scoring display -- read from queue cache fields
        _sv = item.get("scoring_verdict", "")
        _ss = item.get("scoring_score")
        _VERDICT_SHORT = {
            "excellent": "EXCE", "strong": "STRO", "pass": "PASS",
            "weak": "WEAK", "fail": "FAIL", "unknown": "???",
        }
        _sv_label = _VERDICT_SHORT.get(_sv.lower(), _sv[:4].upper()) if _sv else ""
        if _ss is not None and _sv:
            score_col = f"{_ss}/{_sv_label}"
        elif _sv:
            score_col = f"?/{_sv_label}"
        else:
            score_col = "-"

        # Schema + length
        _atype = item.get("article_type", "")
        _alen  = item.get("article_length", "medium")
        if _atype:
            schema_col = f"{_atype[:16]}/{_alen[0]}"
        else:
            schema_col = f"-/{_alen[0]}"

        # Status hint
        if status == queue.STATUS_PENDING:
            hint = " -> run"
        elif status == queue.STATUS_RESEARCHED:
            hint = " [g]enerate"
        elif status == queue.STATUS_ERROR:
            hint = " [r]eset"
        else:
            hint = ""

        print(f"  {idx:<4} [{item['id']}]  {icon}  {status:<12} {score_col:<9} {model:<14} [{cat:<9}]  {schema_col:<20} {topic}{focus}{hint}")
    print()
    if show_hints:
        print("  Commands: number to inspect | g <n> generate | w <n> rewrite | sc <n> score | r <n> redo | x <n> remove")
        print()

def _inspect_edit_item(item: dict) -> None:
    EDITABLE = {
        "t": ("topic",         "Topic"),
        "m": ("model",         "Model"),
        "p": ("persona",       "Persona"),
        "f": ("article_focus", "Article focus"),
        "c": ("category",      "Category"),
        "n": ("notes",         "Notes"),
        "l": ("topic_slug",    "Slug"),
    }

    while True:
        print()
        print(f"  +-- [{item['id']}] ----------------------------------------------")
        print(f"  |  Topic    : {item['topic']}")
        print(f"  |  Status   : {item['status']}")
        print(f"  |  Section  : {item.get('section', '')}  |  Category: {item.get('category', '')}  |  Persona: {item.get('persona', 'lukasz')}")
        print(f"  |  Model    : {item.get('model', '')}")
        _focus_full = item.get('article_focus', '') or '(none)'
        _focus_w = 90
        if len(_focus_full) <= _focus_w:
            print(f"  |  Focus    : {_focus_full}")
        else:
            print(f"  |  Focus    : {_focus_full[:_focus_w]}")
            _focus_rest = _focus_full[_focus_w:]
            while _focus_rest:
                print(f"  |             {_focus_rest[:_focus_w]}")
                _focus_rest = _focus_rest[_focus_w:]
        print(f"  |  Length   : {item.get('article_length', 'medium')}")
        print(f"  |  Schema   : {item.get('article_type', '') or '(none -- default schema)'}")
        print(f"  |  Lang     : {item.get('article_lang', 'en')}")
        _tr_flag = item.get('translate', False)
        _tr_lang = item.get('article_lang', 'pl')
        print(f"  |  Translate: {'yes (article_' + _tr_lang + '.md)' if _tr_flag else 'no'}")
        print(f"  |  Slug     : {item.get('topic_slug', '') or '(none)'}")
        print(f"  |  Notes    : {item.get('notes', '') or '(none)'}")
        seed_urls = item.get("seed_urls", [])
        if seed_urls:
            print(f"  |  Seed URLs: {len(seed_urls)}")
            for u in seed_urls:
                print(f"  |    * {u[:120]}")
        else:
            print(f"  |  Seed URLs: (none)")
        seed_q = item.get("seed_queries", [])
        if seed_q:
            print(f"  |  Seed Q   : {len(seed_q)}")
            for sq in seed_q[:3]:
                print(f"  |    * {sq[:70]}")
        print(f"  |  Added    : {item.get('added_at', '')[:19]}")
        if item.get("researched_at"):
            print(f"  |  Researched: {item['researched_at'][:19]}")
        if item.get("error"):
            print(f"  |  Error    : {item['error'][:80]}")
        print(f"  ----------------------------------------------------------------")
        print()
        status = item.get("status", "")
        can_generate = status in (queue.STATUS_RESEARCHED, queue.STATUS_DONE)
        gen_hint = "  [g] generate now" if can_generate else ""
        print(f"  Edit: [t] topic  [m] model  [p] persona  [f] focus  [c] category  [n] notes  [l] slug  [z] length  [a] schema  [y] lang  [tr] translate")
        print(f"        [u] seed URLs (+add / clear)  [r] redo  [x] remove  [s] RAG sources  [sc] score  [v] view{gen_hint}  [Enter] back")
        print()

        cmd = input("  > ").strip().lower()

        if cmd in ("", "q", "back"):
            break

        elif cmd == "m":
            from discovery.selector import _fetch_ollama_models
            from config import OLLAMA_URL, MODELS
            installed = _fetch_ollama_models(OLLAMA_URL)
            current   = item.get("model", "")
            print(f"  Current model: {current or '(empty)'}")
            print()
            config_models = list(MODELS.items())
            for idx, (k, m) in enumerate(config_models, 1):
                is_inst = any(
                    inst == m["name"] or inst.startswith(m["name"].split(":")[0])
                    for inst in installed
                )
                status = "" if is_inst else "  [not installed]"
                marker = " <-" if m["name"] == current else ""
                print(f"  [{idx}] {m['label']}{marker}{status}")
            extra = [
                m for m in installed
                if not any(m == cm["name"] or m.startswith(cm["name"].split(":")[0])
                           for _, cm in config_models)
            ]
            extra_start = len(config_models) + 1
            for idx, name in enumerate(extra, extra_start):
                marker = " <-" if name == current else ""
                print(f"  [{idx}] {name}{marker}")
            print(f"  [0] Keep current")
            raw_m = input("  Number: ").strip()
            if raw_m == "0" or raw_m == "":
                pass
            elif raw_m.isdigit():
                nr = int(raw_m)
                if 1 <= nr <= len(config_models):
                    new_val = config_models[nr-1][1]["name"]
                elif extra and extra_start <= nr < extra_start + len(extra):
                    new_val = extra[nr - extra_start]
                else:
                    print("  Invalid number.")
                    continue
                q = queue.load()
                for qi in q["items"]:
                    if qi["id"] == item["id"]:
                        qi["model"] = new_val
                        item["model"] = new_val
                        break
                queue.save(q)
                print(f"  [OK] Model updated: {new_val}")

        elif cmd == "p":
            try:
                from config import QDRANT_URL as _QU
                from qdrant_client import QdrantClient as _QC
                _all = [c.name for c in _QC(url=_QU).get_collections().collections]
                persona_list = [(c, c.replace("persona_","")) for c in sorted(_all) if c.startswith("persona_")]
            except Exception:
                from config import PERSONAS
                persona_list = list(PERSONAS.items())
            current = item.get("persona", "lukasz")
            print(f"  Current persona: {current}")
            print()
            for idx, (col, name) in enumerate(persona_list, 1):
                marker = " <-" if name == current else ""
                print(f"  [{idx}] {col:<36}{marker}")
            print(f"  [0] Keep current")
            raw_p = input("  Number: ").strip()
            if raw_p == "0" or raw_p == "":
                pass
            elif raw_p.isdigit() and 1 <= int(raw_p) <= len(persona_list):
                col, name = persona_list[int(raw_p) - 1]
                new_val = name  # store short name (lukasz, schopenhauer)
                q = queue.load()
                for qi in q["items"]:
                    if qi["id"] == item["id"]:
                        qi["persona"] = new_val
                        item["persona"] = new_val
                        break
                queue.save(q)
                print(f"  [OK] Persona updated: {new_val}")
            else:
                print("  Invalid number.")

        elif cmd in EDITABLE and cmd not in ("m", "p"):
            field, label = EDITABLE[cmd]
            current  = item.get(field, "")
            print(f"  Current {label}: {current or '(empty)'}")
            if field == "topic_slug":
                print("  Tip: type partial slug + Enter to see autocomplete suggestions")
            new_val = input(f"  New {label} (Enter = keep): ").strip()
            if new_val:
                if field == "topic_slug":
                    import re as _re
                    # Autocomplete: show existing slugs if input matches
                    _hint = new_val.lower().strip()
                    if len(_hint) >= 2:
                        _slugs = set()
                        try:
                            import json as _j, os as _o
                            _sp = _o.path.join(_o.path.dirname(_o.path.dirname(_o.path.abspath(__file__))), 'data', 'slugs.json')
                            if _o.path.exists(_sp):
                                with open(_sp, encoding='utf-8') as _sf:
                                    for _cat_slugs in _j.load(_sf).values():
                                        for _s in _cat_slugs:
                                            if _hint in _s:
                                                _slugs.add(_s)
                        except Exception:
                            pass
                        try:
                            from qdrant_client import QdrantClient as _QC
                            from config import QDRANT_URL as _QU
                            _qc = _QC(url=_QU)
                            _all = [c.name for c in _qc.get_collections().collections]
                            for _col in [c for c in _all if c.startswith('knowledge_') or c.startswith('research_')]:
                                try:
                                    _pts = _qc.scroll(_col, limit=500, with_payload=['topic_slug'], with_vectors=False)[0]
                                    for _p in _pts:
                                        _s = _p.payload.get('topic_slug', '')
                                        if _s and _hint in _s:
                                            _slugs.add(_s)
                                except Exception:
                                    pass
                        except Exception:
                            pass
                        if _slugs:
                            _slug_list = sorted(_slugs)[:9]
                            print(f"  Slugs matching '{_hint}':")
                            for _si, _sl in enumerate(_slug_list, 1):
                                print(f"    [{_si}] {_sl}")
                            _pick = input("  Select [1-9] or Enter to keep typed: ").strip()
                            if _pick.isdigit() and 1 <= int(_pick) <= len(_slug_list):
                                new_val = _slug_list[int(_pick) - 1]
                            elif _pick:
                                new_val = _pick
                    new_val = _re.sub(r"[^a-z0-9-]", "-", new_val.lower().strip())
                    new_val = _re.sub(r"-+", "-", new_val).strip("-")
                    print(f"  [OK] Slug: {new_val}")
                q = queue.load()
                for qi in q["items"]:
                    if qi["id"] == item["id"]:
                        qi[field] = new_val
                        item[field] = new_val
                        break
                queue.save(q)
                print(f"  [OK] {label} updated.")

        elif cmd == "u":
            seed_urls = item.get("seed_urls", [])
            print(f"  Current seed URLs ({len(seed_urls)}):")
            for u in seed_urls:
                print(f"     *  {u[:90]}")
            print(f"  [+] Add URLs  |  [clear] Remove all  |  [Enter] Keep")
            raw = input("  > ").strip()
            if raw.lower() == "clear":
                new_urls = []
            elif raw:
                import re as _re
                added = [u.strip() for u in raw.split(",") if _re.match(r"https?://", u.strip())]
                new_urls = seed_urls + [u for u in added if u not in seed_urls]
                print(f"  Adding {len(added)} URL(s) -> total: {len(new_urls)}")
            else:
                continue
            q = queue.load()
            for qi in q["items"]:
                if qi["id"] == item["id"]:
                    qi["seed_urls"] = new_urls
                    item["seed_urls"] = new_urls
                    break
            queue.save(q)
            print(f"  [OK] Seed URLs updated ({len(new_urls)}).")

        elif cmd == "r":
            current_status = item.get("status", "")
            if current_status == queue.STATUS_PENDING:
                print("  Already pending.")
                continue
            if current_status in (queue.STATUS_RESEARCHED, queue.STATUS_ERROR):
                if current_status == queue.STATUS_ERROR:
                    print(f"  Item failed. Research data may still exist.")
                keep = input("  Keep research, regenerate article only? [Y/n]: ").strip().lower()
                if keep in ("", "y", "yes"):
                    if current_status == queue.STATUS_ERROR:
                        _reset_to_researched(item)
                    _rf = input("  Reset focus? [Y/n]: ").strip().lower()
                    if _rf in ("", "y", "yes"):
                        _clear_focus(item)
                    gen_now = input("  Generate now? [Y/n]: ").strip().lower()
                    if gen_now in ("", "y", "yes"):
                        _run_generate(item)
                    continue
            if current_status == queue.STATUS_DONE:
                print(f"  [1] Reset -> researched (regenerate article, keep research data)")
                print(f"  [2] Reset -> pending (full re-run: research + generate)")
                print(f"  [3] Uber Research (gap analysis + targeted search, then regenerate)")
                raw_r = input("  Choice (Enter = cancel): ").strip()
                if raw_r == "1":
                    _reset_to_researched(item)
                    _rf = input("  Reset focus? [Y/n]: ").strip().lower()
                    if _rf in ("", "y", "yes"):
                        _clear_focus(item)
                    gen_now = input("  Generate now? [Y/n]: ").strip().lower()
                    if gen_now in ("", "y", "yes"):
                        _run_generate(item)
                    continue
                elif raw_r == "3":
                    try:
                        from qdrant_client import QdrantClient as _UbQC
                        from config import (
                            QDRANT_URL as _UbQURL, OLLAMA_URL as _UbOLLAMA,
                            UBER_RESEARCH_MODEL, UBER_GAP_QUESTIONS, UBER_MAX_URLS_PER_Q,
                            research_collection as _UbRC,
                        )
                        from pipeline.uber_research import run_uber_research
                        _ub_client = _UbQC(url=_UbQURL)
                        _ub_col    = _UbRC(item.get("category", "other"))
                        print(f"  Running Uber Research on: {item['topic'][:60]}")
                        _ub_result = run_uber_research(
                            item           = item,
                            col_name       = _ub_col,
                            client         = _ub_client,
                            ollama_url     = _UbOLLAMA,
                            model          = UBER_RESEARCH_MODEL,
                            is_night_run   = False,
                            n_questions    = UBER_GAP_QUESTIONS,
                            max_urls_per_q = UBER_MAX_URLS_PER_Q,
                        )
                        import core.queue as _ubq
                        _ubq.update_field(item["id"], "uber_research", _ub_result)
                        print(f"  [OK] New chunks: {_ub_result.get('new_chunks', 0)}  |  Total: {_ub_result.get('total_after', 0)}")
                        _reset_to_researched(item)
                        _rf = input("  Reset focus? [Y/n]: ").strip().lower()
                        if _rf in ("", "y", "yes"):
                            _clear_focus(item)
                        gen_now = input("  Generate now? [Y/n]: ").strip().lower()
                        if gen_now in ("", "y", "yes"):
                            _run_generate(item)
                    except Exception as _ub_err:
                        print(f"  Uber Research error: {_ub_err}")
                    continue
                elif raw_r == "2":
                    pass  # fall through to full reset below
                else:
                    continue
            confirm = input(f"  Reset [{item['id']}] -> pending (full re-run)? [y/N]: ").strip().lower()
            if confirm in ("y", "yes"):
                # Cleanup research chunks from Qdrant
                try:
                    from qdrant_client import QdrantClient as _QC
                    from qdrant_client.models import Filter as _F, FieldCondition as _FC, MatchValue as _MV
                    from config import QDRANT_URL as _QURL, research_collection as _rc
                    _qc = _QC(url=_QURL)
                    _col = _rc(item.get("category", "other"))
                    _existing = [c.name for c in _qc.get_collections().collections]
                    if _col in _existing:
                        _qc.delete(
                            collection_name=_col,
                            points_selector=_F(must=[_FC(key="item_id", match=_MV(value=item["id"]))]),
                        )
                        print(f"  [OK] Research chunks cleaned.")
                except Exception as _e:
                    print(f"  [!] Chunk cleanup error: {_e}")
                q = queue.load()
                for qi in q["items"]:
                    if qi["id"] == item["id"]:
                        qi["status"]        = queue.STATUS_PENDING
                        qi["researched_at"] = None
                        qi["done_at"]       = None
                        qi["error"]         = None
                        item.update(qi)
                        break
                queue.save(q)
                print(f"  [OK] Reset to pending.")

        elif cmd == "v":
            # Show generation history if available, then article viewer
            _gens = item.get("generations", [])
            if _gens:
                print()
                print(f"  Generation history ({len(_gens)}):")
                _VSHORT = {"excellent": "EXCE", "strong": "STRO", "pass": "PASS",
                           "weak": "WEAK", "fail": "FAIL"}
                print(f"  {'#':<4} {'Date':<12} {'Score':<6} {'Verdict':<8} {'Schema':<22} {'Len':<8} Focus")
                print(f"  {'-'*95}")
                _best_score = max((_g.get('score') or 0) for _g in _gens)
                for _g in _gens:
                    _gn  = _g.get('n', '?')
                    _gd  = (_g.get('date') or '')[:10]
                    _gs  = str(_g.get('score', '?')) if _g.get('score') is not None else '?'
                    _gv  = _VSHORT.get((_g.get('verdict') or '').lower(),
                                       (_g.get('verdict') or '?')[:4].upper())
                    _gsc = (_g.get('schema') or '-')[:21]
                    _gl  = (_g.get('length') or '-')[:7]
                    _gf  = (_g.get('focus') or '-')[:38]
                    _mark = '  <- best' if (_g.get('score') or 0) == _best_score else ''
                    print(f"  {_gn:<4} {_gd:<12} {_gs:<6} {_gv:<8} {_gsc:<22} {_gl:<8} {_gf}{_mark}")
                print()
                print("  [N] open generation N in Notepad  |  [Enter] view latest in terminal")
                _vraw = input("  > ").strip()
                if _vraw.isdigit():
                    _vn  = int(_vraw)
                    _vg  = next((_g for _g in _gens if _g.get('n') == _vn), None)
                    if _vg and _vg.get('dir'):
                        import subprocess as _vsp
                        _vart = os.path.join(ROOT_DIR, 'output', _vg['dir'], 'article.md')
                        if os.path.isfile(_vart):
                            _vsp.Popen(['notepad.exe', _vart])
                        else:
                            print(f"  File not found: {_vart}")
                    else:
                        print(f"  Generation {_vn} not found.")
                    continue  # back to menu without falling into terminal viewer
            # View generated article -- output is a folder per article:
            # output/<date-slug-or-slug>/article.md (+ metadata.json, sources.json)
            import glob as _glob, json as _vjson
            slug = item.get('topic_slug', '')
            qid = item.get('id', '')
            out_dir = os.path.join(ROOT_DIR, 'output')
            fpath = None
            # 1) Match by queue_id inside metadata.json (most reliable)
            if qid and os.path.isdir(out_dir):
                for _d in sorted(os.listdir(out_dir), reverse=True):
                    _meta_path = os.path.join(out_dir, _d, 'metadata.json')
                    if os.path.isfile(_meta_path):
                        try:
                            with open(_meta_path, 'r', encoding='utf-8') as _mf:
                                _meta = _vjson.load(_mf)
                            if _meta.get('queue_id') == qid:
                                _candidate = os.path.join(out_dir, _d, 'article.md')
                                if os.path.isfile(_candidate):
                                    fpath = _candidate
                                    break
                        except Exception:
                            pass
            # 2) Fallback: match folder name by slug
            if not fpath and slug:
                _dir_matches = sorted(_glob.glob(os.path.join(out_dir, f'*{slug}*')), reverse=True)
                for _d in _dir_matches:
                    _candidate = os.path.join(_d, 'article.md')
                    if os.path.isfile(_candidate):
                        fpath = _candidate
                        break
            # 3) Last resort: most recently modified article.md anywhere in output/
            if not fpath and os.path.isdir(out_dir):
                _all_articles = _glob.glob(os.path.join(out_dir, '*', 'article.md'))
                if _all_articles:
                    fpath = max(_all_articles, key=os.path.getmtime)
            if not fpath:
                print('  [v] No output files found.')
            else:
                try:
                    with open(fpath, 'r', encoding='utf-8') as _vf:
                        _vcontent = _vf.read()
                    _vlines = _vcontent.split('\n')
                    _vfolder = os.path.basename(os.path.dirname(fpath))
                    print(f'  [v] {_vfolder}/article.md  ({len(_vlines)} lines)')
                    print('  Press Enter for next page, [q] to stop.')
                    print()
                    _page = 30
                    for _vi in range(0, len(_vlines), _page):
                        for _vl in _vlines[_vi:_vi+_page]:
                            print(f'  {_vl}')
                        if _vi + _page < len(_vlines):
                            _vk = input('  -- [Enter] more / [q] stop -- ').strip().lower()
                            if _vk == 'q':
                                break
                except Exception as _ve:
                    print(f'  [v] Error reading file: {_ve}')

        elif cmd == "sc":
            _score_single(item)
            # Refresh scoring display
            refreshed = [i for i in queue.get_all() if i["id"] == item["id"]]
            if refreshed:
                item.update(refreshed[0])

        elif cmd == "g":
            status = item.get("status", "")
            if status == queue.STATUS_RESEARCHED:
                confirm = input(f"  Generate [{item['id']}] now? [Y/n]: ").strip().lower()
                if confirm in ("", "y", "yes"):
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
                    if not item.get("article_focus"):
                        try:
                            import requests as _greq
                            from config import QDRANT_URL as _GQU, OLLAMA_URL as _GOU, MODELS as _GM
                            from pipeline.focus_picker import run_focus_picker as _rfp
                            _gcol = 'research_' + item.get('category', 'other')
                            _gr = _greq.post(f'{_GQU}/collections/{_gcol}/points/scroll',
                                json={'filter': {'must': [{'key': 'item_id', 'match': {'value': item['id']}}]},
                                      'limit': 50, 'with_payload': True, 'with_vector': False},
                                timeout=15)
                            _gpts = _gr.json().get('result', {}).get('points', [])
                            _gctx = '\n\n'.join(p['payload'].get('text', '')[:500]
                                                  for p in _gpts[:30] if p['payload'].get('text'))
                            _gmodel = item.get('model', _GM.get('NORMAL', {}).get('name', ''))
                            focus = _rfp(topic=item.get('topic', ''),
                                         context_research=_gctx, model=_gmodel, ollama_url=_GOU)
                        except Exception as _ge:
                            print(f'  [picker] Error: {_ge}')
                            focus = ''
                        if focus:
                            _save_focus(item, focus)
                    _run_generate(item)
            elif status == queue.STATUS_DONE:
                ans = input("  Item is done. Regenerate? [Y/n]: ").strip().lower()
                if ans not in ("n", "no"):
                    _reset_to_researched(item)
                    if not item.get("article_focus"):
                        try:
                            import requests as _greq
                            from config import QDRANT_URL as _GQU, OLLAMA_URL as _GOU, MODELS as _GM
                            from pipeline.focus_picker import run_focus_picker as _rfp
                            _gcol = 'research_' + item.get('category', 'other')
                            _gr = _greq.post(f'{_GQU}/collections/{_gcol}/points/scroll',
                                json={'filter': {'must': [{'key': 'item_id', 'match': {'value': item['id']}}]},
                                      'limit': 50, 'with_payload': True, 'with_vector': False},
                                timeout=15)
                            _gpts = _gr.json().get('result', {}).get('points', [])
                            _gctx = '\n\n'.join(p['payload'].get('text', '')[:500]
                                                  for p in _gpts[:30] if p['payload'].get('text'))
                            _gmodel = item.get('model', _GM.get('NORMAL', {}).get('name', ''))
                            focus = _rfp(topic=item.get('topic', ''),
                                         context_research=_gctx, model=_gmodel, ollama_url=_GOU)
                        except Exception as _ge:
                            print(f'  [picker] Error: {_ge}')
                            focus = ''
                        if focus:
                            _save_focus(item, focus)
                    _run_generate(item)
            else:
                print(f"  Cannot generate  --  status is '{status}'. Need 'researched'.")
        elif cmd == "z":
            _lengths = ["short", "medium", "long"]
            current  = item.get("article_length", "medium")
            _desc    = {"short": "1500-2500 chars", "medium": "3500-5000 chars", "long": "6000-9000 chars"}
            print(f"  Current length: {current}")
            print()
            for idx, l in enumerate(_lengths, 1):
                marker = " <-" if l == current else ""
                print(f"  [{idx}] {l:<8}  --  {_desc[l]}{marker}")
            print(f"  [0] Keep current")
            raw_z = input("  Number: ").strip()
            if raw_z == "0" or raw_z == "":
                pass
            elif raw_z.isdigit() and 1 <= int(raw_z) <= len(_lengths):
                new_val = _lengths[int(raw_z) - 1]
                q = queue.load()
                for qi in q["items"]:
                    if qi["id"] == item["id"]:
                        qi["article_length"] = new_val
                        item["article_length"] = new_val
                        break
                queue.save(q)
                print(f"  [OK] Length updated: {new_val}")
            else:
                print("  Invalid number.")


        elif cmd == "a":
            import json as _qj, os as _qo
            _qschemas_dir = _qo.path.join(_qo.path.dirname(_qo.path.dirname(_qo.path.abspath(__file__))), "data", "schemas")
            _QSCHEMAS = {}
            if _qo.path.exists(_qschemas_dir):
                for _qf in sorted(_qo.listdir(_qschemas_dir)):
                    if _qf.endswith(".json"):
                        try:
                            with open(_qo.path.join(_qschemas_dir, _qf), encoding="utf-8") as _qfh:
                                _QSCHEMAS[_qf[:-5]] = _qj.load(_qfh)
                        except Exception:
                            pass
            schema_list = [(k, v) for k, v in _QSCHEMAS.items() if k != "default"]
            schema_list += [("default", _QSCHEMAS["default"])] if "default" in _QSCHEMAS else []
            current = item.get("article_type", "")
            print(f"  Current schema: {current or '(none -- default)'}")
            print()
            for idx, (key, schema) in enumerate(schema_list, 1):
                marker = " <-" if key == current else ""
                print(f"  [{idx}] {key:<22}  --  {schema.get('label', '')}{marker}")
            print(f"  [0] Clear (use default schema)")
            raw_a = input("  Number (Enter = keep): ").strip()
            if raw_a == "0":
                new_val = ""
                q = queue.load()
                for qi in q["items"]:
                    if qi["id"] == item["id"]:
                        qi["article_type"] = ""
                        item["article_type"] = ""
                        break
                queue.save(q)
                print(f"  [OK] Schema cleared -- default schema will be used")
            elif raw_a == "" :
                pass
            elif raw_a.isdigit() and 1 <= int(raw_a) <= len(schema_list):
                new_val = schema_list[int(raw_a) - 1][0]
                q = queue.load()
                for qi in q["items"]:
                    if qi["id"] == item["id"]:
                        qi["article_type"] = new_val
                        item["article_type"] = new_val
                        break
                queue.save(q)
                print(f"  [OK] Schema updated: {new_val}")
            else:
                print("  Invalid number.")

        elif cmd == "y":
            _langs = ["en", "pl", "no"]
            _lang_label = {"en": "English (no translation)", "pl": "Polish", "no": "Norwegian"}
            current = item.get("article_lang", "en")
            print(f"  Current language: {current}  --  {_lang_label.get(current, current)}")
            print()
            for idx, l in enumerate(_langs, 1):
                marker = " <-" if l == current else ""
                print(f"  [{idx}] {l:<4}  --  {_lang_label[l]}{marker}")
            print(f"  [0] Keep current")
            raw_y = input("  Number: ").strip()
            if raw_y == "0" or raw_y == "":
                pass
            elif raw_y.isdigit() and 1 <= int(raw_y) <= len(_langs):
                new_val = _langs[int(raw_y) - 1]
                q = queue.load()
                for qi in q["items"]:
                    if qi["id"] == item["id"]:
                        qi["article_lang"] = new_val
                        item["article_lang"] = new_val
                        break
                queue.save(q)
                print(f"  [OK] Language updated: {new_val}")
            else:
                print("  Invalid number.")


        elif cmd == "tr":
            current_tr = item.get("translate", False)
            current_lang = item.get("article_lang", "en")
            if current_tr:
                # Toggle off
                q = queue.load()
                for qi in q["items"]:
                    if qi["id"] == item["id"]:
                        qi["translate"] = False
                        item["translate"] = False
                        break
                queue.save(q)
                print(f"  [OK] Translation disabled")
            else:
                # Toggle on  --  pick language
                _langs = ["pl", "no"]
                _lang_label = {"pl": "Polish", "no": "Norwegian"}
                print(f"  Translate to:")
                for idx, l in enumerate(_langs, 1):
                    marker = " <-" if l == current_lang else ""
                    print(f"  [{idx}] {l:<4}  --  {_lang_label[l]}{marker}")
                raw_tr = input("  Number (Enter = pl): ").strip()
                lang = _langs[int(raw_tr)-1] if raw_tr.isdigit() and 1 <= int(raw_tr) <= len(_langs) else "pl"
                q = queue.load()
                for qi in q["items"]:
                    if qi["id"] == item["id"]:
                        qi["translate"] = True
                        qi["article_lang"] = lang
                        item["translate"] = True
                        item["article_lang"] = lang
                        break
                queue.save(q)
                print(f"  [OK] Translation enabled: article_{lang}.md will be saved")

        elif cmd == "x":
            confirm = input(f"  Remove [{item['id']}] {item['topic'][:50]}? [y/N]: ").strip().lower()
            if confirm in ("y", "yes"):
                # Cleanup research chunks from Qdrant
                try:
                    from qdrant_client import QdrantClient as _QC
                    from qdrant_client.models import Filter as _F, FieldCondition as _FC, MatchValue as _MV
                    from config import QDRANT_URL as _QURL, research_collection as _rc
                    _qc = _QC(url=_QURL)
                    _col = _rc(item.get("category", "other"))
                    _existing = [c.name for c in _qc.get_collections().collections]
                    if _col in _existing:
                        _qc.delete(
                            collection_name=_col,
                            points_selector=_F(must=[_FC(key="item_id", match=_MV(value=item["id"]))]),
                        )
                        print(f"  [OK] Research chunks cleaned.")
                except Exception as _e:
                    print(f"  [!] Chunk cleanup error: {_e}")
                queue.remove_item(item["id"])
                print(f"  [OK] Removed.")
                break

            elif action == "sc":
                # Score existing article
                _score_single(item)

        elif cmd == "s":
            _show_rag_sources(item)

        else:
            print("  Unknown command.")


def _show_rag_sources(item: dict) -> None:
    """Show RAG chunks used to generate this article + option to delete from Qdrant."""
    import json as _json
    import os as _os
    from config import OUTPUT_DIR, QDRANT_URL

    item_id = item["id"]

    # Find sources.json file -- saved inside article subdirectory
    sources_file = None
    if _os.path.exists(OUTPUT_DIR):
        # Search subdirectories for sources.json matching this item_id
        for entry in sorted(_os.listdir(OUTPUT_DIR), reverse=True):
            subdir = _os.path.join(OUTPUT_DIR, entry)
            if not _os.path.isdir(subdir):
                continue
            candidate = _os.path.join(subdir, "sources.json")
            if _os.path.exists(candidate):
                try:
                    data = _json.load(open(candidate, encoding="utf-8"))
                    if data.get("item_id") == item_id:
                        sources_file = candidate
                        break
                except Exception:
                    pass

    if not sources_file:
        print(f"\n  No sources.json found for [{item_id}].")
        print(f"  Generate the article first to create source map.")
        return

    data   = _json.load(open(sources_file, encoding="utf-8"))
    chunks = data.get("chunks", [])

    if not chunks:
        print(f"  No chunks recorded.")
        return

    print(f"\n  RAG sources for: {data.get('topic', '')[:60]}")
    print(f"  Generated: {data.get('generated', '')[:19]}  |  Model: {data.get('model', '')}")
    print(f"  {'-'*90}")
    print(f"  {'ID':<5} {'SCORE':>7}  {'TRUST':<8} {'DOMAIN':<30} {'COLLECTION':<20} PREVIEW")
    print(f"  {'-'*90}")

    for chunk in chunks:
        cid    = chunk.get("id", "?")
        score  = chunk.get("score", 0)
        trust  = chunk.get("trust", "?")[:7]
        domain = chunk.get("domain", "?")[:29]
        col    = chunk.get("collection", "?")[:19]
        preview = chunk.get("text", "")[:50].replace("\n", " ")
        print(f"  {cid:<5} {score:>7.4f}  {trust:<8} {domain:<30} {col:<20} {preview}")

    print(f"  {'-'*90}")
    print(f"\n  Domain summary: " + "  ".join(f"{d}({c})" for d, c in data.get("domains", {}).items()))

    print()
    print("  Commands: [d <ID>] delete chunk from Qdrant | [D <domain>] delete all from domain | [Enter] back")
    print()

    while True:
        raw = input("  > ").strip()
        if not raw:
            break

        parts = raw.split()
        if len(parts) == 2 and parts[0].lower() == "d":
            # Delete single chunk by C-ID
            cid_target = parts[1].upper()
            target = next((c for c in chunks if c.get("id") == cid_target), None)
            if not target:
                print(f"  Chunk {cid_target} not found.")
                continue
            # Find chunk in Qdrant by URL + text preview
            try:
                from qdrant_client import QdrantClient
                from qdrant_client.models import Filter, FieldCondition, MatchValue
                qc  = QdrantClient(url=QDRANT_URL)
                col = target["collection"]
                # Search by URL
                res = qc.scroll(
                    collection_name = col,
                    scroll_filter   = Filter(must=[FieldCondition(
                        key="url", match=MatchValue(value=target.get("url", ""))
                    )]),
                    limit           = 50,
                    with_payload    = True,
                    with_vectors    = False,
                )
                # Find chunk with matching text preview
                preview_match = target.get("text", "")[:100].lower()
                to_delete = []
                for p in res[0]:
                    if preview_match[:50] in p.payload.get("text", "").lower()[:150]:
                        to_delete.append(p.id)

                if not to_delete:
                    print(f"  Could not locate chunk in Qdrant (may already be deleted).")
                    continue

                confirm = input(f"  Delete {len(to_delete)} chunk(s) for {cid_target} from {col}? [y/N]: ").strip().lower()
                if confirm in ("y", "yes"):
                    qc.delete(collection_name=col, points_selector=to_delete)
                    print(f"  [OK] Deleted {len(to_delete)} chunk(s) from {col}")
            except Exception as e:
                print(f"  Error: {e}")

        elif len(parts) == 2 and parts[0].upper() == "D":
            # Delete all chunks from domain
            domain_target = parts[1].lower()
            domain_chunks = [c for c in chunks if c.get("domain", "").lower() == domain_target]
            if not domain_chunks:
                print(f"  No chunks from '{domain_target}'.")
                continue
            try:
                from qdrant_client import QdrantClient
                from qdrant_client.models import Filter, FieldCondition, MatchValue
                qc = QdrantClient(url=QDRANT_URL)
                # Group by collection
                by_col = {}
                for c in domain_chunks:
                    col = c["collection"]
                    by_col.setdefault(col, []).append(c.get("url", ""))

                total_deleted = 0
                for col, urls in by_col.items():
                    for url in set(urls):
                        res = qc.scroll(
                            collection_name = col,
                            scroll_filter   = Filter(must=[FieldCondition(
                                key="url", match=MatchValue(value=url)
                            )]),
                            limit=100, with_payload=False, with_vectors=False,
                        )
                        ids = [p.id for p in res[0]]
                        if ids:
                            confirm = input(f"  Delete {len(ids)} chunks from {domain_target} in {col}? [y/N]: ").strip().lower()
                            if confirm in ("y", "yes"):
                                qc.delete(collection_name=col, points_selector=ids)
                                total_deleted += len(ids)
                                print(f"  [OK] Deleted {len(ids)} from {col}")
                if total_deleted:
                    print(f"  [OK] Total deleted: {total_deleted} chunks")
            except Exception as e:
                print(f"  Error: {e}")
        else:
            print("  Unknown command. Use: d C01 | D ign.com | Enter to exit")


def _score_single(item: dict) -> None:
    """Score a generated article for this queue item."""
    import os as _os
    import json as _j
    from config import OUTPUT_DIR, OLLAMA_URL

    item_id = item["id"]
    if item.get("status") != "done":
        print("  Item not done -- generate article first.")
        return

    # Find article dir
    article_dir = None
    metadata    = None
    if _os.path.exists(OUTPUT_DIR):
        for d in sorted(_os.listdir(OUTPUT_DIR), reverse=True):
            dp = _os.path.join(OUTPUT_DIR, d)
            mp = _os.path.join(dp, "metadata.json")
            if _os.path.isdir(dp) and _os.path.exists(mp):
                try:
                    meta = _j.load(open(mp, encoding="utf-8"))
                    if meta.get("queue_id") == item_id:
                        article_dir = dp
                        metadata    = meta
                        break
                except Exception:
                    pass

    if not article_dir:
        print(f"  No output dir found for [{item_id}].")
        return

    # Show existing scoring breakdown if present
    existing = metadata.get("scoring") if metadata else None
    if existing and existing.get("raw_response"):
        score_str = f"{existing['score']}/10" if existing.get("score") is not None else "?"
        print()
        print(f"  Last scoring: {existing.get('verdict','?').upper()}  {score_str}"
              f"  [{existing.get('scored_at','')[:19]}]  model={existing.get('model','?')}")
        print()
        print("  -- full breakdown -------------------------------------")
        for _line in existing["raw_response"].strip().splitlines():
            print(f"  {_line}")
        print("  -------------------------------------------------------")
        print()
        if existing.get("issues"):
            print("  Issues:")
            for issue in existing["issues"]:
                print(f"    - {issue}")
            print()
        rescore = input("  Re-score now? [y/N]: ").strip().lower()
        if rescore not in ("y", "yes"):
            return
    elif existing:
        # Has scoring but no raw_response (old format)
        score_str = f"{existing['score']}/10" if existing.get("score") is not None else "?"
        print(f"  Last scoring: {existing.get('verdict','?').upper()}  {score_str}"
              f"  (no breakdown saved -- re-scoring to get full details)")

    try:
        from pipeline.scoring_pass import score_article
        scoring = score_article(
            article_dir = article_dir,
            item        = item,
            ollama_url  = OLLAMA_URL,
            model       = item.get("model", ""),
        )
        if scoring:
            import core.queue as _cq
            if scoring.get("verdict") in ("fail", "weak"):
                _cq.update_field(item_id, "scoring_flag", True)
                _cq.update_field(item_id, "scoring_verdict", scoring.get("verdict", ""))
                _cq.update_field(item_id, "scoring_score", scoring.get("score"))
                _cq.update_field(item_id, "scoring_score", scoring.get("score"))
            else:
                _cq.update_field(item_id, "scoring_flag", False)
                _cq.update_field(item_id, "scoring_verdict", scoring.get("verdict", ""))
    except Exception as e:
        print(f"  Scoring error: {e}")


def _rewrite_single(item: dict) -> None:
    """Rewrite a specific queue item's output file with Claude."""
    import os as _os
    import subprocess as _sp
    from config import OUTPUT_DIR

    # Find output file for this item
    item_id = item["id"]
    topic   = item.get("topic", "")

    # Look for matching .md file in output/
    candidates = []
    if _os.path.exists(OUTPUT_DIR):
        for f in _os.listdir(OUTPUT_DIR):
            if f.endswith(".md") and not f.startswith("_") and "__" not in f:
                path = _os.path.join(OUTPUT_DIR, f)
                # Check if file references this item_id
                try:
                    with open(path, encoding="utf-8") as fh:
                        head = fh.read(500)
                    if item_id in head:
                        candidates.append((path, _os.path.getmtime(path)))
                except Exception:
                    pass

    if not candidates:
        print(f"  No output file found for [{item_id}]. Generate article first.")
        return

    # Pick newest
    candidates.sort(key=lambda x: x[1], reverse=True)
    filepath = candidates[0][0]
    print(f"  Found: {_os.path.basename(filepath)}")

    rewrite_path = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "claude_rewrite.py")
    if not _os.path.exists(rewrite_path):
        print("  claude_rewrite.py not found.")
        return

    _sp.call([__import__('sys').executable, rewrite_path, "--file", filepath, "--persona", "lukasz"])


def _queue_menu() -> None:
    """Queue menu  --  shows list immediately, supports inline commands."""

    def _resolve_item(raw: str, items: list):
        """Resolve number or ID to item."""
        if raw.isdigit():
            idx = int(raw) - 1
            if 0 <= idx < len(items):
                return items[idx]
        else:
            matches = [i for i in items if i["id"].startswith(raw)]
            if matches:
                return matches[0]
        return None

    while True:
        all_items  = queue.get_all()
        n_pending  = sum(1 for i in all_items if i["status"] == queue.STATUS_PENDING)
        n_research = sum(1 for i in all_items if i["status"] == queue.STATUS_RESEARCHED)
        n_done     = sum(1 for i in all_items if i["status"] == queue.STATUS_DONE)
        n_errors   = sum(1 for i in all_items if i["status"] == queue.STATUS_ERROR)

        # Always show list
        _fmt_list(all_items)

        if not all_items:
            input("  [Enter] Back: ")
            break

        print(f"  -- {n_pending} pending  {n_research} ready  {n_done} done  {n_errors} err --")
        print(f"  [Enter] back")
        print(f"  [cd]  clear done         --  removes all completed items and their research chunks")
        print(f"  [re]  reset errors       --  resets failed items back to pending")
        print(f"  [rr]  reset researched   --  resets researched items back to pending (clears chunks)")
        print()
        raw = input("  > ").strip().lower()

        if raw in ("", "q", "back"):
            break

        # Inline commands: g 2, w 3, r 1, x 4
        parts = raw.split()
        if len(parts) == 2 and parts[0] in ("g", "w", "r", "x", "sc"):
            action = parts[0]
            item   = _resolve_item(parts[1], all_items)
            if not item:
                print("  Item not found.")
                continue

            if action == "g":
                status = item.get("status", "")
                if status == queue.STATUS_RESEARCHED:
                    confirm = input(f"  Generate [{item['id']}] {item['topic'][:40]}? [Y/n]: ").strip().lower()
                    if confirm in ("", "y", "yes"):
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
                        if not item.get("article_focus"):
                            focus = _focus_picker(item)
                            if focus:
                                _save_focus(item, focus)
                        _run_generate(item)
                        rw = input("  Rewrite with Claude? [y/N]: ").strip().lower()
                        if rw in ("y", "yes"):
                            _rewrite_single(item)
                elif status == queue.STATUS_DONE:
                    print(f"  Already done  --  use [r {parts[1]}] to reset first.")
                else:
                    print(f"  Status is '{status}'  --  need 'researched'.")
            elif action == "w":
                # Rewrite
                _rewrite_single(item)

            elif action == "r":
                # Redo
                status = item.get("status", "")
                if status == queue.STATUS_DONE:
                    print(f"  [1] Regenerate only (keep research)")
                    print(f"  [2] Full re-run (new research + generate)")
                    print(f"  [3] Uber Research (gap analysis + targeted search, then regenerate)")
                    ch = input("  > ").strip()
                    if ch == "1":
                        _reset_to_researched(item)
                        _rf = input("  Reset focus? [Y/n]: ").strip().lower()
                        if _rf in ("", "y", "yes"):
                            _clear_focus(item)
                        gen = input("  Generate now? [Y/n]: ").strip().lower()
                        if gen in ("", "y", "yes"):
                            _run_generate(item)
                    elif ch == "3":
                        try:
                            from qdrant_client import QdrantClient as _UbQC
                            from config import (
                                QDRANT_URL as _UbQURL, OLLAMA_URL as _UbOLLAMA,
                                UBER_RESEARCH_MODEL, UBER_GAP_QUESTIONS, UBER_MAX_URLS_PER_Q,
                                research_collection as _UbRC,
                            )
                            from pipeline.uber_research import run_uber_research
                            _ub_client = _UbQC(url=_UbQURL)
                            _ub_col    = _UbRC(item.get("category", "other"))
                            print(f"  Running Uber Research on: {item['topic'][:60]}")
                            _ub_result = run_uber_research(
                                item           = item,
                                col_name       = _ub_col,
                                client         = _ub_client,
                                ollama_url     = _UbOLLAMA,
                                model          = UBER_RESEARCH_MODEL,
                                is_night_run   = False,
                                n_questions    = UBER_GAP_QUESTIONS,
                                max_urls_per_q = UBER_MAX_URLS_PER_Q,
                            )
                            import core.queue as _ubq
                            _ubq.update_field(item["id"], "uber_research", _ub_result)
                            print(f"  [OK] New chunks: {_ub_result.get('new_chunks', 0)}  |  Total: {_ub_result.get('total_after', 0)}")
                            _reset_to_researched(item)
                            _rf = input("  Reset focus? [Y/n]: ").strip().lower()
                            if _rf in ("", "y", "yes"):
                                _clear_focus(item)
                            gen = input("  Generate now? [Y/n]: ").strip().lower()
                            if gen in ("", "y", "yes"):
                                _run_generate(item)
                        except Exception as _ub_err:
                            print(f"  Uber Research error: {_ub_err}")
                    elif ch == "2":
                        q = queue.load()
                        for qi in q["items"]:
                            if qi["id"] == item["id"]:
                                qi["status"]        = queue.STATUS_PENDING
                                qi["researched_at"] = None
                                qi["done_at"]       = None
                                qi["error"]         = None
                                item.update(qi)
                                break
                        queue.save(q)
                        print(f"  [OK] Reset to pending.")
                elif status == queue.STATUS_RESEARCHED:
                    gen = input(f"  Regenerate [{item['id']}]? [Y/n]: ").strip().lower()
                    if gen in ("", "y", "yes"):
                        from pipeline.generate_run import run_generate
                        run_generate(item_ids=[item["id"]], api_provider="")
                elif status == queue.STATUS_ERROR:
                    q = queue.load()
                    for qi in q["items"]:
                        if qi["id"] == item["id"]:
                            qi["status"] = queue.STATUS_PENDING
                            qi["error"]  = None
                            break
                    queue.save(q)
                    print(f"  [OK] Reset to pending.")
                else:
                    print("  Already pending.")

            elif action == "x":
                confirm = input(f"  Remove [{item['id']}] {item['topic'][:40]}? [y/N]: ").strip().lower()
                if confirm in ("y", "yes"):
                    # Cleanup research chunks from Qdrant
                    try:
                        from qdrant_client import QdrantClient as _QC
                        from qdrant_client.models import Filter as _F, FieldCondition as _FC, MatchValue as _MV
                        from config import QDRANT_URL as _QURL, research_collection as _rc
                        _qc = _QC(url=_QURL)
                        _col = _rc(item.get("category", "other"))
                        _existing = [c.name for c in _qc.get_collections().collections]
                        if _col in _existing:
                            _qc.delete(
                                collection_name=_col,
                                points_selector=_F(must=[_FC(key="item_id", match=_MV(value=item["id"]))]),
                            )
                            print(f"  [OK] Research chunks cleaned.")
                    except Exception as _e:
                        print(f"  [!] Chunk cleanup error: {_e}")
                    queue.remove_item(item["id"])
                    print(f"  [OK] Removed.")

        elif raw.isdigit():
            # Number alone = inspect
            item = _resolve_item(raw, all_items)
            if item:
                _inspect_edit_item(item)
            else:
                print("  Not found.")

        elif raw == "cd":
            done_items = [i for i in all_items if i["status"] == queue.STATUS_DONE]
            if not done_items:
                print("  No completed items.")
            else:
                confirm = input(f"  Clear {len(done_items)} done items? [Y/n]: ").strip().lower()
                if confirm in ("", "y", "yes"):
                    from pipeline.research_run import delete_vectors_for_items
                    delete_vectors_for_items([i["id"] for i in done_items])
                    n = queue.clear_done()
                    print(f"  [OK] Removed {n} items.")

        elif raw == "re":
            if n_errors == 0:
                print("  No error items.")
            else:
                n = queue.reset_errors()
                print(f"  [OK] Reset {n} error items -> pending.")

        elif raw == "rr":
            if n_research == 0:
                print("  No researched items.")
            else:
                confirm = input(f"  Reset {n_research} researched -> pending? [y/N]: ").strip().lower()
                if confirm in ("y", "yes"):
                    from pipeline.research_run import delete_vectors_for_items
                    researched_items = [i for i in all_items if i["status"] == queue.STATUS_RESEARCHED]
                    delete_vectors_for_items([i["id"] for i in researched_items])
                    n = queue.reset_researched()
                    print(f"  [OK] Reset {n} items -> pending.")

        else:
            print("  Unknown command. Try: number, g/w/r/x <number>, 4/5/6")


# "" Run Pipeline menu """"""""""""""""""""""""""""""""""""""""""""""""""""""