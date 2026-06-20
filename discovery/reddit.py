"""
discovery/reddit.py
-------------------
Fetches hot posts from Reddit subreddits via public JSON API.
No API key required.
"""

import time
import requests
from taxonomy.categories import REDDIT_SUBS

SKIP_STARTERS = (
    "who ", "what ", "why ", "how ", "anyone ", "does ",
    "is it ", "should i ", "best way ", "help ", "question:",
    "psa:", "rant:", "weekly ", "daily ", "monthly ",
)

MAX_SUBS_PER_CAT = 4
POSTS_PER_SUB    = 15


def fetch(categories: list) -> list:
    """
    Fetch hot posts from subreddits for given categories.
    Returns raw list of (topic, category, score, source, notes).
    """
    headers = {"User-Agent": "MultiplyAgent/1.0"}
    raw     = []

    for cat in categories:
        subs = REDDIT_SUBS.get(cat, [])[:MAX_SUBS_PER_CAT]
        for sub in subs:
            sn = sub.lstrip("r/")
            try:
                resp = requests.get(
                    f"https://www.reddit.com/r/{sn}/hot.json?limit={POSTS_PER_SUB}",
                    headers=headers, timeout=15
                )
                if resp.status_code == 429:
                    time.sleep(3)
                    continue
                if resp.status_code != 200:
                    continue

                posts = resp.json().get("data", {}).get("children", [])
                for post in posts:
                    d     = post.get("data", {})
                    title = d.get("title", "").strip()
                    score = min(d.get("score", 0) / 10000, 1.0)

                    if not title:
                        continue
                    if d.get("stickied"):
                        continue
                    if title.lower().startswith(SKIP_STARTERS):
                        continue

                    raw.append((title, cat, score, f"reddit/{sn}", ""))

                time.sleep(0.8)
            except Exception:
                pass

    return raw
