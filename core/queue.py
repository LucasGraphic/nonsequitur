# core/queue.py -- zarządzanie kolejką artykułów
# Jedyne miejsce które czyta i zapisuje queue.json.
# Wszystkie inne moduły używają tylko tych funkcji.

import json
import uuid
import os
from datetime import datetime, timezone
from typing import Optional

from config import QUEUE_FILE

# -- Statusy ----------------------------------------------------------------
STATUS_PENDING    = "pending"     # dodany przez discovery, czeka na research
STATUS_RESEARCHED = "researched"  # deep research zrobiony, czeka na generate
STATUS_DONE       = "done"        # artykuł wygenerowany
STATUS_SKIPPED    = "skipped"     # ręcznie pominięty
STATUS_ERROR      = "error"       # błąd podczas research lub generate

ALL_STATUSES = [STATUS_PENDING, STATUS_RESEARCHED, STATUS_DONE, STATUS_SKIPPED, STATUS_ERROR]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _empty_queue() -> dict:
    return {"version": 1, "items": []}


# -- Odczyt / zapis --------------------------------------------------------

def load() -> dict:
    """Wczytuje queue.json. Jeśli nie istnieje, zwraca pustą strukturę."""
    if not os.path.exists(QUEUE_FILE):
        return _empty_queue()
    with open(QUEUE_FILE, "r", encoding="utf-8") as f:
        try:
            return json.load(f)
        except json.JSONDecodeError:
            print(f"  [queue] ERROR: {QUEUE_FILE} is corrupted -- creating new.")
            return _empty_queue()


def save(q: dict) -> None:
    """Zapisuje queue do pliku JSON (ładnie sformatowany)."""
    os.makedirs(os.path.dirname(QUEUE_FILE), exist_ok=True) if os.path.dirname(QUEUE_FILE) else None
    with open(QUEUE_FILE, "w", encoding="utf-8") as f:
        json.dump(q, f, ensure_ascii=False, indent=2)


# -- Operacje na elementach ------------------------------------------------

def add_item(
    topic: str,
    category: str,
    persona: str,
    section: str,
    model: str,
    notes: str = "",
    seed_urls: list = None,
    seed_queries: list = None,
    article_focus: str = "",
    upgrade_url: str = "",
    upgrade_mode: str = "",
    topic_slug: str = "",
    topic_tags: list = None,
    article_length: str = "medium",
    translate: bool = False,
    article_lang: str = "en",
) -> dict:
    """
    Add a new topic to the queue.
    article_focus: optional direction for LLM -- what to cover in the article.
    topic_slug: knowledge base slug (e.g. "fable", "nvidia-rtx-5090")
    topic_tags: CMS tags for this article (e.g. ["rpg", "open-world"])
    """
    q = load()

    for item in q["items"]:
        if item["topic"].lower() == topic.lower() and item["section"] == section:
            print(f"  [queue] Duplicate skipped: {topic[:60]}")
            return item

    item = {
        "id":             str(uuid.uuid4())[:8],
        "topic":          topic,
        "category":       category,
        "persona":        persona,
        "section":        section,
        "model":          model,
        "notes":          notes,
        "seed_urls":      seed_urls or [],
        "seed_queries":   seed_queries or [],
        "article_focus":  article_focus,
        "upgrade_url":    upgrade_url,
        "upgrade_mode":   upgrade_mode,
        "topic_slug":     topic_slug or "",
        "topic_tags":     topic_tags or [],
        "article_length": article_length,
        "translate":      translate,
        "article_lang":   article_lang,
        "status":         STATUS_PENDING,
        "added_at":       _now(),
        "researched_at":  None,
        "done_at":        None,
        "error":          None,
    }

    q["items"].append(item)
    save(q)
    return item


def update_status(item_id: str, status: str, error: Optional[str] = None) -> bool:
    """
    Aktualizuje status elementu w queue.
    Ustawia odpowiedni timestamp (researched_at / done_at).
    Zwraca True jeśli znaleziono i zaktualizowano.
    """
    if status not in ALL_STATUSES:
        raise ValueError(f"Nieznany status: {status}")

    q = load()
    for item in q["items"]:
        if item["id"] == item_id:
            item["status"] = status
            item["error"]  = error
            if status == STATUS_RESEARCHED:
                item["researched_at"] = _now()
            elif status == STATUS_DONE:
                item["done_at"] = _now()
            save(q)
            return True
    return False




def update_field(item_id: str, field: str, value) -> bool:
    """Aktualizuje dowolne pole itemu. Zwraca True jeśli znaleziono."""
    q = load()
    for item in q["items"]:
        if item["id"] == item_id:
            item[field] = value
            save(q)
            return True
    return False

def remove_item(item_id: str) -> bool:
    """Usuwa element z queue po ID. Zwraca True jeśli znaleziono."""
    q = load()
    before = len(q["items"])
    q["items"] = [i for i in q["items"] if i["id"] != item_id]
    if len(q["items"]) < before:
        save(q)
        return True
    return False


def clear_done() -> int:
    """Usuwa wszystkie elementy ze statusem 'done'. Zwraca liczbę usuniętych."""
    q = load()
    before = len(q["items"])
    q["items"] = [i for i in q["items"] if i["status"] != STATUS_DONE]
    removed = before - len(q["items"])
    if removed:
        save(q)
    return removed


def reset_errors() -> int:
    """Resets all 'error' items back to 'pending'. Returns count reset."""
    q = load()
    count = 0
    for item in q["items"]:
        if item["status"] == STATUS_ERROR:
            item["status"] = STATUS_PENDING
            item["error"]  = None
            count += 1
    if count:
        save(q)
    return count


def reset_researched() -> int:
    """Resets all 'researched' items back to 'pending'. Returns count reset."""
    q = load()
    count = 0
    for item in q["items"]:
        if item["status"] == STATUS_RESEARCHED:
            item["status"]        = STATUS_PENDING
            item["researched_at"] = None
            count += 1
    if count:
        save(q)
    return count


def clear_all() -> int:
    """Czyści całą queue. Zwraca liczbę usuniętych."""
    q = load()
    count = len(q["items"])
    q["items"] = []
    save(q)
    return count


# -- Filtry ----------------------------------------------------------------

def get_by_status(status: str) -> list:
    """Zwraca listę itemów o podanym statusie."""
    return [i for i in load()["items"] if i["status"] == status]


def get_pending()    -> list: return get_by_status(STATUS_PENDING)
def get_researched() -> list: return get_by_status(STATUS_RESEARCHED)
def get_all()        -> list: return load()["items"]


# -- Wyświetlanie ----------------------------------------------------------

STATUS_ICON = {
    STATUS_PENDING:    "o",
    STATUS_RESEARCHED: "◑",
    STATUS_DONE:       "*",
    STATUS_SKIPPED:    "--",
    STATUS_ERROR:      "[X]",
}

STATUS_LABEL = {
    STATUS_PENDING:    "pending",
    STATUS_RESEARCHED: "researched",
    STATUS_DONE:       "done",
    STATUS_SKIPPED:    "skipped",
    STATUS_ERROR:      "error",
}


def print_queue(filter_status: Optional[str] = None) -> None:
    """Prints a readable queue list to terminal."""
    items = load()["items"]
    if filter_status:
        items = [i for i in items if i["status"] == filter_status]

    if not items:
        print("  Queue is empty.")
        return

    print(f"\n  {'ID':<10} {'S':<2} {'STATUS':<12} {'CAT':<12} {'TOPIC'}")
    print(f"  {'-'*8}   {'-'*10}   {'-'*10}   {'-'*50}")

    for item in items:
        icon   = STATUS_ICON.get(item["status"], "?")
        status = STATUS_LABEL.get(item["status"], item["status"])
        cat    = item.get("category", "")[:10]
        topic  = item["topic"][:60]
        err    = f"  <- {item['error'][:40]}" if item.get("error") else ""
        print(f"  [{item['id']}]  {icon}  {status:<12} [{cat:<10}]  {topic}{err}")

    all_items = load()["items"]
    counts = {}
    for s in ALL_STATUSES:
        n = sum(1 for i in all_items if i["status"] == s)
        if n:
            counts[s] = n
    summary = "  . ".join(f"{STATUS_ICON[s]} {n} {s}" for s, n in counts.items())
    print(f"\n  {summary}\n")
