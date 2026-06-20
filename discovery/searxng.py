"""
discovery/searxng.py
--------------------
Fetches trending topics from SearXNG (multiple pages per query).
"""

import time
import requests
from taxonomy.categories import TREND_QUERIES
from discovery.filter import clean_title

SEARXNG_URL   = "http://10.0.0.195:8080/search"
PAGES_PER_QUERY = 3
ENGINES = "google,bing,duckduckgo,brave"


def fetch(categories: list) -> list:
    """
    Search SearXNG for all queries of given categories.
    Returns raw list of (topic, category, score, source, notes).
    """
    headers = {"User-Agent": "MultiplyAgent/1.0", "Accept": "application/json"}
    raw     = []

    for cat in categories:
        queries = TREND_QUERIES.get(cat, [])
        for query in queries:
            for page in range(1, PAGES_PER_QUERY + 1):
                try:
                    resp = requests.get(
                        SEARXNG_URL, headers=headers, timeout=20,
                        params={
                            "q":       query,
                            "format":  "json",
                            "language": "en",
                            "pageno":  page,
                            "engines": ENGINES,
                        }
                    )
                    results = resp.json().get("results", [])
                    if not results:
                        break
                    for r in results[:8]:
                        t = clean_title(r.get("title", ""))
                        if t:
                            score = round(1.0 - results.index(r) * 0.08, 2)
                            raw.append((t, cat, score, "searxng", ""))
                    time.sleep(0.3)
                except Exception:
                    break

    return raw
