"""
clip.py -- Pobierz URL -> zweryfikuj tekst -> zapisz do Qdrant knowledge base

Użycie:
    python clip.py --url "https://dev.epicgames.com/..."
    python clip.py --url "https://..." --category software --tag ue5
    python clip.py --url "https://..." --dry-run

Jeśli category/tag nie podane -- pyta interaktywnie.
Jeśli fetch się nie uda -- otwiera edytor do wklejenia tekstu ręcznie.
"""

import argparse
import hashlib
import json
import os
import re
import struct
import sys
import tempfile
import subprocess
from datetime import datetime, timezone
from urllib.parse import urlparse

import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import (
    EMBED_DIM,
    EMBED_MODEL,
    OLLAMA_EMBED_URL,
    QDRANT_URL,
    RESEARCH_CHUNK_SIZE,
    VALKEY_URL,
)

# FETCH_SERVICE_URL nie jest jeszcze w config.py -- dodaj tam lub zostaw tu
try:
    from config import FETCH_SERVICE_URL
except ImportError:
    FETCH_SERVICE_URL = "http://10.0.0.195:8765/fetch"

# --- Valkey cache ---
try:
    import redis
    _valkey = redis.from_url(VALKEY_URL, decode_responses=False,
                             socket_connect_timeout=2, socket_timeout=2)
    _valkey.ping()
    VALKEY_OK = True
except Exception:
    VALKEY_OK = False

VALID_CATEGORIES = {
    "games", "ai-data", "software", "security",
    "hardware", "entertainment", "photography",
    "drone", "3d", "other",
}

CHUNK_SIZE    = RESEARCH_CHUNK_SIZE
CHUNK_OVERLAP = 80

# Kolory terminala
GREEN  = "\033[92m"
YELLOW = "\033[93m"
RED    = "\033[91m"
CYAN   = "\033[96m"
RESET  = "\033[0m"
BOLD   = "\033[1m"


# ---------------------------------------------
# FETCH
# ---------------------------------------------

def fetch_via_service(url: str) -> str:
    """Próbuje pobrać tekst przez fetch_service (Playwright) na Ubuntu."""
    try:
        r = requests.post(
            FETCH_SERVICE_URL,
            json={"url": url, "timeout": 15},
            timeout=20,
        )
        if r.status_code == 200:
            data = r.json()
            text = data.get("text", "").strip()
            if len(text) > 200:
                return text
    except Exception as e:
        print(f"  {YELLOW}[fetch] Playwright service niedostępny: {e}{RESET}")
    return ""


def fetch_via_requests(url: str) -> str:
    """Fallback: requests + trafilatura."""
    try:
        import trafilatura
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                          "AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36"
        }
        r = requests.get(url, timeout=12, headers=headers)
        r.raise_for_status()
        text = trafilatura.extract(
            r.text,
            include_comments=False,
            include_tables=True,
            favor_precision=True,
        )
        if text and len(text.strip()) > 200:
            return text.strip()[:12000]
    except ImportError:
        pass
    except Exception:
        pass

    # Ostateczny fallback -- strip HTML
    try:
        headers = {"User-Agent": "Mozilla/5.0 (compatible; ArticleAgent/1.0)"}
        r = requests.get(url, timeout=10, headers=headers)
        r.raise_for_status()
        text = re.sub(r"<[^>]+>", " ", r.text)
        text = re.sub(r"\s+", " ", text).strip()
        if len(text) > 200:
            return text[:12000]
    except Exception:
        pass

    return ""


def fetch_url(url: str) -> str:
    """Próbuje Playwright service, potem requests fallback."""
    print(f"  -> Pobieranie przez Playwright service...")
    text = fetch_via_service(url)
    if text:
        print(f"  {GREEN}[OK] Playwright: {len(text)} znaków{RESET}")
        return text

    print(f"  -> Fallback: requests + trafilatura...")
    text = fetch_via_requests(url)
    if text:
        print(f"  {GREEN}[OK] Requests: {len(text)} znaków{RESET}")
        return text

    return ""


# ---------------------------------------------
# INTERACTIVE
# ---------------------------------------------

def _load_tags() -> dict:
    try:
        tags_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "tags.json")
        with open(tags_file, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def ask_category() -> str:
    # Kolejność kategorii -- najpierw najczęściej używane
    cats = ["ai-data", "software", "games", "hardware", "security",
            "entertainment", "photography", "drone", "3d", "other"]
    tags_data = _load_tags()
    print(f"\n{BOLD}Kategoria:{RESET}")
    for i, c in enumerate(cats, 1):
        tag_count = len(tags_data.get(c, []))
        print(f"  {CYAN}{i}{RESET}. {c:<20} ({tag_count} tagów)")
    while True:
        choice = input(f"  Numer [{CYAN}1-{len(cats)}{RESET}]: ").strip()
        if choice.isdigit() and 1 <= int(choice) <= len(cats):
            return cats[int(choice) - 1]
        print(f"  {RED}Wpisz numer 1-{len(cats)}{RESET}")


def ask_tag(category: str) -> str:
    tags_data = _load_tags()
    cat_tags  = tags_data.get(category, [])
    if not cat_tags:
        return ""

    print(f"\n{BOLD}Tag:{RESET}")
    for i, t in enumerate(cat_tags, 1):
        print(f"  {CYAN}{i}{RESET}. {t}")
    print(f"  {CYAN}0{RESET}. (brak tagu)")

    while True:
        choice = input(f"  Numer [{CYAN}0-{len(cat_tags)}{RESET}]: ").strip()
        if choice == "0" or choice == "":
            return ""
        if choice.isdigit() and 1 <= int(choice) <= len(cat_tags):
            return cat_tags[int(choice) - 1]
        print(f"  {RED}Wpisz numer 0-{len(cat_tags)}{RESET}")


def open_editor_for_text(url: str, existing: str = "") -> str:
    """Wklejanie tekstu bezpośrednio w terminalu. Zakończ linią '---'."""
    print(f"\n  {CYAN}Wklej tekst artykułu (zakończ linią zawierającą tylko ---){RESET}")
    print(f"  URL: {url}")
    if existing:
        print(f"  {YELLOW}Obecny tekst ({len(existing)} znaków) zostanie zastąpiony{RESET}")
    print()

    lines = []
    try:
        while True:
            line = input()
            if line.strip() == "---":
                break
            lines.append(line)
    except (EOFError, KeyboardInterrupt):
        pass

    text = "\n".join(lines).strip()
    return text


def show_preview(text: str, chars: int = 800) -> None:
    """Pokazuje podgląd tekstu."""
    print(f"\n{BOLD}{'-'*60}{RESET}")
    print(f"{CYAN}PODGLĄD TEKSTU ({len(text)} znaków):{RESET}")
    print(f"{'-'*60}")
    print(text[:chars])
    if len(text) > chars:
        print(f"\n{YELLOW}... [{len(text) - chars} znaków więcej]{RESET}")
    print(f"{'-'*60}")


# ---------------------------------------------
# EMBED + UPSERT (identyczne z manual_feed.py)
# ---------------------------------------------

def embed(text: str) -> list | None:
    cache_key = None
    if VALKEY_OK:
        cache_key = b"emb:" + hashlib.sha256(text.encode()).digest()
        cached = _valkey.get(cache_key)
        if cached:
            floats = struct.unpack(f"{len(cached)//4}f", cached)
            return list(floats)
    try:
        r = requests.post(
            f"{OLLAMA_EMBED_URL}/api/embed",
            json={"model": EMBED_MODEL, "input": text},
            timeout=30,
        )
        r.raise_for_status()
        vec = r.json()["embeddings"][0]
        if VALKEY_OK and cache_key:
            packed = struct.pack(f"{len(vec)}f", *vec)
            _valkey.setex(cache_key, 86400 * 30, packed)
        return vec
    except Exception as e:
        print(f"  {RED}[embed] ERROR: {e}{RESET}")
        return None


def chunk_text(text: str) -> list[str]:
    chunks, start = [], 0
    while start < len(text):
        end = start + CHUNK_SIZE
        chunks.append(text[start:end].strip())
        start += CHUNK_SIZE - CHUNK_OVERLAP
    return [c for c in chunks if len(c) > 80]


def ensure_collection(collection: str) -> bool:
    url = f"{QDRANT_URL}/collections/{collection}"
    r = requests.get(url, timeout=10)
    if r.status_code == 200:
        return True
    payload = {
        "vectors": {
            "dense": {"size": EMBED_DIM, "distance": "Cosine"}
        },
        "sparse_vectors": {
            "sparse": {"index": {"on_disk": False}}
        },
    }
    r = requests.put(url, json=payload, timeout=10)
    return r.status_code in (200, 201)


def upsert_to_qdrant(
    text: str, url: str, category: str, tag: str,
    freshness: str, content_date: str, collection: str,
    dry_run: bool,
) -> int:
    domain  = urlparse(url).netloc.lstrip("www.")
    words   = [w for w in re.findall(r"\w+", url.split("/")[-1].replace("-", " ")) if len(w) > 2]
    title   = " ".join(words[:6]) or domain
    subject = " ".join([w for w in re.findall(r"\w+", title) if len(w) > 2][:4])
    now_iso = datetime.now(timezone.utc).isoformat()
    item_id = hashlib.md5(url.encode()).hexdigest()[:8]

    raw_chunks = chunk_text(text)
    print(f"\n  -> {len(raw_chunks)} chunków do wgrania...")

    if dry_run:
        print(f"  {YELLOW}[dry-run] {len(raw_chunks)} chunków -> {collection}{RESET}")
        return len(raw_chunks)

    if not ensure_collection(collection):
        print(f"  {RED}Nie można utworzyć kolekcji: {collection}{RESET}")
        return 0

    points = []
    for idx, chunk in enumerate(raw_chunks):
        vec = embed(chunk)
        if vec is None:
            continue

        # sparse BM25
        ws    = re.findall(r"\w+", chunk.lower())
        total = max(len(ws), 1)
        tf: dict[str, int] = {}
        for w in ws:
            if len(w) > 2:
                tf[w] = tf.get(w, 0) + 1
        idx_map: dict[int, float] = {}
        for w, count in tf.items():
            i = abs(hash(w)) % (2**20)
            idx_map[i] = idx_map.get(i, 0.0) + round(count / total, 6)

        point_id = abs(hash(url + str(idx))) % (2**53)
        points.append({
            "id": point_id,
            "vector": {
                "dense":  vec,
                "sparse": {
                    "indices": list(idx_map.keys()),
                    "values":  [round(v, 6) for v in idx_map.values()],
                },
            },
            "payload": {
                "topic":           title,
                "category":        category,
                "item_id":         item_id,
                "url":             url,
                "title":           title,
                "source":          "clip",
                "text":            chunk,
                "domain":          domain,
                "domain_trust":    "press",
                "trust_score":     1.0,
                "trust_reason":    "clip_manual",
                "content_type":    "article",
                "subcategory":     tag,
                "language":        "en",
                "retrieval_boost": 1.0,
                "indexed_at":      now_iso,
                "content_date":    content_date,
                "freshness":       freshness,
                "subject":         subject,
                "version_tag":     "",
                "chunk_idx":       idx,
                "knowledge":       True,
            },
        })
        print(f"  [embed] {idx+1}/{len(raw_chunks)}...", end="\r")

    if not points:
        return 0

    r = requests.put(
        f"{QDRANT_URL}/collections/{collection}/points",
        json={"points": points},
        timeout=60,
    )
    if r.status_code in (200, 201):
        print(f"  {GREEN}[OK] {len(points)} chunków -> {collection}{RESET}        ")
        return len(points)
    print(f"  {RED}[qdrant] Błąd: {r.text}{RESET}")
    return 0


# ---------------------------------------------
# MAIN
# ---------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="clip.py -- URL -> Qdrant knowledge base")
    parser.add_argument("--url",      required=True, help="URL do pobrania")
    parser.add_argument("--category", default="",    help="Kategoria (games/software/ai-data/...)")
    parser.add_argument("--tag",      default="",    help="Tag z tags.json (opcjonalny)")
    parser.add_argument("--freshness",default="reference", choices=["reference", "news"])
    parser.add_argument("--evergreen",action="store_true", help="Zapisz do knowledge_evergreen")
    parser.add_argument("--dry-run",  action="store_true", help="Tylko podgląd, bez zapisu")
    args = parser.parse_args()

    url = args.url.strip()
    print(f"\n{BOLD}clip.py{RESET} {'[DRY-RUN] ' if args.dry_run else ''}-> {url}\n")

    # 1. Fetch
    text = fetch_url(url)

    if not text:
        print(f"  {YELLOW}Fetch nieudany -- wklej tekst ręcznie{RESET}")
        text = open_editor_for_text(url)

    if not text or len(text) < 100:
        print(f"  {RED}Za mało tekstu ({len(text)} znaków). Przerywam.{RESET}")
        sys.exit(1)

    # 2. Podgląd
    show_preview(text)

    # 3. Czy tekst jest OK?
    confirm = input(f"\n  Tekst OK? [{GREEN}T{RESET}/n/r(wklej ręcznie)]: ").strip().lower()
    if confirm == "n":
        print("  Anulowano.")
        sys.exit(0)
    if confirm == "r":
        text = open_editor_for_text(url)
        if not text or len(text) < 100:
            print(f"  {RED}Za mało tekstu. Przerywam.{RESET}")
            sys.exit(1)
        show_preview(text)

    # 4. Kategoria
    category = args.category.strip().lower()
    if not category or category not in VALID_CATEGORIES:
        category = ask_category()

    # 5. Tag
    tag = args.tag.strip().lower()
    if not tag:
        tag = ask_tag(category)

    # 6. Kolekcja
    if args.evergreen:
        collection = "knowledge_evergreen"
    else:
        collection = f"knowledge_{category}"

    content_date = datetime.now(timezone.utc).date().isoformat()

    # 7. Podsumowanie przed zapisem
    print(f"\n{BOLD}Do zapisania:{RESET}")
    print(f"  URL:        {url}")
    print(f"  Kategoria:  {category}")
    print(f"  Tag:        {tag or '(brak)'}")
    print(f"  Kolekcja:   {collection}")
    print(f"  Freshness:  {args.freshness}")
    print(f"  Tekst:      {len(text)} znaków")
    print(f"  Chunki:     ~{len(chunk_text(text))}")

    if not args.dry_run:
        go = input(f"\n  Zapisać do Qdrant? [{GREEN}T{RESET}/n]: ").strip().lower()
        if go == "n":
            print("  Anulowano.")
            sys.exit(0)

    # 8. Upsert
    added = upsert_to_qdrant(
        text         = text,
        url          = url,
        category     = category,
        tag          = tag,
        freshness    = args.freshness,
        content_date = content_date,
        collection   = collection,
        dry_run      = args.dry_run,
    )

    if added:
        print(f"\n  {GREEN}{BOLD}[OK] Gotowe -- {added} chunków w {collection}{RESET}")
    elif args.dry_run:
        print(f"\n  {YELLOW}Dry-run zakończony.{RESET}")
    else:
        print(f"\n  {RED}Błąd zapisu.{RESET}")


if __name__ == "__main__":
    main()
