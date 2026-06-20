# discovery/filter.py -- filtering and ranking of discovery results
# Blocked domains/patterns loaded from data/domains_blocked.json via domain_config.py

import re
from urllib.parse import urlparse
from config import DISCOVERY_MAX, DISCOVERY_TOP_N

# Import central domain config
from domain_config import is_blocked

SOURCE_SCORE = {
    "searxng":      3,
    "google_news":  4,
    "steam_news":   5,
    "steam_charts": 2,
    "reddit":       2,
    "youtube":      3,
    "twitch":       1,
    "huggingface":  5,
    "seed_url":     6,
}

REDDIT_LOW_QUALITY_RE = re.compile(
    r"^(lol|wtf|omg|why|help|anyone|question|thoughts|opinion|"
    r"rant|meme|shitpost|daily|weekly|megathread|looking for|"
    r"does anyone|can someone|is it|should i|what is|how do)",
    re.IGNORECASE
)


def _score(item: dict, query: str) -> float:
    base  = float(SOURCE_SCORE.get(item.get("source", ""), 1))
    score = base

    title   = item.get("title", "").lower()
    snippet = item.get("snippet", "").lower()
    ql      = query.lower()

    words = [w for w in re.split(r"\W+", ql) if len(w) > 2]
    title_matches = sum(1 for w in words if w in title)
    score += title_matches * 2.0

    snippet_matches = sum(1 for w in words if w in snippet)
    score += snippet_matches * 0.5

    title_words = len(title.split())
    if title_words >= 5:
        score += 0.5
    if title_words >= 8:
        score += 0.5

    if len(snippet) > 150:
        score += 0.5
    if len(snippet) > 350:
        score += 0.5

    if item.get("source") == "reddit":
        if REDDIT_LOW_QUALITY_RE.match(title):
            score -= 2.0
        if title_words <= 3:
            score -= 1.5

    if re.search(r"[\u0400-\u04FF\u4E00-\u9FFF\u0600-\u06FF\u3040-\u30FF]", title):
        score -= 3.0

    return max(score, 0.1)


def filter_and_rank(items: list, query: str, top_n: int = None) -> list:
    if top_n is None:
        top_n = DISCOVERY_TOP_N

    seen_urls   = set()
    seen_titles = set()
    filtered    = []

    for item in items[:DISCOVERY_MAX * 2]:
        url   = item.get("url", "").strip()
        title = item.get("title", "").strip()

        if not url or not title:
            continue
        if url in seen_urls:
            continue
        if title.lower() in seen_titles:
            continue

        if is_blocked(url, title):
            continue

        seen_urls.add(url)
        seen_titles.add(title.lower())

        item["_score"] = _score(item, query)
        filtered.append(item)

    filtered.sort(key=lambda x: x["_score"], reverse=True)
    return filtered[:top_n]
