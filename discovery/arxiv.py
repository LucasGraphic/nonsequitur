"""
discovery/arxiv.py
------------------
ArXiv API -- recent AI/ML papers.
Queries the official ArXiv API (no key required).
Returns paper titles formatted as article-ready topics.
"""

import time
import requests
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta

ARXIV_CATEGORIES = {"ai", "models", "software"}

ARXIV_API = "https://export.arxiv.org/api/query"

# ArXiv subject categories mapped to our taxonomy
ARXIV_QUERIES = {
    "ai": [
        "cat:cs.AI",            # Artificial Intelligence
        "cat:cs.LG",            # Machine Learning
        "cat:cs.CL",            # Computation and Language (NLP/LLMs)
        "cat:cs.CV",            # Computer Vision
    ],
    "models": [
        "cat:cs.LG+AND+ti:diffusion",
        "cat:cs.CV+AND+ti:generation",
        "cat:cs.CL+AND+ti:language+model",
    ],
    "software": [
        "cat:cs.SE",            # Software Engineering
        "cat:cs.PL",            # Programming Languages
    ],
}

HEADERS = {"User-Agent": "MultiplyAgent/1.0"}
NS      = {"atom": "http://www.w3.org/2005/Atom"}


def _fetch_papers(query: str, cat: str, max_results: int = 8) -> list:
    raw = []
    try:
        resp = requests.get(ARXIV_API, params={
            "search_query": query,
            "start":        0,
            "max_results":  max_results,
            "sortBy":       "submittedDate",
            "sortOrder":    "descending",
        }, headers=HEADERS, timeout=20)

        root = ET.fromstring(resp.content)

        for entry in root.findall("atom:entry", NS):
            title   = entry.findtext("atom:title", "", NS).strip()
            summary = entry.findtext("atom:summary", "", NS).strip()[:200]

            if not title or len(title) < 15:
                continue

            # Format as article topic
            topic = f"{title} -- AI research paper analysis"
            raw.append((topic, cat, 0.7, "arxiv", summary))
        time.sleep(1)
    except Exception:
        pass
    return raw


def fetch(categories: list) -> list:
    if not any(c in ARXIV_CATEGORIES for c in categories):
        return []

    raw = []
    for cat in categories:
        queries = ARXIV_QUERIES.get(cat, [])
        for query in queries[:2]:
            raw.extend(_fetch_papers(query, cat))
    return raw
