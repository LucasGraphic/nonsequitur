# discovery/selector.py -- interaktywny terminal UI do wyboru tematów
# Pokazuje przefiltrowane wyniki i pozwala użytkownikowi wybrać tematy do queue.

import sys
from typing import Optional


def _print_items(items: list, picked: set) -> None:
    from urllib.parse import urlparse as _up
    print()
    for idx, item in enumerate(items):
        marker = "►" if idx in picked else " "
        score  = item.get("_score", 0)
        source = item.get("source", "")[:12]
        title  = item["title"][:60]
        url    = item.get("url", "")
        try:
            domain = _up(url).netloc.lstrip("www.")[:28] if url else ""
        except Exception:
            domain = ""
        print(f"  {marker} [{idx+1:>3}]  {title:<60}  [{source:<12}]  {domain:<28}  ({score:.1f})")
    print()


def _print_status(picked: set, items: list) -> None:
    if not picked:
        print("  (nothing selected)")
    else:
        print(f"  Selected ({len(picked)}):")
        for i in sorted(picked):
            print(f"   + [{i+1:>3}] {items[i]['title'][:70]}")
    print()


def select_topics(items: list) -> list:
    """
    Interactive topic selection UI.

    Commands:
      1-N          -- toggle single number
      1-5          -- toggle range
      1,3,7        -- toggle list
      a            -- select all
      c            -- deselect all
      p            -- preview selected
      Enter        -- confirm and finish
      q / skip     -- exit without adding anything
    """
    if not items:
        print("  No results to select from.")
        return []

    picked: set = set()

    _print_items(items, picked)
    print("  Commands: number (1), range (1-5), list (1,3,7) | a=all  c=clear  p=preview  Enter=confirm  q=skip")
    print()

    while True:
        try:
            raw = input("  > ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print()
            return []

        if raw in ("q", "quit", "skip", "n"):
            print("  Skipped -- nothing added to queue.")
            return []

        if raw == "" or raw in ("y", "yes", "ok", "enter"):
            if not picked:
                confirm = input("  Nothing selected. Skip this category? [Y/n]: ").strip().lower()
                if confirm in ("", "y", "yes"):
                    return []
                continue
            break

        if raw == "a":
            picked = set(range(len(items)))
            _print_status(picked, items)
            continue

        if raw == "c":
            picked.clear()
            _print_status(picked, items)
            continue

        if raw == "p":
            _print_items(items, picked)
            _print_status(picked, items)
            continue

        indices = _parse_selection(raw, len(items))
        if indices is None:
            print("  Invalid format. Examples: 3  /  1-5  /  2,4,7")
            continue

        for idx in indices:
            if idx in picked:
                picked.remove(idx)
            else:
                picked.add(idx)

        _print_status(picked, items)

    selected = [items[i] for i in sorted(picked)]
    print(f"\n  [OK] Selected {len(selected)} topics for queue.\n")
    return selected


def _parse_selection(raw: str, max_idx: int) -> Optional[list]:
    """Parse selection string -> list of 0-based indices. Returns None if invalid."""
    indices = []

    try:
        if "," in raw and "-" not in raw.replace("-", ""):
            parts = [p.strip() for p in raw.split(",")]
            for p in parts:
                n = int(p)
                if n < 1 or n > max_idx:
                    return None
                indices.append(n - 1)
            return indices

        if "-" in raw:
            parts = raw.split("-")
            if len(parts) == 2:
                a, b = int(parts[0].strip()), int(parts[1].strip())
                if a < 1 or b > max_idx or a > b:
                    return None
                return list(range(a - 1, b))

        n = int(raw)
        if n < 1 or n > max_idx:
            return None
        return [n - 1]

    except (ValueError, TypeError):
        return None


def _fetch_ollama_models(ollama_url: str) -> list:
    """Fetch installed models from Ollama /api/tags. Returns list of names."""
    try:
        import requests as _req
        r = _req.get(f"{ollama_url}/api/tags", timeout=5)
        r.raise_for_status()
        return sorted([m["name"] for m in r.json().get("models", [])])
    except Exception:
        return []


def _load_persona_descriptions() -> dict:
    """
    Load persona descriptions from data/personas/*.md files.
    Returns dict: {name: first_line_description}
    """
    import os
    import re

    base_dir     = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    personas_dir = os.path.join(base_dir, "data", "personas")

    from config import PERSONAS
    descriptions = {}

    for name, info in PERSONAS.items():
        path = os.path.join(personas_dir, f"{name}.md")
        if os.path.exists(path):
            try:
                with open(path, encoding="utf-8") as f:
                    content = f.read()
                # Find first non-comment, non-empty line after frontmatter
                for line in content.split("\n"):
                    line = line.strip()
                    if line and not line.startswith("#") and not line.startswith("*"):
                        descriptions[name] = line[:60]
                        break
                else:
                    descriptions[name] = info["label"]
            except Exception:
                descriptions[name] = info["label"]
        else:
            descriptions[name] = info["label"]

    return descriptions


def ask_persona_and_model(default_persona: str = "lukasz",
                          default_model: str = "") -> tuple:
    """
    Ask for persona and LLM model.
    Returns (persona_name, model_name).
    Model list fetched live from Ollama -- no hardcoded MODELS dict needed.
    """
    from config import PERSONAS, OLLAMA_URL, fetch_ollama_models, EMBED_FILTER

    import os
    base_dir     = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    personas_dir = os.path.join(base_dir, "data", "personas")

    # -- Count chunks per persona from Qdrant -----------------------------
    def _persona_chunk_count(persona_name: str) -> int:
        try:
            from config import QDRANT_URL, PERSONA_COLLECTION
            import requests as _req
            if persona_name != "lukasz":
                return 0
            count_url = f"{QDRANT_URL}/collections/{PERSONA_COLLECTION}/points/count"
            rc = _req.post(count_url, json={"exact": True}, timeout=4)
            rc.raise_for_status()
            return rc.json().get("result", {}).get("count", 0)
        except Exception:
            return -1

    print()
    print("  -- Persona --------------------------------------------------")
    persona_list = list(PERSONAS.items())
    for idx, (name, info) in enumerate(persona_list, 1):
        marker = " <-" if name == default_persona else ""
        count  = _persona_chunk_count(name)
        if count == -1:
            status = "  [qdrant unavailable]"
        elif count == 0:
            status = "  [no chunks yet]"
        else:
            status = f"  ({count} chunks)"
        print(f"  [{idx}] {name:<12} -- {info['label']}{marker}{status}")

    raw = input(f"  Choose persona (Enter = {default_persona}): ").strip()
    persona_name = default_persona
    if raw.isdigit() and 1 <= int(raw) <= len(persona_list):
        persona_name = persona_list[int(raw) - 1][0]
    elif raw and raw in PERSONAS:
        persona_name = raw

    # -- Model selection -- live from Ollama ----------------------------
    print()
    print("  -- LLM model ------------------------------------------------")

    installed = _fetch_ollama_models(OLLAMA_URL)  # list of model name strings
    # Filter out embedding/reranker models
    gen_models = [m for m in installed
                  if not any(f in m.lower() for f in EMBED_FILTER)]

    from config import DEFAULT_MODEL
    _default = default_model or DEFAULT_MODEL
    # If default_model is a legacy key like "MAX", fall back to DEFAULT_MODEL
    if _default not in gen_models:
        _default = DEFAULT_MODEL if DEFAULT_MODEL in gen_models else (gen_models[0] if gen_models else "")

    if gen_models:
        for idx, name in enumerate(gen_models, 1):
            marker = " <-" if name == _default else ""
            print(f"  [{idx}] {name}{marker}")
    else:
        print("  [no models found -- Ollama offline?]")

    custom_nr = len(gen_models) + 1
    print(f"  [{custom_nr}] Custom -- type any Ollama model name")

    default_nr = next((i + 1 for i, n in enumerate(gen_models) if n == _default), 1)
    raw = input(f"  Choose model (Enter = {default_nr}): ").strip()

    if raw == str(custom_nr):
        custom_name = input("  Model name (e.g. qwen3.5:32b): ").strip()
        return persona_name, custom_name if custom_name else _default

    if raw.isdigit() and 1 <= int(raw) <= len(gen_models):
        return persona_name, gen_models[int(raw) - 1]
    elif raw == "":
        return persona_name, _default
    else:
        print("  Invalid choice -- using default.")
        return persona_name, _default


# -- Legacy shim -- kept for any callers that still use ask_style_and_model -
def ask_style_and_model(styles: dict, models: dict,
                        default_style: str = "1",
                        default_model: str = "MAX") -> tuple:
    """
    Deprecated -- forwards to ask_persona_and_model().
    Returns (persona_name, model_key) for backwards compatibility.
    style_key position now carries persona_name.
    """
    persona_name, model_key = ask_persona_and_model(
        default_persona = "lukasz",
        default_model   = default_model,
    )
    return persona_name, model_key
