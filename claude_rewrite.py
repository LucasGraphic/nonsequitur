#!/usr/bin/env python3
# claude_rewrite.py -- Article Agent: API rewrite pass with persona system
#
# Supported providers: Claude (Anthropic), Gemini (Google), Groq
#
# USAGE:
#   python claude_rewrite.py                           # interactive
#   python claude_rewrite.py --file output/draft_dir --persona lukasz
#   python claude_rewrite.py --provider gemini
#   python claude_rewrite.py --dry-run
#   python claude_rewrite.py --stats

import os
import re
import sys
import json
import time
import argparse
import requests
from datetime import datetime
from pathlib import Path

# -- Paths ------------------------------------------------------------------

BASE_DIR     = os.path.dirname(os.path.abspath(__file__))
OUTPUT_DIR   = os.path.join(BASE_DIR, "output")
PERSONAS_DIR = os.path.join(BASE_DIR, "data", "personas")

AVAILABLE_PERSONAS = ["lukasz", "neutral", "critic", "paranoic"]

# -- API Providers ----------------------------------------------------------

PROVIDERS = {
    "claude": {
        "label":              "Claude Sonnet (Anthropic)",
        "model":              "claude-sonnet-4-6",
        "env_key":            "ANTHROPIC_API_KEY",
        "url":                "https://api.anthropic.com/v1/messages",
        "price_input_per_m":  3.00,
        "price_output_per_m": 15.00,
        "free":               False,
    },
    "gemini": {
        "label":              "Gemini 2.0 Flash (Google -- free tier)",
        "model":              "gemini-2.0-flash",
        "env_key":            "GEMINI_API_KEY",
        "url":                "https://generativelanguage.googleapis.com/v1beta/models",
        "price_input_per_m":  0.10,
        "price_output_per_m": 0.40,
        "free":               True,
        "get_key_url":        "https://aistudio.google.com/apikey",
    },
    "groq": {
        "label":              "Groq -- llama-3.3-70b (free tier)",
        "model":              "llama-3.3-70b-versatile",
        "env_key":            "GROQ_API_KEY",
        "url":                "https://api.groq.com/openai/v1/chat/completions",
        "price_input_per_m":  0.59,
        "price_output_per_m": 0.79,
        "free":               True,
        "get_key_url":        "https://console.groq.com/keys",
    },
    "deepseek": {
        "label":              "DeepSeek V3",
        "model":              "deepseek-chat",
        "env_key":            "DEEPSEEK_API_KEY",
        "url":                "https://api.deepseek.com/v1/chat/completions",
        "price_input_per_m":  0.27,
        "price_output_per_m": 1.10,
        "free":               False,
        "get_key_url":        "https://platform.deepseek.com/api_keys",
    },
    "local_max": {
        "label":              "Local MAX (qwen3.5:122b -- Ollama Windows)",
        "model":              "qwen3.5:122b",
        "env_key":            "",
        "url":                "",
        "price_input_per_m":  0.0,
        "price_output_per_m": 0.0,
        "free":               True,
        "local":              True,
    },
}
DEFAULT_PROVIDER = "claude"


# -- Qdrant persona retrieval -----------------------------------------------

def _embed(text: str, ollama_embed_url: str, embed_model: str):
    try:
        r = requests.post(
            f"{ollama_embed_url}/api/embed",
            json={"model": embed_model, "input": text},
            timeout=60,
        )
        data = r.json()
        embeddings = data.get("embeddings")
        if embeddings and len(embeddings) > 0:
            return embeddings[0]
        return data.get("embedding")
    except Exception as e:
        print(f"  [embed] ERROR: {e}")
        return None


def _retrieve_persona_rag(persona: str, topic: str, article_focus: str,
                           category: str = "", top_k: int = 12) -> str:
    try:
        from config import QDRANT_URL, OLLAMA_EMBED_URL, EMBED_MODEL
        from qdrant_client import QdrantClient
        from qdrant_client.models import SparseVector, Prefetch, FusionQuery, Fusion
    except ImportError as e:
        print(f"  [persona rag] Import error: {e}")
        return ""

    PERSONA_CATEGORY_MAP = {
        "games":       "games",
        "hardware":    "tech",
        "software":    "tech",
        "security":    "tech",
        "ai-data":     "ai",
        "photography": "photography",
    }
    persona_base = persona if persona else "lukasz"
    cat_suffix   = PERSONA_CATEGORY_MAP.get(category, "")
    persona_cols = [
        f"persona_{persona_base}_style",
        f"persona_{persona_base}_worldview",
    ]
    if cat_suffix:
        persona_cols.append(f"persona_{persona_base}_{cat_suffix}")

    try:
        client   = QdrantClient(url=QDRANT_URL)
        existing = [c.name for c in client.get_collections().collections]
        persona_cols = [c for c in persona_cols if c in existing]
        if not persona_cols:
            print(f"  [persona rag] No collections found for '{persona_base}'")
            return ""

        query  = article_focus.strip() if article_focus else topic
        vec    = _embed(query, OLLAMA_EMBED_URL, EMBED_MODEL)
        if not vec:
            return ""

        tokens = re.findall(r"[a-z0-9]+", query.lower())
        tf = {}
        for t in tokens:
            if len(t) > 2:
                tf[t] = tf.get(t, 0) + 1
        total   = max(len(tokens), 1)
        idx_map = {}
        for t, count in tf.items():
            idx = abs(hash(t)) % (2**20)
            idx_map[idx] = round(count / total, 6)

        all_chunks = []
        seen_texts = set()
        for collection in persona_cols:
            results = client.query_points(
                collection_name = collection,
                prefetch = [
                    Prefetch(query=vec, using="dense", limit=top_k),
                    Prefetch(
                        query = SparseVector(
                            indices = list(idx_map.keys()),
                            values  = list(idx_map.values()),
                        ),
                        using = "sparse",
                        limit = top_k,
                    ),
                ],
                query        = FusionQuery(fusion=Fusion.RRF),
                limit        = top_k,
                with_payload = True,
            ).points
            added = 0
            for r in results:
                text = r.payload.get("text", "")
                if text and text not in seen_texts:
                    seen_texts.add(text)
                    all_chunks.append(text)
                    added += 1
            if added:
                print(f"  [persona rag] {collection}: {added} chunks")

        if all_chunks:
            return "\n\n---\n\n".join(all_chunks[:top_k])
        return ""
    except Exception as e:
        print(f"  [persona rag] ERROR: {e}")
        return ""

def _load_persona(persona: str) -> str:
    path = os.path.join(PERSONAS_DIR, f"{persona}.md")
    if not os.path.exists(path):
        pass  # .md file not required -- Qdrant fallback handles it
        return ""
    with open(path, encoding="utf-8") as f:
        return f.read().strip()


# -- Draft loader -----------------------------------------------------------

def _load_draft(filepath: str) -> dict:
    """
    Load draft from:
    - directory: reads article.md + metadata.json
    - .md file: reads file with optional YAML frontmatter (legacy)
    """
    result = {
        "body":                 "",
        "topic":                "",
        "focus":                "",
        "category":             "other",
        "section":              "DATA",
        "model":                "",
        "titles":               [],
        "excerpts":             [],
        "article_type":         "",
        "article_length":       "medium",
        "schema_default_length": "",
    }

    p = Path(filepath)

    # New format: directory with article.md + metadata.json
    if p.is_dir():
        article_path  = p / "article.md"
        metadata_path = p / "metadata.json"

        if article_path.exists():
            import re as _re_html
            body = article_path.read_text(encoding="utf-8").strip()
            # Remove RAG_CONTEXT comment
            rag_idx = body.find("<!-- RAG_CONTEXT")
            if rag_idx != -1:
                body = body[:rag_idx].strip()
            # Remove all HTML comments (<!-- ALT: -->, <!-- META: -->, etc.)
            body = _re_html.sub(r'<!--.*?-->', '', body, flags=_re_html.DOTALL).strip()
            result["body"] = body

        if metadata_path.exists():
            try:
                meta = json.loads(metadata_path.read_text(encoding="utf-8"))
                result["topic"]    = meta.get("topic", "")
                result["focus"]    = meta.get("article_focus", meta.get("focus", ""))
                result["category"] = meta.get("category", "other")
                result["section"]  = meta.get("section", "DATA")
                result["model"]    = meta.get("model", "")
                result["titles"]               = meta.get("titles", [])
                result["excerpts"]             = meta.get("excerpts", [])
                result["article_type"]         = meta.get("article_type", "")
                result["article_length"]       = meta.get("article_length", "medium")
            except Exception as e:
                print(f"  [draft] metadata.json parse error: {e}")
        return result

    # Legacy format: single .md file with YAML frontmatter
    content = p.read_text(encoding="utf-8")
    result["body"] = content

    fm_match = re.match(r"^---\n(.*?)\n---\n", content, re.DOTALL)
    if fm_match:
        fm_text        = fm_match.group(1)
        result["body"] = content[fm_match.end():].strip()

        for line in fm_text.split("\n"):
            line = line.strip()
            if line.startswith("topic:"):
                result["topic"] = line.split(":", 1)[1].strip().strip('"').strip("'")
            elif line.startswith("article_focus:") or line.startswith("focus:"):
                result["focus"] = line.split(":", 1)[1].strip().strip('"').strip("'")
            elif line.startswith("category:"):
                result["category"] = line.split(":", 1)[1].strip()
            elif line.startswith("section:"):
                result["section"] = line.split(":", 1)[1].strip()
            elif line.startswith("model:"):
                result["model"] = line.split(":", 1)[1].strip()

        titles_match = re.search(r"titles:\s*\n((?:\s+- .+\n?)+)", fm_text)
        if titles_match:
            result["titles"] = [
                re.sub(r'^\s*-\s*"?|"?\s*$', "", line).strip()
                for line in titles_match.group(1).split("\n")
                if line.strip().startswith("-")
            ]

        excerpts_match = re.search(r"excerpts:\s*\n((?:\s+- .+\n?)+)", fm_text)
        if excerpts_match:
            result["excerpts"] = [
                re.sub(r'^\s*-\s*"?|"?\s*$', "", line).strip()
                for line in excerpts_match.group(1).split("\n")
                if line.strip().startswith("-")
            ]

    return result


# -- Prompt builder ---------------------------------------------------------

def _build_rewrite_prompt(draft: dict, persona_text: str,
                           persona_rag: str, persona_name: str,
                           length_instruction: str = "1200-1800 words") -> str:
    persona_block = ""
    if persona_text:
        persona_block = f"""
=== YOUR PERSONA ===
You are rewriting this article as the person described below.
Internalize this persona completely -- voice, opinions, style, worldview.

{persona_text}
=== END PERSONA ===
"""

    rag_block = ""
    if persona_rag:
        rag_block = f"""
=== PERSONA KNOWLEDGE BASE ===
Additional context from the persona's knowledge base (past writing, style examples):
{persona_rag[:4000]}
=== END PERSONA KNOWLEDGE BASE ===
"""

    focus_block = ""
    if draft["focus"]:
        focus_block = f"""
=== ARTICLE DIRECTION ===
The article must follow this editorial direction:
{draft["focus"]}
=== END ARTICLE DIRECTION ===
"""

    titles_block = ""  # titles/excerpts kept from local draft -- not regenerated by API

    return f"""You are rewriting a draft article in a specific author's voice.

{persona_block}
{rag_block}
{focus_block}

=== DRAFT ARTICLE ===
Topic: {draft["topic"]}
Section: {draft["section"]} | Category: {draft["category"]}

{draft["body"]}
=== END DRAFT ===


REWRITE INSTRUCTIONS:
1. Rewrite the ENTIRE article in the persona's voice -- not just surface-level edits

2. Keep ALL facts, data, and technical details from the draft -- do NOT invent new facts

3. Replace generic/corporate language with the persona's direct, opinionated style

4. OPENING -- first sentence must be a thesis: a specific, arguable claim about this
   specific game/topic. Not a description. Not scene-setting. Not a question.
   BAD: "Seven years is an eternity in the theater of technology"
   BAD: "Speculative fiction has always served as a mirror for societal anxieties"
   GOOD: "Replaced stopped being speculative fiction somewhere between 2018 and now --
         the developers built a warning and reality converted it into a document"
   GOOD: "Replaced is the best argument against the idea that games need to be subtle"

5. CONCLUSION -- last paragraph must deliver a verdict: a specific evaluative position
   on the game/topic that could not serve as an intro to a different article.
   NEVER end with: rhetorical questions, 'the question is whether', 'it remains to be
   seen', calls to action, observations about 'our time', poetic observations about
   the future. These are evasions, not conclusions.
   BAD: "The question is whether we can still see the walls before they close in"
   BAD: "The dystopias of tomorrow are being built today"
   GOOD: "Replaced is essential and uneven in roughly equal measure -- the world it
         builds is worth the mechanical frustration of getting through it"
   GOOD: "Sad Cat Studios made a game about losing distance from reality at exactly
         the moment that distance ran out -- that timing is luck, but the game
         is good enough to deserve it"

6. ARGUMENT PROGRESSION -- each section must advance the argument, not restate it.
   If removing a section would not change the conclusion, cut it or merge it.
   The final section must take a harder position than the opening section.

7. If the assessment is negative -- say it directly. Name the company, the developer,
   the decision. 'Blizzard failed' not 'the developer made choices that disappointed'

8. Cut filler phrases: 'raises questions about', 'it remains to be seen',
   'some users have noted', 'it is worth noting', 'in our time', 'in today's world'

9. Length: {length_instruction} -- expand where analysis allows, cut where it repeats

10. Format: Markdown with H1 title, H2 section headers -- output the article body ONLY,
    no titles list, no excerpts, no tags

11. FORBIDDEN sentence starters -- never open a sentence with these:
    "This proves", "This shows", "This demonstrates", "This signals",
    "This is not", "This is the", "This is a", "This is why", "This is how",
    "This level", "This move", "This decision", "This approach"
    Rewrite as an active claim instead.

12. FORBIDDEN filler non-statements -- replace with a concrete argument:
    "It is a bold move." / "It is a risky move." / "It is a good sign."
    State WHO did WHAT and WHY it matters.

13. FORBIDDEN short sentence stacks -- never write three or more consecutive
    sentences under 12 words each. Merge into one analytical sentence.

14. ZERO REPETITION -- every fact and argument appears once. If a point was
    made in a previous section, do not restate it even in different words.
    Each H2 section must introduce a new argument.

15. H2 HEADERS -- every H2 must contain a verb or specific claim, not a noun phrase.
    BAD: "Narrative Depth"  GOOD: "The Narrative Earns Its Ambition"
    BAD: "Visual Design"    GOOD: "Visual Design Argues Against Spectacle"

16. Do NOT present both sides without taking a position. Pick a side and defend it.

Write the rewritten article body now:
"""


# -- API calls per provider -------------------------------------------------

def _call_api(prompt: str, provider_key: str, dry_run: bool = False) -> dict:
    empty = {"text": "", "input_tokens": 0, "output_tokens": 0,
             "cost_usd": 0.0, "provider": provider_key}

    provider = PROVIDERS.get(provider_key)
    if not provider:
        print(f"  [X] Unknown provider: {provider_key}")
        return empty

    if dry_run:
        print("\n" + "=" * 60)
        print(f"  DRY RUN -- Provider: {provider['label']}")
        print("=" * 60)
        print(prompt[:3000])
        if len(prompt) > 3000:
            print(f"\n  ... [{len(prompt) - 3000} more chars] ...")
        print("=" * 60)
        print(f"  Prompt: {len(prompt)} chars (~{len(prompt)//4} tokens estimated)")
        return empty

    api_key = os.environ.get(provider.get("env_key", ""), "") if provider.get("env_key") else ""
    if not api_key and not provider.get("local"):
        print(f"  [X] No API key for {provider['label']}")
        print(f"  Set: setx {provider['env_key']} \"your-key\"")
        if provider.get("get_key_url"):
            print(f"  Get key: {provider['get_key_url']}")
        return empty

    model   = provider["model"]
    url     = provider["url"]
    headers = {"Content-Type": "application/json"}

    # -- Local Ollama MAX model (no API key needed) --
    if provider_key == "local_max":
        from config import OLLAMA_URL as _LOCAL_URL
        try:
            import requests as _req
            print(f"  -> Calling Local MAX ({model})...")
            t0 = __import__("time").time()
            r = _req.post(
                f"{_LOCAL_URL}/api/chat",
                json={
                    "model":   model,
                    "think":   False,
                    "stream":  False,
                    "options": {"num_predict": 6000},
                    "messages": [{"role": "user", "content": prompt}],
                },
                timeout=900,
            )
            elapsed = __import__("time").time() - t0
            if r.status_code != 200:
                print(f"  Local MAX error {r.status_code}")
                return empty
            data = r.json()
            text = data.get("message", {}).get("content", "").strip()
            t_in  = data.get("prompt_eval_count", 0)
            t_out = data.get("eval_count", 0)
            print(f"  Local MAX: {len(text)} chars | {elapsed:.1f}s")
            return {"text": text, "input_tokens": t_in, "output_tokens": t_out,
                    "cost_usd": 0.0, "provider": provider_key}
        except Exception as e:
            print(f"  Local MAX error: {e}")
            return empty

    if provider_key == "claude":
        headers["x-api-key"]         = api_key
        headers["anthropic-version"] = "2023-06-01"
        payload = {
            "model":      model,
            "max_tokens": 8192,
            "messages":   [{"role": "user", "content": prompt}],
        }

    elif provider_key == "gemini":
        url     = f"{url}/{model}:generateContent?key={api_key}"
        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "maxOutputTokens": 8192,
                "temperature":     0.7,
            },
        }

    elif provider_key in ("groq", "deepseek"):
        headers["Authorization"] = f"Bearer {api_key}"
        payload = {
            "model":       model,
            "messages":    [{"role": "user", "content": prompt}],
            "max_tokens":  8192,
            "temperature": 0.7,
        }

    else:
        print(f"  [X] Provider '{provider_key}' not implemented")
        return empty

    try:
        print(f"  -> Calling {provider['label']}...")
        t_start = time.time()

        r       = requests.post(url, headers=headers, json=payload, timeout=300)
        elapsed = time.time() - t_start

        if r.status_code != 200:
            print(f"  [X] API error {r.status_code}: {r.text[:400]}")
            return empty

        data       = r.json()
        text       = ""
        tokens_in  = 0
        tokens_out = 0

        if provider_key == "claude":
            for block in data.get("content", []):
                if block.get("type") == "text":
                    text += block.get("text", "")
            usage      = data.get("usage", {})
            tokens_in  = usage.get("input_tokens", 0)
            tokens_out = usage.get("output_tokens", 0)

        elif provider_key == "gemini":
            candidates = data.get("candidates", [])
            if candidates:
                parts = candidates[0].get("content", {}).get("parts", [])
                text  = "".join(p.get("text", "") for p in parts)
            usage      = data.get("usageMetadata", {})
            tokens_in  = usage.get("promptTokenCount", 0)
            tokens_out = usage.get("candidatesTokenCount", 0)

        elif provider_key in ("groq", "deepseek"):
            choices = data.get("choices", [])
            if choices:
                text = choices[0].get("message", {}).get("content", "")
            usage      = data.get("usage", {})
            tokens_in  = usage.get("prompt_tokens", 0)
            tokens_out = usage.get("completion_tokens", 0)

        cost_usd = (tokens_in  / 1_000_000 * provider["price_input_per_m"] +
                    tokens_out / 1_000_000 * provider["price_output_per_m"])

        free_tag = "  [free tier]" if provider.get("free") else ""
        print(f"  [OK] {provider['label']}: {len(text)} chars | {elapsed:.1f}s{free_tag}")
        print(f"  📊 Tokens: {tokens_in:,} in + {tokens_out:,} out = "
              f"{tokens_in + tokens_out:,} total | Cost: ${cost_usd:.4f}")

        return {
            "text":          text,
            "input_tokens":  tokens_in,
            "output_tokens": tokens_out,
            "cost_usd":      cost_usd,
            "elapsed":       elapsed,
            "provider":      provider_key,
        }

    except Exception as e:
        print(f"  [X] API exception: {e}")
        return empty


# -- Output parser ----------------------------------------------------------

def _parse_rewrite(text: str) -> dict:
    sections = {
        "body":      "",
        "titles":    [],
        "excerpts":  [],
        "image_seo": [],
        "tags":      [],
        "h1_title":  "",
    }

    markers   = ["=== TITLES ===", "=== EXCERPTS ===",
                 "=== IMAGE SEO TITLES ===", "=== TAGS ==="]
    positions = {}
    for m in markers:
        idx = text.find(m)
        if idx != -1:
            positions[m] = idx

    if positions:
        sections["body"] = text[:min(positions.values())].strip()
    else:
        sections["body"] = text.strip()

    for line in sections["body"].split("\n"):
        line = line.strip()
        if line.startswith("# "):
            sections["h1_title"] = line[2:].strip()
            break

    def _extract(marker, next_marker=None):
        if marker not in positions:
            return ""
        start = positions[marker] + len(marker)
        end   = positions[next_marker] if next_marker and next_marker in positions else len(text)
        return text[start:end].strip()

    def _parse_list(raw):
        items = []
        for line in raw.split("\n"):
            line    = line.strip()
            if not line:
                continue
            line    = re.sub(r"\*{1,2}([^*]+)\*{1,2}", r"\1", line)
            cleaned = re.sub(r"^\d+[\.\:\)]\s*", "", line).strip().strip('"').strip("'")
            cleaned = re.sub(r"\s*[----]\s*\d+\s*chars?.*$", "", cleaned).strip()
            cleaned = re.sub(r"\s*\(\d+\s*chars?[^)]*\)\s*$", "", cleaned).strip()
            cleaned = re.sub(r"\s*--\s*trim(?:ming)?:?\s*$", "", cleaned).strip()
            cleaned = re.sub(r"\s*[[OK][X]]\s*$", "", cleaned).strip()
            cleaned = re.sub(r"^`(.+)`$", r"\1", cleaned).strip()
            if re.match(r"(let me|here are|note:|recount|redo|carefully)", cleaned.lower()):
                continue
            if cleaned and len(cleaned) > 3:
                items.append(cleaned)
        return items

    marker_order = ["=== TITLES ===", "=== EXCERPTS ===",
                    "=== TAGS ==="]  # IMAGE SEO TITLES removed
    for i, marker in enumerate(marker_order):
        next_m = marker_order[i + 1] if i + 1 < len(marker_order) else None
        raw    = _extract(marker, next_m)
        items  = _parse_list(raw)

        if marker == "=== TITLES ===":
            sections["titles"] = items[:5]
        elif marker == "=== EXCERPTS ===":
            sections["excerpts"] = items[:5]
        elif marker == "=== IMAGE SEO TITLES ===":
            sections["image_seo"] = items[:5]
        elif marker == "=== TAGS ===":
            raw_section = _extract(marker, None)
            tag_lines   = [t.strip().lower() for t in raw_section.splitlines()
                           if t.strip() and not t.strip().startswith("=")]
            tag_lines   = [re.sub(r"^\d+[\.\:\)]\s*", "", t) for t in tag_lines]
            tag_lines   = [t for t in tag_lines if 2 <= len(t) <= 40]
            sections["tags"] = tag_lines[:6]

    return sections


# -- Save rewritten article -------------------------------------------------

def _save_rewrite(original_path: str, draft: dict, parsed: dict,
                  persona: str, api_result: dict) -> str:
    provider  = api_result.get("provider", "api")
    prov_info = PROVIDERS.get(provider, {})
    model_str = prov_info.get("model", provider)

    h1_title = parsed.get("h1_title") or draft["topic"]

    # titles/excerpts always from local draft (not from API rewrite)
    draft_titles   = draft.get("titles", [])
    draft_excerpts = draft.get("excerpts", [])
    meta = draft_excerpts[0] if draft_excerpts else ""

    p = Path(original_path)

    # Determine article directory
    if p.is_dir():
        article_dir = str(p)
    elif p.suffix == ".md" and p.parent.name != "output":
        # file inside article dir
        article_dir = str(p.parent)
    else:
        # legacy single .md in output/ -- create sibling dir
        article_dir = os.path.join(OUTPUT_DIR, p.stem)
        os.makedirs(article_dir, exist_ok=True)

    # --- article_{persona}_{provider}_rewrite.md ---
    rewrite_filename = f"article_{persona}_{provider}_rewrite.md"
    rewrite_path = os.path.join(article_dir, rewrite_filename)
    with open(rewrite_path, "w", encoding="utf-8") as f:
        f.write(parsed["body"])

    # --- metadata_{persona}_{provider}_rewrite.json ---
    metadata = {
        "title":              h1_title,
        "topic":              draft["topic"],
        "category":           draft["category"],
        "section":            draft["section"],
        "persona":            persona,
        "provider":           provider,
        "model":              model_str,
        "original_draft":     os.path.basename(original_path),
        "generated_at":       datetime.now().isoformat(),
        "rewrite_tokens_in":  api_result.get("input_tokens", 0),
        "rewrite_tokens_out": api_result.get("output_tokens", 0),
        "rewrite_cost_usd":   round(api_result.get("cost_usd", 0.0), 6),
        "meta_description":   meta,
        "titles":             draft_titles,
        "excerpts":           draft_excerpts,
        # image_seo_titles removed
        "tags":               parsed.get("tags", []),
    }

    metadata_filename = f"metadata_{persona}_{provider}_rewrite.json"
    metadata_path = os.path.join(article_dir, metadata_filename)
    with open(metadata_path, "w", encoding="utf-8") as f:
        json.dump(metadata, f, ensure_ascii=False, indent=2)

    return article_dir


# -- Token log -------------------------------------------------------------

def _log_token_usage(original_path: str, persona: str,
                     api_result: dict, output_path: str) -> None:
    log_path = os.path.join(OUTPUT_DIR, "_rewrite_log.txt")
    stamp    = datetime.now().strftime("%Y-%m-%d %H:%M")
    provider = api_result.get("provider", "?")

    line = (
        f"{stamp}  "
        f"provider={provider:<10}  "
        f"persona={persona:<10}  "
        f"in={api_result.get('input_tokens', 0):>6,}  "
        f"out={api_result.get('output_tokens', 0):>6,}  "
        f"cost=${api_result.get('cost_usd', 0.0):.4f}  "
        f"file={os.path.basename(output_path)}\n"
    )

    with open(log_path, "a", encoding="utf-8") as f:
        f.write(line)

    print(f"  📝 Logged to {log_path}")


def _show_cumulative_cost() -> None:
    log_path = os.path.join(OUTPUT_DIR, "_rewrite_log.txt")
    if not os.path.exists(log_path):
        print("  No rewrite log found.")
        return

    total_in   = 0
    total_out  = 0
    total_cost = 0.0
    total_runs = 0
    by_provider: dict = {}

    with open(log_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                prov_m  = re.search(r"provider=(\S+)", line)
                in_m    = re.search(r"in=\s*([\d,]+)", line)
                out_m   = re.search(r"out=\s*([\d,]+)", line)
                cost_m  = re.search(r"cost=\$([\d.]+)", line)
                prov    = prov_m.group(1) if prov_m else "?"
                t_in    = int(in_m.group(1).replace(",", "")) if in_m else 0
                t_out   = int(out_m.group(1).replace(",", "")) if out_m else 0
                cost    = float(cost_m.group(1)) if cost_m else 0.0
                total_in   += t_in
                total_out  += t_out
                total_cost += cost
                total_runs += 1
                if prov not in by_provider:
                    by_provider[prov] = {"runs": 0, "cost": 0.0}
                by_provider[prov]["runs"] += 1
                by_provider[prov]["cost"] += cost
            except Exception:
                pass

    print(f"\n  -- Cumulative rewrite stats --------------------------")
    print(f"  Total runs  : {total_runs}")
    print(f"  Total tokens: {total_in:,} in + {total_out:,} out = {total_in + total_out:,}")
    print(f"  Total cost  : ${total_cost:.4f}")
    print(f"  Avg/article : ${total_cost/max(total_runs,1):.4f}")
    if by_provider:
        print(f"\n  By provider:")
        for p, s in sorted(by_provider.items()):
            label = PROVIDERS.get(p, {}).get("label", p)
            print(f"    {p:<12} {s['runs']:>3} runs  ${s['cost']:.4f}")
    print(f"  -----------------------------------------------------\n")


# -- Interactive pickers ----------------------------------------------------

def _pick_provider() -> str:
    print()
    print("  -- API Provider --------------------------------------")
    options = list(PROVIDERS.items())
    for i, (key, p) in enumerate(options, 1):
        api_key    = os.environ.get(p["env_key"], "")
        key_status = "[OK] key set" if api_key else "[X] no key"
        free_tag   = "  [free tier]" if p.get("free") else ""
        print(f"  [{i}] {p['label']:<40} {key_status}{free_tag}")
    print()

    raw = input(f"  Pick provider (Enter = {DEFAULT_PROVIDER}): ").strip()
    if not raw:
        return DEFAULT_PROVIDER
    if raw.isdigit() and 1 <= int(raw) <= len(options):
        return options[int(raw) - 1][0]
    if raw in PROVIDERS:
        return raw
    return DEFAULT_PROVIDER


def _pick_draft_file() -> str | None:
    if not os.path.exists(OUTPUT_DIR):
        print(f"  Output dir not found: {OUTPUT_DIR}")
        return None

    # New format: directories with article.md
    dirs = sorted(
        [d for d in os.listdir(OUTPUT_DIR)
         if os.path.isdir(os.path.join(OUTPUT_DIR, d))
         and os.path.exists(os.path.join(OUTPUT_DIR, d, "article.md"))],
        reverse=True,
    )[:30]

    # Legacy: .md files without rewrite
    legacy_files = sorted(
        [f for f in os.listdir(OUTPUT_DIR)
         if f.endswith(".md") and not f.startswith("_") and "__" not in f],
        reverse=True,
    )[:5]

    entries = [(d, True) for d in dirs] + [(f, False) for f in legacy_files]

    if not entries:
        print("  No draft files found in output/")
        return None

    print()
    print("  -- Recent drafts ------------------------------------")
    for i, (name, is_dir) in enumerate(entries, 1):
        if is_dir:
            meta_path = os.path.join(OUTPUT_DIR, name, "metadata.json")
            try:
                meta  = json.loads(Path(meta_path).read_text(encoding="utf-8"))
                topic = meta.get("title", name)[:65]
            except Exception:
                topic = name[:65]
            print(f"  [{i:>2}] {topic:<65}  [dir]")
        else:
            path  = os.path.join(OUTPUT_DIR, name)
            size  = os.path.getsize(path)
            mtime = datetime.fromtimestamp(os.path.getmtime(path)).strftime("%Y-%m-%d %H:%M")
            print(f"  [{i:>2}] {name[:65]:<65}  {size//1024:>4}KB  {mtime}")
    print()

    raw = input("  Pick number (Enter = cancel): ").strip()
    if not raw or not raw.isdigit():
        return None
    idx = int(raw) - 1
    if 0 <= idx < len(entries):
        name, is_dir = entries[idx]
        if is_dir:
            return os.path.join(OUTPUT_DIR, name)
        return os.path.join(OUTPUT_DIR, name)
    return None


def _pick_persona() -> str | None:
    os.makedirs(PERSONAS_DIR, exist_ok=True)
    available = [(p, os.path.exists(os.path.join(PERSONAS_DIR, f"{p}.md")))
                 for p in AVAILABLE_PERSONAS]

    print()
    print("  -- Personas ------------------------------------------")
    for i, (p, exists) in enumerate(available, 1):
        status = "" if exists else "  [not created yet]"
        print(f"  [{i}] {p}{status}")
    print()

    raw = input("  Pick persona (Enter = cancel): ").strip()
    if not raw or not raw.isdigit():
        return None
    idx = int(raw) - 1
    if 0 <= idx < len(available):
        p, exists = available[idx]
        return p
        return p
    return None


# -- Main pipeline ----------------------------------------------------------

def rewrite_article(filepath: str, persona: str, provider_key: str,
                    dry_run: bool = False) -> str | None:
    print(f"\n  -- Rewrite: {os.path.basename(filepath)} ------------------")
    print(f"  Persona : {persona}")
    print(f"  Provider: {PROVIDERS.get(provider_key, {}).get('label', provider_key)}")

    draft = _load_draft(filepath)
    if not draft["body"]:
        print("  [X] Empty draft body.")
        return None
    print(f"  Draft   : {len(draft['body'])} chars | Topic: {draft['topic'][:60]}")

    persona_text = _load_persona(persona)
    # Single RAG call -- result used for both fallback and prompt context
    # category passed so games/tech/ai chunks are included (#5)
    persona_rag = _retrieve_persona_rag(
        persona       = persona,
        topic         = draft["topic"],
        article_focus = draft["focus"],
        category      = draft.get("category", "other"),
    )
    if not persona_text:
        if persona_rag:
            persona_text = persona_rag
        else:
            print("  [persona] No .md file and no Qdrant chunks found.")
            return None
    print(f"  Persona : {len(persona_text)} chars loaded")

    # Resolve length: schema length_{short/medium/long} > config ARTICLE_LENGTH fallback
    _article_length = draft.get("article_length", "medium")
    if _article_length not in ("short", "medium", "long"):
        _article_length = "medium"
    _length_instruction = "1000-1400 words"  # hardcoded fallback
    _article_type = draft.get("article_type", "")
    if _article_type:
        try:
            import json as _lj, os as _lo
            _schema_path = _lo.path.join(BASE_DIR, "data", "schemas", f"{_article_type}.json")
            if _lo.path.exists(_schema_path):
                _schema_data = _lj.loads(open(_schema_path, encoding="utf-8").read())
                _length_key  = f"length_{_article_length}"
                _length_instruction = (
                    _schema_data.get(_length_key)
                    or _schema_data.get("length_medium", "1000-1400 words")
                )
        except Exception:
            pass
    if _length_instruction == "1000-1400 words":
        # fallback: config ARTICLE_LENGTH per section
        try:
            from config import ARTICLE_LENGTH as _AL
            _section_key = draft.get("section", "DATA").lower()
            _cfg_len = _AL.get(_section_key, "")
            if _cfg_len:
                if "to" in _cfg_len and "word" not in _cfg_len:
                    _cfg_len = _cfg_len.replace(" to ", "-") + " words"
                _length_instruction = _cfg_len
        except Exception:
            pass

    prompt = _build_rewrite_prompt(
        draft               = draft,
        persona_text        = persona_text,
        persona_rag         = persona_rag,
        persona_name        = persona,
        length_instruction  = _length_instruction,
    )
    print(f"  Prompt  : {len(prompt)} chars (~{len(prompt)//4} tokens estimated)")

    api_result = _call_api(prompt, provider_key, dry_run=dry_run)

    if dry_run or not api_result["text"]:
        return None

    parsed = _parse_rewrite(api_result["text"])
    if not parsed["body"]:
        print("  [X] Empty rewrite body.")
        return None

    title_lens   = [len(t) for t in parsed["titles"]]
    excerpt_lens = [len(e) for e in parsed["excerpts"]]
    print(f"\n  Rewrite : {len(parsed['body'])} chars")
    print(f"  Parsed  : {len(parsed['titles'])} titles {title_lens}  "
          f"{len(parsed['excerpts'])} excerpts {excerpt_lens}  "
          f"{len(parsed.get('tags', []))} tags")

    output_path = _save_rewrite(filepath, draft, parsed, persona, api_result)
    print(f"\n  [OK] Saved : {output_path}")

    _log_token_usage(filepath, persona, api_result, output_path)

    return output_path


def main():
    parser = argparse.ArgumentParser(
        prog        = "claude_rewrite.py",
        description = "Rewrite Article Agent drafts via API with persona system",
    )
    parser.add_argument("--file",     help="Path to draft directory or .md file")
    parser.add_argument("--persona",  choices=AVAILABLE_PERSONAS)
    parser.add_argument("--provider", choices=list(PROVIDERS.keys()),
                        help="API provider (claude/gemini/groq/deepseek)")
    parser.add_argument("--dry-run",  action="store_true")
    parser.add_argument("--stats",    action="store_true")
    args = parser.parse_args()

    if args.stats:
        _show_cumulative_cost()
        return

    filepath = args.file or _pick_draft_file()
    if not filepath:
        print("  Cancelled.")
        return
    if not os.path.exists(filepath):
        print(f"  File not found: {filepath}")
        sys.exit(1)

    persona = args.persona or _pick_persona()
    if not persona:
        print("  Cancelled.")
        return

    provider_key = args.provider or _pick_provider()

    result = rewrite_article(filepath, persona, provider_key, dry_run=args.dry_run)

    if result:
        print(f"\n  [OK] Done: {result}")
    elif not args.dry_run:
        print("\n  [X] Rewrite failed.")
        sys.exit(1)


if __name__ == "__main__":
    main()
