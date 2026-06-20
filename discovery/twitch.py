"""
discovery/twitch.py
-------------------
Twitch -- trending games by viewer count.
Uses public Twitch directory (no API key via scraping approach)
or falls back to known popular streams.
"""

import time
import requests

TWITCH_CATEGORIES = {"games", "entertainment"}

# Twitch public API endpoint (no OAuth for public game list)
TWITCH_GAMES_URL = "https://api.twitch.tv/helix/games/top"

# Fallback: use SullyGnome public stats API (no key required)
SULFGNOME_URL = "https://sullygnome.com/api/tables/games/30/watched/desc/0/100"

HEADERS = {"User-Agent": "MultiplyAgent/1.0"}


def _fetch_sullygnome() -> list:
    """Fetch trending games from SullyGnome (30-day stats, no key)."""
    raw = []
    try:
        resp = requests.get(SULFGNOME_URL, headers=HEADERS, timeout=15)
        data = resp.json()
        items = data.get("data", [])[:15]

        for item in items:
            name = item.get("name", "").strip()
            if not name or len(name) < 3:
                continue
            hours = item.get("watchtime", 0)
            score = min(hours / 1_000_000, 1.0)
            topic = f"{name} -- trending on Twitch, viewer analysis 2026"
            raw.append((topic, "games", score, "twitch", ""))
        time.sleep(0.5)
    except Exception:
        pass
    return raw


def _fetch_fallback() -> list:
    """Static fallback list of consistently trending Twitch games."""
    trending = [
        "League of Legends", "Fortnite", "Valorant", "Minecraft",
        "Grand Theft Auto V", "Apex Legends", "World of Warcraft",
        "Dota 2", "Counter-Strike 2", "Diablo IV",
        "Path of Exile 2", "Elden Ring", "Palworld",
    ]
    raw = []
    for name in trending:
        topic = f"{name} -- Twitch viewer trends and community analysis"
        raw.append((topic, "games", 0.65, "twitch_fallback", ""))
    return raw


def fetch(categories: list) -> list:
    if not any(c in TWITCH_CATEGORIES for c in categories):
        return []

    raw = _fetch_sullygnome()

    # Use fallback if sullygnome failed
    if not raw:
        raw = _fetch_fallback()

    return raw
