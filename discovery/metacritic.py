"""
discovery/metacritic.py
-----------------------
Metacritic RSS feeds -- new game and film reviews.
No API key required.
"""

import time
import requests
import xml.etree.ElementTree as ET

METACRITIC_CATEGORIES = {"games", "entertainment"}

RSS_FEEDS = {
    "games": [
        "https://www.metacritic.com/rss/games",
        "https://www.metacritic.com/rss/games/pc",
    ],
    "entertainment": [
        "https://www.metacritic.com/rss/movies",
        "https://www.metacritic.com/rss/tv",
    ],
}

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; MultiplyAgent/1.0)",
    "Accept": "application/rss+xml, application/xml, text/xml",
}


def _parse_rss(url: str, category: str) -> list:
    raw = []
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        root = ET.fromstring(resp.content)
        ns   = {"content": "http://purl.org/rss/1.0/modules/content/"}

        for item in root.findall(".//item")[:10]:
            title = item.findtext("title", "").strip()
            if title and len(title) > 20:
                raw.append((title, category, 0.8, "metacritic", ""))
        time.sleep(0.5)
    except Exception:
        pass
    return raw


def fetch(categories: list) -> list:
    if not any(c in METACRITIC_CATEGORIES for c in categories):
        return []

    raw = []
    for cat in categories:
        feeds = RSS_FEEDS.get(cat, [])
        for url in feeds:
            raw.extend(_parse_rss(url, cat))
    return raw
