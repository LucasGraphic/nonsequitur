"""
discovery/youtube.py
--------------------
YouTube channel RSS feeds -- trending videos by category.
Uses public RSS feeds, no API key required.
Each YouTube channel has a public RSS feed at:
https://www.youtube.com/feeds/videos.xml?channel_id=CHANNEL_ID
"""

import time
import requests
import xml.etree.ElementTree as ET

YOUTUBE_CATEGORIES = {"games", "ai", "software", "hardware",
                      "entertainment", "comfyui", "models"}

# Curated channel IDs per category -- active, high quality channels
CHANNEL_IDS = {
    "games": [
        "UCbu2SsF-Or3Rsn3NxqODImw",  # IGN
        "UCK-65DO2oOxxMwphl2tIPkA",  # GameSpot
        "UCpqXJOEqGS-TCnazcHCo0rA",  # Skill Up
        "UCsvn_Po0SmunchJYOWpOxMg",  # Dunkey
    ],
    "ai": [
        "UCZHmQk67mSJgfCCTn7xBfew",  # Two Minute Papers
        "UCWX3yGbODM3lMFpMXBOhzXQ",  # AI Explained
        "UCnUYZLuoy1rq1aVMwx4aTzw",  # Andrej Karpathy
        "UC0e3QhIYukixgh5VVpKHH9Q",  # Code Bullet
    ],
    "software": [
        "UCVhQ2NnY5Rskt6UjCUkJ_DA",  # Fireship
        "UC8butISFwT-Wl7EV0hUK0BQ",  # freeCodeCamp
        "UCW5YeuERMulmmLW1L2sydgA",  # NetworkChuck
    ],
    "hardware": [
        "UCXuqSBlHAE6Xw-yeJA0Tunw",  # Linus Tech Tips
        "UCvWmQdhMQs-8OZBH0s-KmNg",  # Gamers Nexus
    ],
    "entertainment": [
        "UCi7GJNg51C3jgmYTUwqoUXA",  # Collider
        "UC4L7YE07v8R1cHzABEkGnlQ",  # WatchMojo
    ],
    "comfyui": [
        "UCqFGBXTpB1VnYeKLBd2FBTA",  # Olivio Sarikas
        "UCXDHG9RQQtCmtEzPV4Jq_Gg",  # Sebastian Kamph
    ],
    "models": [
        "UCqFGBXTpB1VnYeKLBd2FBTA",  # Olivio Sarikas
        "UC5DNytAJ6_FISueUfzZCVsA",  # Aitrepreneur
    ],
}

RSS_BASE = "https://www.youtube.com/feeds/videos.xml?channel_id="
HEADERS  = {"User-Agent": "MultiplyAgent/1.0"}

NS = {
    "atom":   "http://www.w3.org/2005/Atom",
    "media":  "http://search.yahoo.com/mrss/",
    "yt":     "http://www.youtube.com/xml/schemas/2015",
}


def _fetch_channel(channel_id: str, cat: str) -> list:
    raw = []
    try:
        resp = requests.get(
            f"{RSS_BASE}{channel_id}",
            headers=HEADERS, timeout=10
        )
        root = ET.fromstring(resp.content)

        for entry in root.findall("atom:entry", NS)[:5]:
            title = entry.findtext("atom:title", "", NS).strip()
            if title and len(title) > 15:
                raw.append((title, cat, 0.75, "youtube", ""))
        time.sleep(0.3)
    except Exception:
        pass
    return raw


def fetch(categories: list) -> list:
    if not any(c in YOUTUBE_CATEGORIES for c in categories):
        return []

    raw = []
    for cat in categories:
        channels = CHANNEL_IDS.get(cat, [])
        for ch_id in channels[:3]:
            raw.extend(_fetch_channel(ch_id, cat))
    return raw
