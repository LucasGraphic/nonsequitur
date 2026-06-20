"""
discovery/producthunt.py
------------------------
Product Hunt -- new AI and software tools.
Uses public RSS feed (no API key required).
"""

import time
import requests
import xml.etree.ElementTree as ET

PRODUCTHUNT_CATEGORIES = {"ai", "software", "models", "comfyui"}

RSS_FEEDS = {
    "ai": [
        "https://www.producthunt.com/feed?category=artificial-intelligence",
        "https://www.producthunt.com/feed?category=machine-learning",
    ],
    "software": [
        "https://www.producthunt.com/feed?category=developer-tools",
        "https://www.producthunt.com/feed?category=productivity",
    ],
    "models": [
        "https://www.producthunt.com/feed?category=artificial-intelligence",
    ],
    "comfyui": [
        "https://www.producthunt.com/feed?category=artificial-intelligence",
    ],
}

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; MultiplyAgent/1.0)",
    "Accept":     "application/rss+xml, application/xml",
}


def _parse_feed(url: str, cat: str) -> list:
    raw = []
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        root = ET.fromstring(resp.content)

        for item in root.findall(".//item")[:10]:
            title = item.findtext("title", "").strip()
            desc  = item.findtext("description", "").strip()[:150]

            if not title or len(title) < 15:
                continue

            # Format as article topic
            topic = f"{title} -- new tool review and analysis"
            raw.append((topic, cat, 0.75, "producthunt", desc))
        time.sleep(0.5)
    except Exception:
        pass
    return raw


def fetch(categories: list) -> list:
    if not any(c in PRODUCTHUNT_CATEGORIES for c in categories):
        return []

    raw = []
    seen_feeds = set()
    for cat in categories:
        feeds = RSS_FEEDS.get(cat, [])
        for url in feeds:
            if url in seen_feeds:
                continue
            seen_feeds.add(url)
            raw.extend(_parse_feed(url, cat))
    return raw
