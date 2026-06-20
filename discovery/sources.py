# discovery/sources.py -- adaptery wszystkich źródeł Discovery i Deep Research
# Każdy adapter zwraca listę dict: {title, url, snippet, source}
# Używany zarówno przez Discovery (broad queries) jak i Deep Research (targeted queries).

import requests
import feedparser
from typing import Optional
from config import SEARXNG_URL, SEARXNG_PAGES, SEARXNG_PAGES_DEEP
from domain_config import get_sources_for_category


# -- Helpers ----------------------------------------------------------------

def _safe_get(url: str, params: dict = None, timeout: int = 8) -> Optional[requests.Response]:
    try:
        r = requests.get(url, params=params, timeout=timeout)
        r.raise_for_status()
        return r
    except Exception:
        return None


def _item(title: str, url: str, snippet: str, source: str) -> dict:
    return {
        "title":   title.strip()[:200],
        "url":     url.strip(),
        "snippet": snippet.strip()[:500],
        "source":  source,
    }


# -- SearXNG ----------------------------------------------------------------

def fetch_searxng(query: str, deep: bool = False, time_range: str = "") -> list:
    """
    Search SearXNG. Returns list of result dicts.
    deep=True uses more pages (SEARXNG_PAGES_DEEP).
    time_range: '', 'day', 'week', 'month', 'year'
    """
    pages  = SEARXNG_PAGES_DEEP if deep else SEARXNG_PAGES
    results = []
    seen_urls = set()

    for page in range(1, pages + 1):
        params = {
            "q":        query,
            "format":   "json",
            "pageno":   page,
            "engines":  "google,duckduckgo,brave",
            "language": "en",
        }
        if time_range:
            params["time_range"] = time_range

        r = _safe_get(f"{SEARXNG_URL}/search", params=params)
        if not r:
            break
        data = r.json()
        items = data.get("results", [])
        if not items:
            break

        new = 0
        for item in items:
            url = item.get("url", "")
            if not url or url in seen_urls:
                continue
            seen_urls.add(url)
            results.append(_item(
                title   = item.get("title", ""),
                url     = url,
                snippet = item.get("content", ""),
                source  = "searxng",
            ))
            new += 1

        print(f"   [SearXNG    ] page {page}: +{new} (total: {len(results)})")

    return results


# -- Reddit -----------------------------------------------------------------

# Relevant subreddits per category -- only these will pass the filter
REDDIT_SUBREDDITS = {
    "games":         {"games", "gaming", "pcgaming", "truegaming", "gamereviews",
                      "patientgamers", "steam", "rpg", "diablo4", "pathofexile",
                      "pathofexile2", "globaloffensive", "leagueoflegends", "wow",
                      "mmorpg", "gachagaming", "apexlegends", "minecraft", "gaming"},
    "hardware":      {"hardware", "nvidia", "amd", "intel", "pcmasterrace",
                      "buildapc", "buildapcforme", "gpumarketplace", "overclocking",
                      "homelab", "sysadmin", "linux_gaming", "linuxhardware"},
    "ai-data":       {"MachineLearning", "artificial", "AIAssistants", "LocalLLaMA",
                      "StableDiffusion", "singularity", "agi", "ChatGPT", "OpenAI",
                      "comfyui", "StableDiffusion", "generativeai"},
    "software":      {"programming", "learnprogramming", "webdev", "javascript",
                      "python", "linux", "opensource", "softwaregore", "devops",
                      "MachineLearning", "techsupport"},
    "security":      {"netsec", "cybersecurity", "hacking", "privacy", "piracy",
                      "sysadmin", "homelab", "linuxadmin", "reverseengineering"},
    "entertainment": {"movies", "television", "netflix", "Showerthoughts",
                      "entertainment", "fantasy", "scifi", "books", "anime"},
    "photography":   {"photojournalism", "analog", "photography", "AskPhotography",
                      "beginning_photography", "AnalogCommunity", "dji"},
    "drone":         {"drones", "dji", "fpv", "multirotor", "fpvracing"},
    "3d":            {"blender", "3Dmodeling", "Maya", "gamedev", "unrealengine",
                      "Unity3D", "pixar", "VFX", "CGI"},
    "ai":            {"StableDiffusion", "comfyui", "MediaSynthesis", "deepdream",
                      "artificial", "MachineLearning"},
    "other":         set(),  # no filter for other
}


def fetch_reddit(query: str, category: str = "other") -> list:
    """Searches Reddit with category-aware subreddit filtering."""
    try:
        r = requests.get(
            "https://www.reddit.com/search.json",
            params={"q": query, "sort": "relevance", "limit": 50, "t": "month"},
            timeout=10,
            headers={"User-Agent": "ArticleAgent/1.0 (research bot; contact: agent@localhost)"},
        )
        r.raise_for_status()
    except Exception:
        print("   [Reddit     ]... 0 raw")
        return []

    posts    = r.json().get("data", {}).get("children", [])
    allowed  = REDDIT_SUBREDDITS.get(category, set())
    results  = []
    filtered = 0

    for p in posts:
        d          = p.get("data", {})
        url        = d.get("url", "")
        subreddit  = d.get("subreddit", "").lower()
        permalink  = d.get("permalink", "")

        if not url:
            continue

        # Filter by subreddit if category has a whitelist
        if allowed and subreddit not in {s.lower() for s in allowed}:
            filtered += 1
            continue

        results.append(_item(
            title   = d.get("title", ""),
            url     = f"https://reddit.com{permalink}",
            snippet = d.get("selftext", "")[:300] or d.get("title", ""),
            source  = "reddit",
        ))

    if filtered:
        print(f"   [Reddit     ]... {len(results)} relevant ({filtered} off-topic filtered)")
    else:
        print(f"   [Reddit     ]... {len(results)} raw")
    return results


# -- Steam News -------------------------------------------------------------

def fetch_steam_news(query: str) -> list:
    """
    Pobiera najnowsze newsy Steam.
    Próbuje RSS, fallback przez SearXNG site:store.steampowered.com.
    """
    results = []

    # Próba RSS
    try:
        feed = feedparser.parse("https://store.steampowered.com/feeds/news/?l=english")
        if feed.entries:
            for entry in feed.entries[:60]:
                title   = entry.get("title", "")
                summary = entry.get("summary", "")
                if not title:
                    continue
                results.append(_item(
                    title   = title,
                    url     = entry.get("link", ""),
                    snippet = summary[:300],
                    source  = "steam_news",
                ))
            print(f"   [Steam News ]... {len(results)} raw (RSS)")
            return results
    except Exception:
        pass

    # Fallback: SearXNG targeted
    r = _safe_get(f"{SEARXNG_URL}/search", params={
        "q":      f"site:store.steampowered.com {query}",
        "format": "json",
        "pageno": 1,
    })
    if r:
        items = r.json().get("results", [])
        results = [_item(
            title   = i.get("title", ""),
            url     = i.get("url", ""),
            snippet = i.get("content", ""),
            source  = "steam_news",
        ) for i in items if "steampowered.com" in i.get("url", "")]

    print(f"   [Steam News ]... {len(results)} raw (SearXNG fallback)")
    return results


# -- Steam Charts -----------------------------------------------------------

def fetch_steam_charts(query: str) -> list:
    """
    Pobiera dane Steam Charts.
    Próbuje RSS, fallback przez SearXNG site:steamcharts.com.
    """
    results = []

    # Próba RSS
    try:
        feed = feedparser.parse("https://steamcharts.com/rss.xml")
        if feed.entries:
            for entry in feed.entries[:60]:
                title = entry.get("title", "")
                if not title:
                    continue
                results.append(_item(
                    title   = title,
                    url     = entry.get("link", ""),
                    snippet = entry.get("summary", "")[:300],
                    source  = "steam_charts",
                ))
            print(f"   [Steam Charts]... {len(results)} raw (RSS)")
            return results
    except Exception:
        pass

    # Fallback: SearXNG targeted
    r = _safe_get(f"{SEARXNG_URL}/search", params={
        "q":      f"site:steamcharts.com {query}",
        "format": "json",
        "pageno": 1,
    })
    if r:
        items = r.json().get("results", [])
        results = [_item(
            title   = i.get("title", ""),
            url     = i.get("url", ""),
            snippet = i.get("content", ""),
            source  = "steam_charts",
        ) for i in items if "steamcharts.com" in i.get("url", "")]

    print(f"   [Steam Charts]... {len(results)} raw (SearXNG fallback)")
    return results


# -- YouTube ----------------------------------------------------------------

def fetch_youtube(query: str) -> list:
    """
    YouTube disabled -- SearXNG returns only titles and descriptions, not transcripts.
    Titles add noise without content value. Re-enable if transcript API is added.
    """
    print(f"   [YouTube    ]... skipped (disabled)")
    return []


# -- Twitch -----------------------------------------------------------------

def fetch_twitch(query: str) -> list:
    """Pobiera trendy Twitch przez SearXNG z engine=twitch."""
    r = _safe_get(f"{SEARXNG_URL}/search", params={
        "q":       f"site:twitch.tv {query}",
        "format":  "json",
        "pageno":  1,
    })
    if not r:
        print("   [Twitch     ]... 0 raw")
        return []

    items   = r.json().get("results", [])
    results = [_item(
        title   = i.get("title", ""),
        url     = i.get("url", ""),
        snippet = i.get("content", ""),
        source  = "twitch",
    ) for i in items if i.get("url") and "twitch.tv" in i.get("url", "")]

    print(f"   [Twitch     ]... {len(results)} raw")
    return results


# -- Google News (via SearXNG news query) ----------------------------------

def fetch_google_news(query: str) -> list:
    """Pobiera newsy przez SearXNG z dodatkowym filtrem 'news'."""
    r = _safe_get(f"{SEARXNG_URL}/search", params={
        "q":      f"{query} news",
        "format": "json",
        "pageno": 1,
    })
    if not r:
        print("   [Google News]... 0 raw")
        return []

    items   = r.json().get("results", [])
    results = [_item(
        title   = i.get("title", ""),
        url     = i.get("url", ""),
        snippet = i.get("content", ""),
        source  = "google_news",
    ) for i in items if i.get("url")]

    print(f"   [Google News]... {len(results)} raw")
    return results


# -- Hugging Face -----------------------------------------------------------

def fetch_huggingface(query: str) -> list:
    """
    Searches Hugging Face models and papers via SearXNG site search.
    Best for: model releases, release dates, architecture details, benchmarks.
    """
    results = []
    seen    = set()

    # Search models
    r = _safe_get(f"{SEARXNG_URL}/search", params={
        "q":      f"site:huggingface.co {query}",
        "format": "json",
        "pageno": 1,
    })
    if r:
        for i in r.json().get("results", []):
            url = i.get("url", "")
            if url and "huggingface.co" in url and url not in seen:
                seen.add(url)
                results.append(_item(
                    title   = i.get("title", ""),
                    url     = url,
                    snippet = i.get("content", ""),
                    source  = "huggingface",
                ))

    # Search papers
    r2 = _safe_get(f"{SEARXNG_URL}/search", params={
        "q":      f"site:arxiv.org {query} model",
        "format": "json",
        "pageno": 1,
    })
    if r2:
        for i in r2.json().get("results", []):
            url = i.get("url", "")
            if url and "arxiv.org" in url and url not in seen:
                seen.add(url)
                results.append(_item(
                    title   = i.get("title", ""),
                    url     = url,
                    snippet = i.get("content", ""),
                    source  = "huggingface",
                ))

    print(f"   [HuggingFace]... {len(results)} raw")
    return results


# -- Zbiorcze wywołanie ----------------------------------------------------

CATEGORY_SOURCES = {
    "games":         ["searxng", "reddit", "steam_news", "google_news"],
    "ai-data":       ["searxng", "reddit", "google_news", "huggingface"],
    "hardware":      ["searxng", "reddit", "google_news"],
    "software":      ["searxng", "reddit", "google_news"],
    "security":      ["searxng", "reddit", "google_news"],
    "entertainment": ["searxng", "reddit", "google_news"],
    "photography":   ["searxng", "reddit"],
    "drone":         ["searxng", "reddit"],
    "portrait-studio":  ["searxng", "reddit"],
    "macro":         ["searxng", "reddit"],
    "portrait-outdoor": ["searxng", "reddit"],
    "product":       ["searxng", "reddit"],
    "travel":        ["searxng", "reddit"],
    "3d":            ["searxng", "reddit"],
    "3d-exterior":   ["searxng", "reddit"],
    "3d-interior":   ["searxng", "reddit"],
    "ai":            ["searxng", "reddit", "huggingface"],
    "other":         ["searxng", "reddit", "google_news"],
}

def fetch_rss_sources(query: str, category: str = "other", max_age_days: int = 30) -> list:
    """
    Fetch articles from RSS feeds defined in source_config.json for the given category.
    Returns items published within max_age_days.
    """
    import datetime
    from domain_config import get_rss_feeds

    feeds = get_rss_feeds(category)
    if not feeds:
        return []

    cutoff  = datetime.datetime.utcnow() - datetime.timedelta(days=max_age_days)
    results = []
    seen    = set()

    for feed_info in feeds:
        url    = feed_info.get("url", "")
        domain = feed_info.get("domain", "")
        if not url:
            continue

        try:
            feed = feedparser.parse(url)
            added = 0
            for entry in feed.entries[:30]:
                published = None
                for date_field in ("published_parsed", "updated_parsed"):
                    val = entry.get(date_field)
                    if val:
                        try:
                            published = datetime.datetime(*val[:6])
                        except Exception:
                            pass
                        break

                if published and published < cutoff:
                    continue

                entry_url = entry.get("link", "")
                if not entry_url or entry_url in seen:
                    continue
                seen.add(entry_url)

                title   = entry.get("title", "").strip()
                summary = entry.get("summary", "").strip()
                import re as _re
                summary = _re.sub(r"<[^>]+>", " ", summary)
                summary = _re.sub(r"\s+", " ", summary).strip()[:400]

                if not title:
                    continue

                results.append(_item(
                    title   = title,
                    url     = entry_url,
                    snippet = summary,
                    source  = "rss",
                ))
                added += 1

            if added:
                print(f"   [RSS        ]... +{added} from {domain}")

        except Exception as e:
            print(f"   [RSS        ]... error fetching {domain}: {e}")
            continue

    if results:
        print(f"   [RSS        ]... {len(results)} total [{category}]")

    return results


SOURCE_FN = {
    "rss":          lambda q: [],  # called with category arg separately
    "searxng":      fetch_searxng,
    "reddit":       fetch_reddit,
    "steam_news":   fetch_steam_news,
    "steam_charts": fetch_steam_charts,
    "youtube":      fetch_youtube,
    "twitch":       fetch_twitch,
    "google_news":  fetch_google_news,
    "huggingface":  fetch_huggingface,
}


def fetch_all(query: str, category: str = "other", deep: bool = False,
              skip_rss: bool = False) -> list:
    """
    Fetches results from all sources relevant to the category.
    skip_rss=True skips RSS (used when RSS was already fetched once per run).
    """
    sources     = get_sources_for_category(category)
    all_results = []


    for src in sources:
        try:
            if src == "rss":
                if skip_rss:
                    continue
                # Skip RSS if we already have enough results from other sources
                if len(all_results) > 100:
                    continue
                results = fetch_rss_sources(query, category=category, max_age_days=3)
            elif src == "searxng":
                results = fetch_searxng(query, deep=deep)
            elif src == "reddit":
                results = fetch_reddit(query, category=category)
            else:
                fn = SOURCE_FN.get(src)
                if not fn:
                    continue
                results = fn(query)
            all_results.extend(results)
        except Exception as e:
            print(f"   [{src:<12}] error: {e}")

    # Filter out blocked domains before returning
    from domain_config import is_blocked as _is_blocked
    before = len(all_results)
    all_results = [r for r in all_results if not _is_blocked(r["url"], r.get("title", ""))]
    removed = before - len(all_results)
    if removed:
        print(f"   [fetch_all  ] Blocked filter: -{removed} results")
    return all_results
