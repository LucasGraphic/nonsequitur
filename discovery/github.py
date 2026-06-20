"""
discovery/github.py
-------------------
GitHub Trending -- trending repositories by language and topic.
Uses unofficial trending scraper endpoint + GitHub search API.
No API key required (rate limited to 60 req/hour unauthenticated).
"""

import time
import requests

GITHUB_CATEGORIES = {"ai", "software", "hardware", "models", "comfyui"}

GITHUB_SEARCH_URL = "https://api.github.com/search/repositories"

TOPIC_QUERIES = {
    "ai":       ["artificial-intelligence", "llm", "machine-learning",
                 "large-language-model", "transformers", "ollama"],
    "software": ["framework", "developer-tools", "cli", "typescript",
                 "rust", "python"],
    "hardware": ["embedded", "arduino", "raspberry-pi", "fpga"],
    "models":   ["stable-diffusion", "lora", "comfyui", "flux",
                 "image-generation"],
    "comfyui":  ["comfyui", "stable-diffusion", "flux-ai"],
}

HEADERS = {
    "User-Agent":  "MultiplyAgent/1.0",
    "Accept":      "application/vnd.github.v3+json",
}


def _search_repos(query: str, cat: str) -> list:
    raw = []
    try:
        resp = requests.get(GITHUB_SEARCH_URL, headers=HEADERS, params={
            "q":        f"{query} pushed:>2026-01-01",
            "sort":     "stars",
            "order":    "desc",
            "per_page": 8,
        }, timeout=15)

        if resp.status_code == 403:
            return []  # Rate limited

        items = resp.json().get("items", [])
        for repo in items:
            name = repo.get("full_name", "")
            desc = repo.get("description") or ""
            stars = repo.get("stargazers_count", 0)
            if not desc or len(desc) < 15:
                continue
            title = f"{name}: {desc[:80]}"
            score = min(stars / 10000, 1.0)
            raw.append((title, cat, score, "github", ""))
        time.sleep(0.5)
    except Exception:
        pass
    return raw


def fetch(categories: list) -> list:
    if not any(c in GITHUB_CATEGORIES for c in categories):
        return []

    raw = []
    for cat in categories:
        topics = TOPIC_QUERIES.get(cat, [])
        for topic in topics[:3]:
            raw.extend(_search_repos(topic, cat))
    return raw
