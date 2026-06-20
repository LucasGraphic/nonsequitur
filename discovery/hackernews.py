"""
discovery/hackernews.py
-----------------------
Fetches top stories from Hacker News via public Firebase API.
Only used for tech-relevant categories.
"""

import time
import requests

HN_TOP_URL  = "https://hacker-news.firebaseio.com/v0/topstories.json"
HN_ITEM_URL = "https://hacker-news.firebaseio.com/v0/item/{}.json"
MAX_STORIES = 30

# Only fetch HN for these categories
HN_CATEGORIES = {"ai", "software", "security", "hardware", "comfyui", "models"}


def _classify(title: str) -> str:
    """Classify a HN title into our taxonomy."""
    tl = title.lower()
    if any(w in tl for w in ["llm","ai ","gpt","model","openai","anthropic",
                              "neural","machine learning","diffusion","flux"]):
        return "ai"
    if any(w in tl for w in ["exploit","vulnerability","hack","breach",
                              "malware","cve","security","zero-day"]):
        return "security"
    if any(w in tl for w in ["gpu","cpu","rtx","chip","nvidia","amd","hardware"]):
        return "hardware"
    if any(w in tl for w in ["comfyui","stable diffusion","sdxl","lora"]):
        return "comfyui"
    return "software"


def fetch(categories: list) -> list:
    """
    Fetch HN top stories relevant to given categories.
    Returns raw list of (topic, category, score, source, notes).
    """
    # Only run if at least one relevant category is requested
    if not any(c in HN_CATEGORIES for c in categories):
        return []

    raw = []
    try:
        story_ids = requests.get(HN_TOP_URL, timeout=10).json()[:MAX_STORIES]
    except Exception:
        return []

    for sid in story_ids:
        try:
            item  = requests.get(HN_ITEM_URL.format(sid), timeout=8).json()
            title = item.get("title", "").strip()
            score = min(item.get("score", 0) / 500, 1.0)

            if not title:
                continue

            cat = _classify(title)
            if cat not in categories:
                continue

            raw.append((title, cat, score, "hackernews", ""))
            time.sleep(0.05)
        except Exception:
            continue

    return raw
