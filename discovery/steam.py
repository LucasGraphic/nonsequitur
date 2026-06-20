"""
discovery/steam.py
------------------
Steam News API -- official game news, patch notes, announcements.
Steam Charts (SteamSpy) -- trending games by player count.
No API key required.
"""

import time
import requests

# Categories where Steam content is relevant
STEAM_CATEGORIES = {"games"}

# Top app IDs to check for news (most popular games)
STEAM_TOP_APPIDS = [
    570,    # Dota 2
    730,    # CS2
    1172470, # Apex Legends
    1245620, # Elden Ring
    2519060, # Diablo IV
    1623730, # Palworld
    2050650, # Baldur's Gate 3
    1091500, # Cyberpunk 2077
    975370,  # Dwarf Fortress
    1172620, # Sea of Thieves
    230410,  # Warframe
    252490,  # Rust
    578080,  # PUBG
    1382330, # Monster Hunter Rise
    1551360, # Forza Horizon 5
]

STEAM_NEWS_URL   = "https://api.steampowered.com/ISteamNews/GetNewsForApp/v0002/"
STEAMSPY_URL     = "https://steamspy.com/api.php"
STEAM_CHARTS_URL = "https://store.steampowered.com/charts/mostplayed"


def _fetch_news() -> list:
    """Fetch recent news from top Steam games."""
    raw = []
    for appid in STEAM_TOP_APPIDS[:8]:
        try:
            resp = requests.get(STEAM_NEWS_URL, params={
                "appid": appid, "count": 3, "maxlength": 300,
                "format": "json",
            }, timeout=10)
            items = resp.json().get("appnews", {}).get("newsitems", [])
            for item in items:
                title = item.get("title", "").strip()
                if title and len(title) > 20:
                    raw.append((title, "games", 0.85, "steam_news", ""))
            time.sleep(0.2)
        except Exception:
            pass
    return raw


def _fetch_charts() -> list:
    """Fetch trending game names from SteamSpy top 100."""
    raw = []
    try:
        resp = requests.get(STEAMSPY_URL, params={
            "request": "top100in2weeks"
        }, timeout=15)
        data = resp.json()
        for appid, info in list(data.items())[:20]:
            name = info.get("name", "").strip()
            if name and len(name) > 3:
                owners = info.get("owners", "0")
                score  = 0.7
                raw.append((
                    f"{name} -- trending on Steam",
                    "games", score, "steam_charts", ""
                ))
        time.sleep(0.3)
    except Exception:
        pass
    return raw


def fetch(categories: list) -> list:
    if not any(c in STEAM_CATEGORIES for c in categories):
        return []

    raw = []
    raw.extend(_fetch_news())
    raw.extend(_fetch_charts())
    return raw
