# pipeline/discovery_run.py -- Discovery phase
#
# Three modes:
#   A) Query mode  -- SearXNG + sources -> filter -> select -> queue
#   B) URL mode    -- paste URLs directly -> title -> queue
#   C) Upgrade mode -- fetch existing article -> improve with fresh research

import re
import requests



def _input(prompt: str):
    """input() wrapper -- returns None on q to go back to menu."""
    val = input(prompt).strip()
    if val.lower() in ('q', 'quit', 'exit'):
        return None
    return val

def _detect_lang(text: str) -> str:
    try:
        from langdetect import detect
        return detect(text)
    except Exception:
        return "en"


def _ensure_english_topic(title: str) -> str:
    lang = _detect_lang(title)
    if lang == "en":
        return title
    print(f"\n  !  Title language detected: [{lang}]")
    print(f"  Title: {title[:80]}")
    print(f"  Enter an English topic (used as slug + article subject):")
    custom = input("  English topic: ").strip()
    if custom:
        return custom
    print("  !  Keeping original title (slug will be non-English).")
    return title

def _suggest_topics(raw_title: str, url: str = "") -> str:
    """
    Generate 10 topic name suggestions via LLM, let user pick one.
    Returns chosen topic string.
    """
    import requests as _req
    from config import OLLAMA_URL, MODELS

    context = raw_title
    if url:
        try:
            r = _req.get(url, timeout=6, headers={
                "User-Agent": "Mozilla/5.0 (compatible; ArticleAgent/1.0)"
            })
            import trafilatura as _tf
            text = _tf.extract(r.text, favor_precision=True) or ""
            if text:
                context = f"{raw_title}\n\n{text[:600]}"
        except Exception:
            pass

    suggestions = []
    try:
        model_name = MODELS.get("DEV", {}).get("name", "qwen2.5:7b")
        prompt = (
            f"You are an SEO editor for a tech/gaming blog.\n"
            f"Based on this article, generate exactly 10 topic name variants.\n"
            f"Rules:\n"
            f"- Always in English\n"
            f"- Each topic: 5-12 words, factual, no clickbait\n"
            f"- Include key proper nouns (game name, company, product)\n"
            f"- Vary the angle: release date, comparison, feature, context\n"
            f"- No question marks, no 'How to', no 'Review'\n"
            f"- Output exactly 10 lines, no numbers, no bullets\n\n"
            f"Article:\n{context[:800]}"
        )
        r = _req.post(
            f"{OLLAMA_URL}/api/chat",
            json={
                "model":    model_name,
                "think":    False,
                "messages": [{"role": "user", "content": prompt}],
                "stream":   False,
                "options":  {"temperature": 0.7},
            },
            timeout=30,
        )
        r.raise_for_status()
        response = r.json().get("message", {}).get("content", "").strip()
        if "</think>" in response:
            response = response.split("</think>")[-1].strip()
        for line in response.split("\n"):
            line = line.strip().strip("--*0123456789.)").strip()
            if line and len(line) > 10:
                suggestions.append(line[:120])
            if len(suggestions) == 10:
                break
    except Exception:
        pass

    print()
    print("  +== TOPIC ===================================================+")
    if suggestions:
        for i, s in enumerate(suggestions, 1):
            print(f"  |  [{i:>2}] {s[:60]}")
        print(f"  |   [0] Write custom topic")
    else:
        print(f"  |  (Could not generate suggestions)")
        print(f"  |  Raw title: {raw_title[:60]}")
        print(f"  |   [0] Write custom topic")
    print("  +============================================================+")
    print()

    while True:
        raw = input("  Pick topic [1-10] or [0] custom: ").strip()
        if raw == "0" or raw == "":
            custom = input("  Custom topic: ").strip()
            if custom:
                return custom
            return raw_title
        if raw.isdigit() and suggestions:
            idx = int(raw)
            if 1 <= idx <= len(suggestions):
                return suggestions[idx - 1]
        print("  Invalid choice.")



def _normalize_topic(title: str) -> str:
    """Strip clickbait patterns, questions, source names from article titles."""
    title = re.sub(r"\s*[\|\-\u2013\u2014]\s*[A-Z][^|\-]{3,30}$", "", title).strip()
    title = re.sub(
        r"^(will|is|are|can|should|what|why|how|does|could|would|has|have)\s+",
        "", title, flags=re.IGNORECASE
    ).strip()
    title = re.sub(
        r",?\s*(will it|does it|find out|here's why|you won't believe|"
        r"everything you need|what you need to know|here's what|"
        r"this is why|and it's|but there's).*$",
        "", title, flags=re.IGNORECASE
    ).strip()
    title = title.strip(" .,?!:")
    if len(title) > 100:
        title = title[:100].rsplit(" ", 1)[0]
    return title if title else title


from discovery.sources   import fetch_all
from discovery.filter    import filter_and_rank
from discovery.selector  import select_topics, ask_persona_and_model
from taxonomy.categories import CATEGORIES, detect_category
import core.queue as queue
from config import SECTIONS, MODELS, DEFAULT_MODEL, DEFAULT_SECTION, PERSONAS, DEFAULT_PERSONA


def _pick_section():
    print("  -- Site section ---------------------------------------------")
    for k, v in SECTIONS.items():
        marker = " <-" if v == DEFAULT_SECTION else ""
        print(f"  [{k}] {v}{marker}")
    raw = _input(f"  Section (Enter = {DEFAULT_SECTION}): ")
    if raw is None:
        return None
    return SECTIONS.get(raw.strip(), DEFAULT_SECTION)


def _pick_category(section: str) -> str:
    from config import RESEARCH_CATEGORIES_DATA, RESEARCH_CATEGORIES_PORTFOLIO
    cats = RESEARCH_CATEGORIES_PORTFOLIO if section == "PORTFOLIO" else RESEARCH_CATEGORIES_DATA
    print("  -- Category -------------------------------------------------")
    for i, cat in enumerate(cats, 1):
        print(f"  [{i}] {cat}")
    print("  [Enter] -> other")
    while True:
        raw = _input(f"  Category (1-{len(cats)}): ").strip()
        if raw == "":
            return "other"
        try:
            idx = int(raw) - 1
            if 0 <= idx < len(cats):
                return cats[idx]
        except (ValueError, TypeError):
            pass
        print(f"  Enter a number from 1 to {len(cats)}.")


def _build_query(category: str) -> tuple:
    from datetime import datetime
    year = datetime.now().year
    default_queries = {
        "games":            f"trending games news {year}",
        "ai-data":          f"artificial intelligence machine learning news {year}",
        "hardware":         f"CPU GPU hardware tech news {year}",
        "software":         f"software development tools programming {year}",
        "security":         f"cybersecurity hacking vulnerabilities {year}",
        "entertainment":    f"movies series streaming entertainment {year}",
        "photography":      f"photography tips techniques {year}",
        "drone":            f"drone photography DJI aerial {year}",
        "portrait-studio":  f"portrait studio photography lighting {year}",
        "macro":            f"macro photography tips insects flowers {year}",
        "portrait-outdoor": f"outdoor portrait photography {year}",
        "product":          f"product photography commercial {year}",
        "travel":           f"travel photography destinations {year}",
        "3d":               f"3D rendering blender visualization {year}",
        "3d-exterior":      f"3D exterior architectural visualization {year}",
        "3d-interior":      f"3D interior design rendering {year}",
        "ai":               f"AI art generative image stable diffusion {year}",
        "other":            "",
    }
    suggestion = default_queries.get(category, "")
    print("  -- Discovery query -------------------------------------------")
    if suggestion:
        print(f"  Suggestion: {suggestion}")
    raw   = _input("  Query (Enter = suggestion): ").strip()
    query = raw if raw else suggestion

    from config import DISCOVERY_TOP_N, DISCOVERY_TOP_N_MAX
    raw_n = _input(f"  Show how many results? (Enter = {DISCOVERY_TOP_N}, max {DISCOVERY_TOP_N_MAX}): ").strip()
    try:
        top_n = min(int(raw_n), DISCOVERY_TOP_N_MAX)
    except (ValueError, TypeError):
        top_n = DISCOVERY_TOP_N

    return query, top_n


def _is_url(text: str) -> bool:
    return bool(re.match(r"https?://", text.strip()))


def _parse_urls(raw: str) -> list:
    parts = [p.strip() for p in raw.split(",")]
    return [p for p in parts if _is_url(p)]


_WEAK_TITLE_DOMAINS = {
    "reddit.com", "twitter.com", "x.com", "facebook.com",
    "youtube.com", "t.co", "bit.ly",
}

_WEAK_TITLES = {
    "reddit", "twitter", "x", "facebook", "youtube",
    "home", "index", "page", "untitled", "loading",
}


def _llm_generate_topic(text: str, url: str) -> str:
    try:
        from config import OLLAMA_EMBED_URL
        prompt = (
            f"Based on this article text, generate a concise English article topic "
            f"(max 80 characters, no quotes, no punctuation at end):\n\n"
            f"{text[:800]}"
        )
        r = requests.post(
            f"{OLLAMA_EMBED_URL}/api/generate",
            json={
                "model":   "qwen2.5:7b",
                "prompt":  prompt,
                "stream":  False,
                "options": {"temperature": 0.3, "num_predict": 60},
            },
            timeout=20,
        )
        r.raise_for_status()
        topic = r.json().get("response", "").strip().strip('"\'`').strip()
        if topic and len(topic) > 10:
            return topic[:100]
    except Exception:
        pass
    return ""


def _fetch_title(url: str) -> str:
    from urllib.parse import urlparse as _up
    domain = _up(url).netloc.removeprefix("www.")

    page_text  = ""
    html_title = ""

    try:
        r = requests.get(url, timeout=8, headers={
            "User-Agent": "Mozilla/5.0 (compatible; ArticleAgent/1.0)"
        })
        r.raise_for_status()
        match = re.search(r"<title[^>]*>(.*?)</title>", r.text, re.IGNORECASE | re.DOTALL)
        if match:
            from html import unescape
            html_title = unescape(re.sub(r"\s+", " ", match.group(1)).strip())
            html_title = re.sub(r"\s*[|\-\u2013\u2014]\s*[^|\-\u2013\u2014]{3,40}$",
                                "", html_title).strip()
        try:
            import trafilatura
            page_text = trafilatura.extract(r.text, include_comments=False,
                                             favor_precision=True) or ""
            page_text = page_text[:1000]
        except Exception:
            pass
    except Exception:
        pass

    title_lower = html_title.lower().strip()
    is_weak = (
        not html_title
        or title_lower in _WEAK_TITLES
        or any(domain.endswith(d) for d in _WEAK_TITLE_DOMAINS)
        or len(html_title) < 15
    )

    if not is_weak:
        return html_title[:200]

    text_for_llm = page_text or html_title
    if text_for_llm:
        print(f"     -> Weak title (\"{html_title[:30]}\") -- generating topic via LLM...")
        llm_topic = _llm_generate_topic(text_for_llm, url)
        if llm_topic:
            return llm_topic

    path = url.rstrip("/").split("/")[-1]
    return re.sub(r"[-_]", " ", path)[:100].title()


# _ask_article_focus removed -- moved to queue.py
def _ask_slug(topic: str) -> str:
    """Ask user for article slug. Auto-suggests from topic. No external imports."""
    import re as _re
    suggestion = _re.sub(r"[^a-z0-9]+", "-", topic.lower()).strip("-")[:60]
    print(f"  Slug (used as URL path and knowledge base key)")
    print(f"  Suggestion: {suggestion}")
    raw = _input(f"  Slug (Enter = use suggestion): ").strip()
    slug = raw if raw else suggestion
    slug = _re.sub(r"[^a-z0-9-]+", "-", slug.lower()).strip("-")[:80]
    print(f"  -> Slug: {slug}")
    return slug

# _ask_focus_simple removed -- moved to queue.py
# _suggest_focus removed -- moved to queue.py
def _assess_results(filtered: list, query: str, category: str = "other") -> dict:
    """Assess quality of discovery results. Measures coverage and relevance -- not press ratio.
    Press ratio is irrelevant for niche/long-tail content strategy."""
    if not filtered:
        return {"score": 0.0, "reason": "no results"}

    total = len(filtered)

    # Coverage -- how many results found
    coverage_score = min(total / 20.0, 1.0)

    # Relevance -- are top results actually about the topic
    top_score  = filtered[0].get("score", 0) if filtered else 0
    top_n_avg  = sum(r.get("score", 0) for r in filtered[:5]) / max(min(total, 5), 1)
    relevance_score = min(top_n_avg / 12.0, 1.0)

    # Diversity -- multiple domains better than one source dominating
    domains = set()
    for r in filtered[:20]:
        url = r.get("url", "")
        if url:
            try:
                from urllib.parse import urlparse
                domains.add(urlparse(url).netloc)
            except Exception:
                pass
    diversity_score = min(len(domains) / 5.0, 1.0)

    # Coverage 40% + relevance 40% + diversity 20%
    score = (coverage_score * 0.4) + (relevance_score * 0.4) + (diversity_score * 0.2)

    if score >= 0.5:
        reason = "good coverage"
    elif total < 5:
        reason = "too few results -- topic too narrow or unknown"
    elif top_score < 5.0:
        reason = "low relevance -- query off-target"
    else:
        reason = "mediocre coverage"

    return {"score": round(score, 2), "reason": reason}


def _refine_query(original_query: str, top_results: list, reason: str) -> str:
    """Use qwen3.5:27b to generate a better search query."""
    try:
        from config import OLLAMA_URL, MODELS
        titles = [r.get("title", "")[:80] for r in top_results[:5] if r.get("title")]
        titles_str = "\n".join(f"- {t}" for t in titles)
        model_name = MODELS.get("NORMAL", {}).get("name", "qwen3.5:27b")

        prompt = (
            f"You are a search query optimizer. Today is 2026.\n"
            f"Original query: {original_query}\n"
            f"Problem: {reason}\n"
            f"Top results were:\n{titles_str}\n\n"
            f"Generate ONE better search query that is more specific and will find "
            f"more relevant results. Focus on the topic itself -- do NOT add mainstream "
            f"publication names like IGN, GameSpot, PC Gamer. Use year 2026 if relevant. "
            f"Do NOT add old years like 2023 or 2024 to recent topics. "
            f"Output ONLY the query, nothing else, max 10 words."
        )

        r = requests.post(
            f"{OLLAMA_URL}/api/chat",
            json={
                "model":    model_name,
                "think":    False,
                "messages": [{"role": "user", "content": prompt}],
                "stream":   False,
                "options":  {"temperature": 0.3},
            },
            timeout=30,
        )
        r.raise_for_status()
        response = r.json().get("message", {}).get("content", "").strip()
        if "</think>" in response:
            response = response.split("</think>")[-1].strip()
        new_query = response.strip().strip('"\'').strip()
        if new_query and len(new_query) > 5:
            return new_query[:120]
    except Exception:
        pass
    return original_query


def _build_queue_item(
    topic, category, persona, section, model,
    seed_urls=None, seed_queries=None,
    article_focus="", topic_slug="", topic_tags=None,
    upgrade_url="", upgrade_mode="",
    translate=False, article_lang="en",
):
    """Central queue item builder -- single place to add new fields."""
    return queue.add_item(
        topic         = topic,
        category      = category,
        persona       = persona,
        section       = section,
        model         = model,
        seed_urls     = seed_urls     or [],
        seed_queries  = seed_queries  or [],
        article_focus = article_focus,
        topic_slug    = topic_slug    or "",
        topic_tags    = topic_tags    or [],
        upgrade_url   = upgrade_url   or "",
        upgrade_mode  = upgrade_mode  or "",
        translate     = translate,
        article_lang  = article_lang,
    )


def _run_query_mode(query, category, section, persona_key, model_key,
                    top_n=None, article_focus="") -> int:

    MAX_ITERATIONS = 3
    QUALITY_THRESHOLD = 0.25
    current_query = query
    filtered = []

    for iteration in range(MAX_ITERATIONS):
        if iteration > 0:
            print(f"\n  -- Iteration {iteration + 1}/{MAX_ITERATIONS} -----------------------------")

        print(f"\n  -> Fetching results for: \"{current_query}\" [{category}]")
        raw_items = fetch_all(current_query, category=category, deep=True)
        print(f"  -> Total raw results: {len(raw_items)}")

        filtered = filter_and_rank(raw_items, current_query, top_n=top_n)
        print(f"  -> After filtering: {len(filtered)} topics to choose from")

        if not filtered:
            print("  No results after filtering.")
            if iteration < MAX_ITERATIONS - 1:
                current_query = _refine_query(current_query, [], "no results found")
                continue
            return 0

        quality = _assess_results(filtered, current_query, category=category)
        print(f"  -> Quality score: {quality['score']:.2f} -- {quality['reason']}")

        if quality["score"] >= QUALITY_THRESHOLD or iteration == MAX_ITERATIONS - 1:
            break

        # Refine query for next iteration
        new_query = _refine_query(current_query, filtered[:5], quality["reason"])
        if new_query == current_query:
            print("  -> Query unchanged -- stopping iteration")
            break
        print(f"  -> Refined query: \"{new_query}\"")
        current_query = new_query

    print()
    selected = select_topics(filtered)
    if not selected:
        return 0

    merge = False
    if len(selected) > 1:
        print(f"  You selected {len(selected)} topics.")
        print(f"  [1] Merge into ONE article (combined research + single generate)")
        print(f"  [2] Separate articles -- {len(selected)} items in queue")
        raw   = _input("  Choice (Enter = 1): ").strip()
        merge = raw != "2"

    added = 0

    # TOPIC picker -- let user rename/refine the topic
    if merge and len(selected) > 1:
        _original_titles = list(dict.fromkeys(s.get("title", "") for s in selected if s.get("title")))
        _picked_title = _suggest_topics(selected[0].get("title", ""), selected[0].get("url", ""))
        for s in selected:
            s["title"] = _picked_title
    else:
        for s in selected:
            s["title"] = _suggest_topics(s.get("title", ""), s.get("url", ""))

    # FOCUS -- clean input, no suggestions
    _focus = article_focus
    if not _focus:
        print()
    # Focus is set post-research in queue.py focus picker
    _focus = ""

    # Topic slug
    _primary_title_q = selected[0].get("title", "") if selected else ""
    _topic_slug  = _ask_slug(_primary_title_q)
    _topic_tags  = []

    if merge and len(selected) > 1:
        combined_topic = _normalize_topic(selected[0]["title"])
        all_titles     = _original_titles
        all_urls       = [item.get("url", "") for item in selected if item.get("url")]
        detected_cat   = detect_category(combined_topic) if category == "other" else category
        result = _build_queue_item(
            topic         = combined_topic,
            category      = detected_cat,
            persona       = persona_key,
            section       = section,
            model         = model_key,
            seed_urls     = all_urls,
            seed_queries  = all_titles,
            topic_slug    = _topic_slug,
            topic_tags    = _topic_tags,
        )
        if result.get("status") == queue.STATUS_PENDING:
            added = 1
            print(f"  + [{result['id']}] {combined_topic[:65]}")
            if _topic_slug:
                print(f"     Slug:  {_topic_slug}")
            print(f"     Seed queries: {len(all_titles)}")
    else:
        for item in selected:
            topic        = _normalize_topic(item["title"])
            detected_cat = detect_category(topic) if category == "other" else category
            result = _build_queue_item(
                topic         = topic,
                category      = detected_cat,
                persona       = persona_key,
                section       = section,
                model         = model_key,
                seed_urls     = [item["url"]] if item.get("url") else [],
                topic_slug    = _topic_slug,
                topic_tags    = _topic_tags,
            )
            if result.get("status") == queue.STATUS_PENDING:
                added += 1
                print(f"  + [{result['id']}] {topic[:65]}")
                if _topic_slug:
                    print(f"     Slug:  {_topic_slug}")

    return added


# -- Dodatkowa funkcja walidacji URL ----------------------------------------

def _validate_seed_url(url: str, category: str = "") -> bool:
    """Warn if seed URL domain is unverified or blocked. Offer to add to trusted."""
    from urllib.parse import urlparse
    from domain_config import is_blocked, get_domain_trust, reload_all

    if is_blocked(url):
        print(f"  X Blocked domain -- skipping: {url[:60]}")
        return False

    trust = get_domain_trust(url)
    if trust.get("domain_trust") == "unknown":
        domain = urlparse(url).netloc.lower().removeprefix("www.")
        print(f"  ! Unverified domain: {domain}")
        print(f"  [y] add URL only  [t] add URL + add to trusted  [n] skip")
        confirm = _input("  > ").strip().lower()
        if confirm == "t":
            _add_domain_to_trusted(domain, category)
            reload_all()
            return True
        return confirm in ("y", "yes")

    return True


def _add_domain_to_trusted(domain: str, category: str = "") -> None:
    """Add domain to domains_trusted.json interactively."""
    import json as _json, os as _os

    trusted_path = _os.path.join(
        _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))),
        "data", "domains_trusted.json"
    )
    try:
        with open(trusted_path, encoding="utf-8") as f:
            data = _json.load(f)
    except Exception as e:
        print(f"  Error loading trusted domains: {e}")
        return

    cats = [k for k in data.keys() if not k.startswith("_")]

    tiers = ["press", "trusted", "community"]
    print()
    print(f"  Adding: {domain}")
    print(f"  Tier:")
    for i, t in enumerate(tiers, 1):
        print(f"  [{i}] {t}")
    raw_t = _input("  > ").strip()
    if not raw_t.isdigit() or not (1 <= int(raw_t) <= len(tiers)):
        tier = "press"
    else:
        tier = tiers[int(raw_t) - 1]

    boost_defaults = {"press": 0.78, "trusted": 0.85, "community": 0.60}
    default_boost  = boost_defaults.get(tier, 0.75)
    raw_b = _input(f"  Boost (Enter = {default_boost}): ").strip()
    try:
        boost = round(max(0.0, min(1.0, float(raw_b))), 2) if raw_b else default_boost
    except ValueError:
        boost = default_boost

    if not category or category not in cats:
        print(f"  Category:")
        for i, c in enumerate(cats, 1):
            print(f"  [{i}] {c}")
        raw_c = _input("  > ").strip()
        if raw_c.isdigit() and 1 <= int(raw_c) <= len(cats):
            category = cats[int(raw_c) - 1]
        else:
            category = "global"

    if category not in data:
        data[category] = {}
    data[category][domain] = {"tier": tier, "boost": boost}

    with open(trusted_path, "w", encoding="utf-8") as f:
        _json.dump(data, f, ensure_ascii=False, indent=2)

    print(f"  OK Added: {domain} -> [{category}] tier={tier} boost={boost}")


# -- URL mode ---------------------------------------------------------------

def _run_url_mode(urls, category, section, persona_key, model_key) -> int:
    print(f"\n  -> Processing {len(urls)} URL(s)...")
    added = 0

    items = []
    for url in urls:
        if not _validate_seed_url(url, category=category):
            continue
        print(f"\n  -> Fetching title: {url[:70]}")
        title = _fetch_title(url)
        title = _normalize_topic(title)
        print(f"     Raw title : {title[:65]}")
        items.append({"url": url, "title": title})

    if not items:
        print("  No valid URLs after validation.")
        return 0

    merge = False
    if len(items) > 1:
        print(f"\n  You have {len(items)} URLs.")
        print(f"  [1] Merge into ONE article (combined research)")
        print(f"  [2] Separate articles -- {len(items)} items in queue")
        raw   = _input("  Choice (Enter = 1): ").strip()
        merge = raw != "2"

    # Pick topic
    if merge or len(items) == 1:
        _url_original_titles = list(dict.fromkeys(i.get("title", "") for i in items if i.get("title")))
        title = _suggest_topics(items[0]["title"], items[0]["url"])
        for item in items:
            item["title"] = title
    else:
        for item in items:
            item["title"] = _suggest_topics(item["title"], item["url"])

    # Focus and schema set post-research in queue.py
    focus = ""

    # Topic slug
    _primary_title = items[0]["title"] if items else ""
    _topic_slug  = _ask_slug(_primary_title)
    _topic_tags  = []
    # Schema picker removed
    if merge and len(items) > 1:
        primary_title = items[0]["title"]
        detected_cat = detect_category(primary_title) if category == "other" else category
        result = _build_queue_item(
            topic         = primary_title,
            category      = detected_cat,
            persona       = persona_key,
            section       = section,
            model         = model_key,
            seed_urls     = [i["url"] for i in items],
            seed_queries  = _url_original_titles,
            article_focus = focus,
            topic_slug    = _topic_slug,
            topic_tags    = _topic_tags,
        )
        if result.get("status") == queue.STATUS_PENDING:
            added = 1
            print(f"  + [{result['id']}] {primary_title[:65]}")
            if _topic_slug:
                print(f"     Slug:  {_topic_slug}")
    else:
        for item in items:
            detected_cat = detect_category(item["title"]) if category == "other" else category
            result = _build_queue_item(
                topic         = item["title"],
                category      = detected_cat,
                persona       = persona_key,
                section       = section,
                model         = model_key,
                seed_urls     = [item["url"]],
                topic_slug    = _topic_slug,
                topic_tags    = _topic_tags,
            )
            if result.get("status") == queue.STATUS_PENDING:
                added += 1
                print(f"  + [{result['id']}] {item['title'][:65]}")
                if _topic_slug:
                    print(f"     Slug:  {_topic_slug}")

    return added


# -- Upgrade mode -----------------------------------------------------------

def _run_upgrade_mode(urls, category, section, persona_key, model_key) -> int:
    print(f"\n  -> Upgrade mode: {len(urls)} article(s) to improve...")
    added = 0

    for url in urls:
        if not _validate_seed_url(url):
            continue
        print(f"\n  -> Fetching existing article: {url[:70]}")
        title = _fetch_title(url)
        title = _normalize_topic(title)
        print(f"     Title: {title[:65]}")
        title = _ensure_english_topic(title)

        print("  -> Fetching article content...")
        existing = _fetch_existing_content(url)
        if existing:
            print(f"     Content: {len(existing)} chars fetched OK")
        else:
            print("     !  Could not fetch content -- will upgrade topic only")

        print("\n  -- Upgrade type -----------------------------------------")
        print("  [1] Upgrade  -- full rewrite, significantly better (default)")
        print("  [2] Expand   -- keep structure, add new sections")
        print("  [3] Refresh  -- update facts/dates only, minimal changes")
        raw          = _input("  Mode (Enter = 1): ").strip()
        mode_map     = {"1": "upgrade", "2": "expand", "3": "refresh"}
        upgrade_mode = mode_map.get(raw, "upgrade")

        print()
        print("  +== FOCUS ====================================================+")
        print("  |  One sentence thesis -- 'X proves Y because Z'              |")
        print("  |  The LLM builds the entire article around this sentence.   |")
        print("  |  Leave empty to skip.                                      |")
        print("  +============================================================+")
        print()
        focus = _ask_focus_simple()

        # Topic slug
        _topic_slug  = _ask_slug(title)
        _topic_tags  = []

        detected_cat = detect_category(title) if category == "other" else category
        result       = _build_queue_item(
            topic         = title,
            category      = detected_cat,
            persona       = persona_key,
            section       = section,
            model         = model_key,
            seed_urls     = [url],
            upgrade_url   = url,
            upgrade_mode  = upgrade_mode,
            topic_slug    = _topic_slug,
            topic_tags    = _topic_tags,
        )

        if result.get("status") == queue.STATUS_PENDING and existing:
            q = queue.load()
            for item in q["items"]:
                if item["id"] == result["id"]:
                    item["existing_content"] = existing
                    break
            queue.save(q)

        if result.get("status") == queue.STATUS_PENDING:
            added += 1
            mode_label = {"upgrade": "UPGRADE", "expand": "EXPAND", "refresh": "REFRESH"}
            print(f"  + [{result['id']}] [{mode_label[upgrade_mode]}] {title[:55]}")

    return added


def _fetch_existing_content(url: str) -> str:
    try:
        import trafilatura
        downloaded = trafilatura.fetch_url(url)
        if downloaded:
            text = trafilatura.extract(downloaded, include_comments=False,
                                       include_tables=True, no_fallback=False)
            if text and len(text.strip()) > 100:
                return text.strip()[:8000]
    except Exception:
        pass
    try:
        r = requests.get(url, timeout=10, headers={
            "User-Agent": "Mozilla/5.0 (compatible; ArticleAgent/1.0)"
        })
        r.raise_for_status()
        text = re.sub(r"<[^>]+>", " ", r.text)
        text = re.sub(r"\s+", " ", text).strip()
        return text[:8000] if len(text) > 100 else ""
    except Exception:
        return ""


# -- RSS discover -----------------------------------------------------------

def _rss_discover(section: str) -> int:
    """Browse recent RSS items from rss_feed Qdrant collections -> select -> queue."""
    from config import QDRANT_URL, RESEARCH_CATEGORIES_DATA, RESEARCH_CATEGORIES_PORTFOLIO

    print("\n  -- RSS -- przeglądaj nowe artykuły -----------------------")

    # Wybór języka / kolekcji
    print("  Język:")
    print("  [1] English  (rss_feed)      -- domyślny, trafia do RAG")
    print("  [2] Polski   (rss_feed_pl)   -- tylko przeglądanie")
    print("  [3] Norski   (rss_feed_nor)  -- tylko przeglądanie")
    print("  [Enter] English")
    lang_raw = _input("  > ").strip()
    lang_map = {"1": "rss_feed", "2": "rss_feed_pl", "3": "rss_feed_nor"}
    rss_collection = lang_map.get(lang_raw, "rss_feed")

    # Wybór kategorii
    cats = RESEARCH_CATEGORIES_DATA + RESEARCH_CATEGORIES_PORTFOLIO
    print("\n  Kategoria (Enter = wszystkie):")
    for i, c in enumerate(cats, 1):
        print(f"  [{i:>2}] {c}")
    raw_cat = _input("  > ").strip()
    if raw_cat.isdigit() and 1 <= int(raw_cat) <= len(cats):
        filter_cat = cats[int(raw_cat) - 1]
    elif raw_cat:
        filter_cat = raw_cat
    else:
        filter_cat = None

    # Pobierz artykuły z wybranej kolekcji
    try:
        import requests as _rq
        scroll_filter = {}
        if filter_cat:
            scroll_filter = {
                "filter": {
                    "must": [{"key": "category", "match": {"value": filter_cat}}]
                }
            }

        r = _rq.post(
            f"{QDRANT_URL}/collections/{rss_collection}/points/scroll",
            json={
                **scroll_filter,
                "limit":        500,
                "with_payload": True,
            },
            timeout=15,
        )
        if r.status_code != 200:
            print(f"  X Qdrant error: {r.status_code}")
            if r.status_code == 404:
                print(f"  Kolekcja '{rss_collection}' nie istnieje. Najpierw zfetchuj RSS: [F] -> [2]")
            return 0

        points = r.json().get("result", {}).get("points", [])
    except Exception as e:
        print(f"  X RSS fetch error: {e}")
        return 0

    if not points:
        print(f"  Brak artykułów w '{rss_collection}'. Najpierw zfetchuj RSS: [F] -> [2]")
        return 0

    # Deduplikuj po URL
    seen_urls = {}
    for p in points:
        url    = p["payload"].get("url", "")
        title  = p["payload"].get("title", "")
        cat    = p["payload"].get("category", "")
        date   = p["payload"].get("content_date", "")[:10]
        domain = p["payload"].get("domain", "")
        if url and url not in seen_urls:
            seen_urls[url] = {
                "title":    title,
                "url":      url,
                "category": cat,
                "date":     date,
                "domain":   domain,
            }

    items = list(seen_urls.values())[:200]

    if not items:
        print("  Brak unikalnych artykułów.")
        return 0

    # Grupuj po kategorii
    by_cat: dict = {}
    for item in items:
        c = item["category"]
        by_cat.setdefault(c, []).append(item)

    lang_label = {"rss_feed": "EN", "rss_feed_pl": "PL", "rss_feed_nor": "NOR"}.get(rss_collection, "")
    print(f"\n  [{lang_label}] Znaleziono {len(items)} artykułów:\n")
    idx = 1
    num_to_item = {}
    for cat, cat_items in sorted(by_cat.items()):
        print(f"  [{cat}]")
        for item in cat_items[:200]:
            domain = item.get("domain", "")[:20]
            print(f"  [{idx:>3}] {item['title'][:60]:<60}  {item['date']}  [{domain}]")
            num_to_item[idx] = item
            idx += 1
        print()

    print("  Select: number (1), range (1-5), list (1,3,5) | Enter=cancel")
    sel = _input("  > ").strip().lower()
    if not sel:
        return 0

    indices = []
    try:
        if "-" in sel and "," not in sel:
            parts = sel.split("-")
            start, end = int(parts[0]), int(parts[1])
            indices = list(range(start, end + 1))
        elif "," in sel:
            indices = [int(x.strip()) for x in sel.split(",")]
        else:
            indices = [int(sel)]
    except ValueError:
        print("  Invalid selection.")
        return 0

    selected = [num_to_item[i] for i in indices if i in num_to_item]
    if not selected:
        print("  Nothing selected.")
        return 0

    focus = _ask_article_focus(
        seed_url   = selected[0]["url"] if selected else "",
        seed_title = selected[0]["title"] if selected else "",
    )

    persona_key, model_key = ask_persona_and_model(
        default_persona = DEFAULT_PERSONA,
        default_model   = DEFAULT_MODEL,
    )
    model_name = MODELS.get(model_key, {}).get("name", model_key)

    added = 0
    for item in selected:
        if not _validate_seed_url(item["url"]):
            continue
        topic = _normalize_topic(item["title"])
        topic = _ensure_english_topic(topic)
        result = _build_queue_item(
            topic         = topic,
            category      = item["category"],
            persona       = persona_key,
            section       = section,
            model         = model_name,
            seed_urls     = [item["url"]],
        )
        if result.get("status") == queue.STATUS_PENDING:
            added += 1
            print(f"  + [{result['id']}] {topic[:65]}")

    if added:
        print(f"\n  -> {added} topic(s) added to queue.")
        go = _input("  [R] Research now  |  [Enter] Back: ").strip().lower()
        if go == "r":
            from pipeline.research_run import run_research
            run_research()

    return added


# -- Main entry point -------------------------------------------------------

def run_discovery(
    category: str = None,
    query:    str = None,
    section:  str = None,
    persona:  str = None,
    model:    str = None,
) -> int:
    print("\n" + "=" * 60)
    print("  DISCOVERY")
    print("=" * 60)

    if section is None:
        section = _pick_section()
        if section is None:
            print("  <- Back to main menu.")
            return 0

    # Mode selection
    print("  -- Discovery mode ------------------------------------------")
    print("  [1] Query    Search topics via SearXNG + Reddit + Google News")
    print("               Best for: discovering new angles on a subject")
    print("  [2] URL      Add article directly from one or more URLs")
    print("               Best for: known sources, seed articles, official pages")
    print("  [3] Upgrade  Rewrite an existing article with new research")
    print("               Best for: refreshing outdated content with current data")
    print()
    mode_raw = _input("  Mode (Enter = 1): ")
    if mode_raw is None:
        print("  <- Back to main menu.")
        return 0
    mode_raw = mode_raw.strip()

    if persona is None or model is None:
        persona_key, model_key = ask_persona_and_model(
            default_persona = DEFAULT_PERSONA,
            default_model   = DEFAULT_MODEL,
        )
    else:
        persona_key = persona
        model_key   = model

    model_name = MODELS.get(model_key, {}).get("name", model_key)

    # Tryb 2 -- URL
    if mode_raw == "2":
        if category is None:
            category = _pick_category(section)
        raw_url = _input("  Paste URL(s) separated by commas: ").strip()
        urls    = _parse_urls(raw_url)
        if not urls:
            print("  No valid URLs detected.")
            return 0
        added = _run_url_mode(urls, category, section, persona_key, model_name)
        if added:
            print(f"\n  -> Added {added} topics to queue.")
            queue.print_queue(filter_status=queue.STATUS_PENDING)
        return added

    # Tryb 3 -- Upgrade
    if mode_raw == "3":
        if category is None:
            category = _pick_category(section)
        raw_url = _input("  Paste URL(s) of article(s) to upgrade: ").strip()
        urls    = _parse_urls(raw_url)
        if not urls:
            print("  No valid URLs detected.")
            return 0
        added = _run_upgrade_mode(urls, category, section, persona_key, model_name)
        if added:
            print(f"\n  -> Added {added} topics to queue.")
            queue.print_queue(filter_status=queue.STATUS_PENDING)
        return added

    # Tryb 1 -- Query (domyślny)
    if category is None:
        category = _pick_category(section)

    top_n         = None
    article_focus = ""
    if query is None:
        result = _build_query(category)
        if isinstance(result, tuple):
            query, top_n = result
        else:
            query = result

    if not query:
        print("  No query -- aborting Discovery.")
        return 0

    added = _run_query_mode(query, category, section, persona_key, model_name,
                            top_n=top_n, article_focus=article_focus)

    if added:
        print(f"\n  -> Added {added} topics to queue.")
        print()
        queue.print_queue(filter_status=queue.STATUS_PENDING)

    return added

