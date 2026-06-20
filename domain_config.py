# domain_config.py -- Central domain configuration loader
# Reads from data/domains_trusted.json, domains_blocked.json, source_config.json
# All other modules import from here -- never hardcode domains elsewhere.

import json
import os
import re
from functools import lru_cache
from urllib.parse import urlparse

BASE_DIR  = os.path.dirname(os.path.abspath(__file__))
DATA_DIR  = os.path.join(BASE_DIR, "data")

_TRUSTED_FILE = os.path.join(DATA_DIR, "domains_trusted.json")
_BLOCKED_FILE = os.path.join(DATA_DIR, "domains_blocked.json")
_SOURCE_FILE  = os.path.join(DATA_DIR, "source_config.json")


# -- Loaders ----------------------------------------------------------------

@lru_cache(maxsize=1)
def _load_trusted() -> dict:
    with open(_TRUSTED_FILE, encoding="utf-8") as f:
        return json.load(f)

@lru_cache(maxsize=1)
def _load_blocked() -> dict:
    with open(_BLOCKED_FILE, encoding="utf-8") as f:
        return json.load(f)

@lru_cache(maxsize=1)
def _load_source_config() -> dict:
    with open(_SOURCE_FILE, encoding="utf-8") as f:
        return json.load(f)


def reload_all() -> None:
    """Force reload all config files (clears lru_cache)."""
    _load_trusted.cache_clear()
    _load_blocked.cache_clear()
    _load_source_config.cache_clear()


# -- Domain trust -----------------------------------------------------------

def get_domain_trust(url: str, category: str = "other") -> dict:
    """
    Returns trust metadata for a URL.
    Checks category-specific config first, then global.
    Returns dict: {domain, tier, boost, trust_reason}
    """
    try:
        parsed = urlparse(url)
        domain = parsed.netloc.lower()
        if domain.startswith("www."):
            domain = domain[4:]
    except Exception:
        return _unknown(url)

    trusted = _load_trusted()

    # Check category-specific first
    cat_data = trusted.get(category, {})
    for key, info in cat_data.items():
        if domain == key or domain.endswith("." + key):
            return {
                "domain":       domain,
                "tier":         info["tier"],
                "boost":        info["boost"],
                "trust_reason": f"{category}:{key}",
                "retrieval_boost": info["boost"],
                "domain_trust": info["tier"],
                "trust_score":  info["boost"],
            }

    # Check global
    global_data = trusted.get("global", {})
    for key, info in global_data.items():
        if domain == key or domain.endswith("." + key):
            return {
                "domain":       domain,
                "tier":         info["tier"],
                "boost":        info["boost"],
                "trust_reason": f"global:{key}",
                "retrieval_boost": info["boost"],
                "domain_trust": info["tier"],
                "trust_score":  info["boost"],
            }

    # Fallback: unknown -- do NOT search other categories
    # Domains are intentionally scoped to their category + global
    # Cross-category bleed causes ai-data domains to appear in games research etc.
    return _unknown(domain)


def _unknown(domain: str) -> dict:
    return {
        "domain":        domain,
        "tier":          "unknown",
        "boost":         0.40,
        "trust_reason":  "not_in_config",
        "retrieval_boost": 0.40,
        "domain_trust":  "unknown",
        "trust_score":   0.40,
        "content_type":  "other",
        "subcategory":   "other",
        "language":      "unknown",
    }


# -- Blocked domains --------------------------------------------------------

@lru_cache(maxsize=1)
def _get_blocked_patterns():
    blocked  = _load_blocked()
    title_re = [re.compile(p, re.IGNORECASE)
                for p in blocked.get("title_patterns", [])]
    url_re   = [re.compile(p, re.IGNORECASE)
                for p in blocked.get("url_patterns", [])]
    domains  = set(blocked.get("domains", []))
    return domains, url_re, title_re


def is_blocked(url: str, title: str = "") -> bool:
    """Returns True if URL or title should be filtered out."""
    domains, url_patterns, title_patterns = _get_blocked_patterns()

    try:
        parsed = urlparse(url)
        domain = parsed.netloc.lower()
        if domain.startswith("www."):
            domain = domain[4:]
        path   = parsed.path + "?" + (parsed.query or "")

        if domain in domains:
            return True
        # Check subdomain match
        for bd in domains:
            if domain.endswith("." + bd):
                return True

        for pat in url_patterns:
            if pat.search(path):
                return True
    except Exception:
        return True

    if title:
        for pat in title_patterns:
            if pat.search(title):
                return True

    return False


# -- Source config ----------------------------------------------------------

def get_sources_for_category(category: str) -> list:
    """Returns list of source adapter names for given category."""
    cfg  = _load_source_config()
    cats = cfg.get("categories", {})
    cat  = cats.get(category, cats.get("other", {}))
    return cat.get("sources", ["searxng", "reddit", "google_news"])


def get_rss_feeds(category: str) -> list:
    """Returns list of RSS feed dicts for given category -- reads from data/sources.json."""
    sources_file = os.path.join(DATA_DIR, "sources.json")
    try:
        with open(sources_file, encoding="utf-8") as f:
            data = json.load(f)
        feeds = data.get(category, [])
        # Normalize format -- sources.json has {url, domain, trust}
        # source_config.json had same format so no conversion needed
        return [f for f in feeds if isinstance(f, dict) and f.get("url")]
    except Exception:
        return []


def get_site_boosts(category: str) -> list:
    """Returns list of domains to boost in SearXNG queries."""
    cfg  = _load_source_config()
    cats = cfg.get("categories", {})
    cat  = cats.get(category, {})
    return cat.get("site_boost", [])


def get_defaults() -> dict:
    """Returns default settings (pages, chunk sizes etc.)."""
    return _load_source_config().get("defaults", {})
