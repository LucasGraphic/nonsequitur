"""
discovery/google_news.py
------------------------
Google News RSS -- Technology and Entertainment topic feeds.
No API key required. Uses official Google News topic RSS.
"""

import time
import requests
import xml.etree.ElementTree as ET
import re

# Official Google News topic RSS feeds
TOPIC_FEEDS = {
    "technology": "https://news.google.com/rss/topics/CAAqJggKIiBDQkFTRWdvSUwyMHZNRGRqTVhZU0FtVnVHZ0pWVXlnQVAB?hl=en-US&gl=US&ceid=US:en",
    "entertainment": "https://news.google.com/rss/topics/CAAqJggKIiBDQkFTRWdvSUwyMHZNREpxYW5RU0FtVnVHZ0pWVXlnQVAB?hl=en-US&gl=US&ceid=US:en",
}

# Map Google News topics to our taxonomy categories
TOPIC_TO_CATS = {
    "technology":    ["ai", "software", "hardware", "security"],
    "entertainment": ["games", "entertainment"],
}

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; MultiplyAgent/1.0)",
    "Accept":     "application/rss+xml, application/xml, text/xml",
}


def _clean_title(title: str) -> str:
    """Remove source name appended by Google News: 'Title - Source Name'"""
    return re.sub(r"\s+-\s+[\w\s]{2,30}$", "", title).strip()


def _fetch_topic(topic_key: str, feed_url: str, categories: list) -> list:
    """Fetch RSS feed and assign to matching categories."""
    target_cats = [c for c in TOPIC_TO_CATS.get(topic_key, []) if c in categories]
    if not target_cats:
        return []

    raw = []
    try:
        resp = requests.get(feed_url, headers=HEADERS, timeout=15)
        root = ET.fromstring(resp.content)

        for item in root.findall(".//item")[:15]:
            title = _clean_title(item.findtext("title", "").strip())
            if not title or len(title) < 20:
                continue
            # Assign to first matching category
            cat = target_cats[0]
            raw.append((title, cat, 0.8, "google_news", ""))

        time.sleep(0.5)
    except Exception:
        pass
    return raw


def fetch(categories: list) -> list:
    raw = []
    for topic_key, feed_url in TOPIC_FEEDS.items():
        raw.extend(_fetch_topic(topic_key, feed_url, categories))
    return raw
