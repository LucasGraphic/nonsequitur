# menus/settings.py -- Settings menu: API provider, embedding model
import os
import requests as _req


def _active_persona_menu() -> None:
    import re as _re, os as _os
    try:
        from config import QDRANT_URL, PERSONA_COLLECTION
        from qdrant_client import QdrantClient
        client = QdrantClient(url=QDRANT_URL)
        all_cols   = [c.name for c in client.get_collections().collections]
        pcols      = sorted([c for c in all_cols if c.startswith("persona_")])
    except Exception as e:
        print(f"  ERROR: {e}"); return

    print()
    print("  +==========================================================+")
    print("  |  ACTIVE PERSONA                                          |")
    print("  +==========================================================+")
    print(f"  |  Current: {PERSONA_COLLECTION[:46]:<46}|")
    print("  +==========================================================+")
    for i, col in enumerate(pcols, 1):
        try:
            n = client.get_collection(col).points_count
        except Exception:
            n = 0
        marker = " <-" if col == PERSONA_COLLECTION else ""
        print(f"  |  [{i}] {col:<38} {n:>4} chunks{marker:3}|")
    print("  |  [Enter]  Back                                           |")
    print("  +==========================================================+")
    print()

    raw = input("  Choice: ").strip()
    if not raw: return
    if raw.isdigit() and 1 <= int(raw) <= len(pcols):
        new_col = pcols[int(raw) - 1]
    elif raw in pcols:
        new_col = raw
    elif not raw.startswith("persona_") and f"persona_{raw}" in pcols:
        new_col = f"persona_{raw}"
    else:
        print("  Invalid choice."); return

    config_path = _os.path.join(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))), "config.py")
    try:
        with open(config_path, "rb") as f:
            cfg = f.read().replace(b"\r\n", b"\n").replace(b"\r", b"\n").decode("utf-8")
        cfg = _re.sub('PERSONA_COLLECTION\s*=\s*["\']+.*?["\']+', 'PERSONA_COLLECTION = "' + new_col + '"', cfg)
        with open(config_path, "wb") as f:
            f.write(cfg.replace("\n", "\r\n").encode("utf-8"))
        print(f"  [OK] Active persona -> {new_col}")
        print("  Restart agent for change to take effect in generate pipeline.")
    except Exception as e:
        print(f"  ERROR writing config: {e}")

def _settings_menu() -> None:
    """Main settings menu -- API provider, embedding model, defaults."""
    while True:
        print()
        print("  +==========================================+")
        print("  |  SETTINGS                                |")
        print("  +==========================================+")
        print("  |  [1]  API provider (generate)            |")
        print("  |  [2]  Embedding model                    |")
        print("  |  [3]  Scoring model                      |")
        print("  |  [4]  Default generate model             |")
        print("  |  [5]  Scoring think (on/off)             |")
        print("  |  [6]  Active persona                     |")
        print("  |  [Enter]  Back                           |")
        print("  +==========================================+")
        print()
        raw = input("  Choice: ").strip().lower()
        if not raw or raw in ("q", "back"):
            break
        elif raw == "1":
            _api_settings_menu()
        elif raw == "4":
            _default_model_menu()
            continue
        elif raw == "5":
            _scoring_think_menu()
            continue
        elif raw == "6":
            _active_persona_menu()
            continue
        elif raw == "3":
            _scoring_model_menu()
            continue
        elif raw == "2":
            _embedding_model_menu()
            continue
        else:
            print("  Unknown option.")



def _scoring_model_menu() -> None:
    from config import SCORING_MODEL, fetch_ollama_models, OLLAMA_URL
    import re as _re
    import os as _os

    print()
    print("  Fetching available models from Ollama...")
    models = fetch_ollama_models(OLLAMA_URL)
    model_list = sorted(models.keys())

    print()
    print("  +==========================================================+")
    print("  |  SCORING MODEL                                           |")
    print("  +==========================================================+")
    print(f"  |  Current: {SCORING_MODEL[:46]:<46}|")
    print("  |  'same' = use same model as generate (per-item)          |")
    print("  +==========================================================+")
    print("  |  [0]  same as generate (default)                         |")
    for i, name in enumerate(model_list, 1):
        marker = " <-" if name == SCORING_MODEL else ""
        print(f"  |  [{i}] {name[:46]:<46}{marker}|")
    print("  |  [Enter]  Back                                           |")
    print("  +==========================================================+")
    print()

    raw = input("  Choice: ").strip()
    if not raw:
        return

    if raw == "0":
        new_model = ""
        label = "same as generate"
    elif raw.isdigit() and 1 <= int(raw) <= len(model_list):
        new_model = model_list[int(raw) - 1]
        label = new_model
    else:
        print("  Invalid choice.")
        return

    config_path = _os.path.join(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))), "config.py")
    try:
        with open(config_path, "rb") as fh:
            cfg_raw = fh.read()
        cfg = cfg_raw.replace(b"\r\n", b"\n").replace(b"\r", b"\n").decode("utf-8")
        if new_model:
            cfg = _re.sub(r'SCORING_MODEL\s*=\s*["\'].*?["\']', f'SCORING_MODEL = "{new_model}"', cfg)
        else:
            cfg = _re.sub(r'SCORING_MODEL\s*=\s*["\'].*?["\']', 'SCORING_MODEL = ""', cfg)
        cfg_bytes = cfg.replace("\n", "\r\n").encode("utf-8")
        with open(config_path, "wb") as fh:
            fh.write(cfg_bytes)
        print(f"  [OK] SCORING_MODEL set to: {label}")
        print("  Restart agent to apply.")
    except Exception as e:
        print(f"  [X] Could not update config.py: {e}")

    input("\n  [Enter] to continue: ")



def _default_model_menu() -> None:
    from config import DEFAULT_MODEL, fetch_ollama_models, OLLAMA_URL
    import re as _re
    import os as _os

    print()
    print("  Fetching available models from Ollama...")
    models = fetch_ollama_models(OLLAMA_URL)
    model_list = sorted(models.keys())

    print()
    print("  +==========================================================+")
    print("  |  DEFAULT GENERATE MODEL                                  |")
    print("  +==========================================================+")
    print(f"  |  Current: {DEFAULT_MODEL[:46]:<46}|")
    print("  +==========================================================+")
    for i, name in enumerate(model_list, 1):
        marker = " <-" if name == DEFAULT_MODEL else ""
        print(f"  |  [{i}] {name[:46]:<46}{marker}|")
    print("  |  [Enter]  Back                                           |")
    print("  +==========================================================+")
    print()

    raw = input("  Choice: ").strip()
    if not raw or not raw.isdigit():
        return
    idx = int(raw) - 1
    if not (0 <= idx < len(model_list)):
        print("  Invalid choice.")
        return

    new_model = model_list[idx]
    if new_model == DEFAULT_MODEL:
        print(f"  Already using {new_model}.")
        return

    config_path = _os.path.join(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))), "config.py")
    try:
        with open(config_path, "rb") as fh:
            cfg_raw = fh.read()
        cfg = cfg_raw.replace(b"\r\n", b"\n").replace(b"\r", b"\n").decode("utf-8")
        cfg = _re.sub(r'DEFAULT_MODEL\s*=\s*["\'].*?["\']', f'DEFAULT_MODEL = "{new_model}"', cfg)
        cfg_bytes = cfg.replace("\n", "\r\n").encode("utf-8")
        with open(config_path, "wb") as fh:
            fh.write(cfg_bytes)
        print(f"  [OK] DEFAULT_MODEL set to: {new_model}")
        print("  Restart agent to apply.")
    except Exception as e:
        print(f"  [X] Could not update config.py: {e}")

    input("\n  [Enter] to continue: ")


def _scoring_think_menu() -> None:
    from config import SCORING_THINK
    import re as _re
    import os as _os

    current = SCORING_THINK
    print()
    print("  +==========================================================+")
    print("  |  SCORING THINK                                           |")
    print("  +==========================================================+")
    print(f"  |  Current: {'ON' if current else 'OFF':<48}|")
    print("  |  ON  = slower, more accurate (dense models only)         |")
    print("  |  OFF = fast, less accurate (MoE models)                  |")
    print("  +==========================================================+")
    print("  |  [1]  ON                                                 |")
    print("  |  [2]  OFF                                                |")
    print("  |  [Enter]  Back                                           |")
    print("  +==========================================================+")
    print()

    raw = input("  Choice: ").strip()
    if not raw:
        return
    if raw == "1":
        new_val = "True"
    elif raw == "2":
        new_val = "False"
    else:
        print("  Invalid choice.")
        return

    config_path = _os.path.join(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))), "config.py")
    try:
        with open(config_path, "rb") as fh:
            cfg_raw = fh.read()
        cfg = cfg_raw.replace(b"\r\n", b"\n").replace(b"\r", b"\n").decode("utf-8")
        cfg = _re.sub(r'SCORING_THINK\s*=\s*(True|False)', f'SCORING_THINK = {new_val}', cfg)
        cfg_bytes = cfg.replace("\n", "\r\n").encode("utf-8")
        with open(config_path, "wb") as fh:
            fh.write(cfg_bytes)
        print(f"  [OK] SCORING_THINK set to: {new_val}")
        print("  Restart agent to apply.")
    except Exception as e:
        print(f"  [X] Could not update config.py: {e}")

    input("\n  [Enter] to continue: ")


def _embedding_model_menu() -> None:
    """Select embedding model -- warns about Qdrant wipe requirement."""
    import requests as _req
    from config import OLLAMA_EMBED_URL, EMBED_MODEL, EMBED_DIM

    # Fetch available models from Ollama embed server
    print()
    print("  Fetching available models from Ollama embed server...")
    try:
        r = _req.get(f"{OLLAMA_EMBED_URL}/api/tags", timeout=5)
        all_models = r.json().get("models", [])
        # Filter to embedding-capable models (exclude LLMs)
        EMBED_KEYWORDS = ["embed", "bge", "e5", "gte", "nomic", "minilm", "reranker"]
        models = [m for m in all_models
                  if any(k in m["name"].lower() for k in EMBED_KEYWORDS)]
        if not models:
            models = all_models  # show all if filter returns nothing
    except Exception as e:
        print(f"  [X] Cannot reach {OLLAMA_EMBED_URL}: {e}")
        return

    # Known dimensions for common models
    DIM_MAP = {
        "bge-m3":                   1024,
        "qwen3-embedding:0.6b":     1024,
        "qwen3-embedding:4b":       2560,
        "qwen3-embedding:8b":       4096,
        "qwen3-embedding:8b-q8_0":  4096,
        "nomic-embed-text":         768,
        "mxbai-embed-large":        1024,
        "all-minilm":               384,
    }

    def _get_dim(name: str) -> str:
        for k, v in DIM_MAP.items():
            if k in name:
                return str(v)
        return "?"

    print()
    print("  +==========================================================+")
    print("  |  EMBEDDING MODEL                                         |")
    print("  +==========================================================+")
    print(f"  |  Current: {EMBED_MODEL[:30]:<30} dim={EMBED_DIM:<6}|")
    print("  +==========================================================+")

    for i, m in enumerate(models, 1):
        name    = m["name"]
        size    = m.get("size", 0)
        size_gb = f"{size/1e9:.1f}GB" if size else "?"
        dim     = _get_dim(name)
        marker  = " <-" if name == EMBED_MODEL else ""
        print(f"  |  [{i}] {name[:30]:<30} {size_gb:<7} dim={dim:<6}{marker}|")

    print("  |  [Enter]  Back                                           |")
    print("  +==========================================================+")
    print()
    print("  ⚠  Changing the embedding model requires wiping all Qdrant")
    print("     collections. Vectors from different models are incompatible.")
    print()

    raw = input("  Choice: ").strip()
    if not raw or not raw.isdigit():
        return
    idx = int(raw) - 1
    if not (0 <= idx < len(models)):
        print("  Invalid selection.")
        return

    selected = models[idx]["name"]
    if selected == EMBED_MODEL:
        print(f"  Already using {selected}.")
        return

    new_dim = int(_get_dim(selected)) if _get_dim(selected) != "?" else EMBED_DIM

    print(f"\n  Selected: {selected} (dim={new_dim})")
    print(f"  Current:  {EMBED_MODEL} (dim={EMBED_DIM})")
    print()
    print("  ⚠  This will:")
    print("     1. Update EMBED_MODEL and EMBED_DIM in config.py")
    print("     2. Wipe all Qdrant research/knowledge collections")
    print("        (persona_* collections will be preserved)")
    print()
    confirm = input("  Proceed? [y/N]: ").strip().lower()
    if confirm not in ("y", "yes"):
        print("  Cancelled.")
        return

    # Update config.py
    import re as _re
    config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.py")
    try:
        cfg = open(config_path, encoding="utf-8").read()
        cfg = _re.sub(r'EMBED_MODEL\s*=\s*["\'].*?["\']', f'EMBED_MODEL = "{selected}"', cfg)
        cfg = _re.sub(r'EMBED_DIM\s*=\s*\d+', f'EMBED_DIM = {new_dim}', cfg)
        open(config_path, "w", encoding="utf-8").write(cfg)
        print(f"  [OK] config.py updated: EMBED_MODEL={selected}, EMBED_DIM={new_dim}")
    except Exception as e:
        print(f"  [X] Could not update config.py: {e}")
        return

    # Wipe research + knowledge collections (keep persona_*)
    try:
        from qdrant_client import QdrantClient
        from config import QDRANT_URL
        qc   = QdrantClient(url=QDRANT_URL)
        cols = [c.name for c in qc.get_collections().collections
                if not c.name.startswith("persona_")]
        wiped = 0
        for col in cols:
            try:
                qc.delete_collection(col)
                print(f"  [OK] Wiped {col}")
                wiped += 1
            except Exception as e:
                print(f"  [X] {col}: {e}")
        print(f"\n  [OK] Wiped {wiped} collections. Restart agent to apply changes.")
    except Exception as e:
        print(f"  [X] Qdrant error: {e}")

    input("\n  [Enter] to continue: ")
    """
    Select API provider for generate phase.
    Returns provider key ("claude", "openai", etc.) or "" for local Ollama.
    Modifies global _SESSION_API_PROVIDER.
    """
    global _SESSION_API_PROVIDER
    from config import API_PROVIDERS

    while True:
        print()
        print("  +==========================================+")
        print("  |  API SETTINGS                            |")
        print("  +==========================================+")

        current = _SESSION_API_PROVIDER
        current_label = (API_PROVIDERS[current]["label"]
                         if current in API_PROVIDERS else "Local Ollama")
        print(f"  |  Current: {current_label[:32]:<32}|")
        print("  +==========================================+")

        options = [("", "Local Ollama (no cost)")] + [
            (k, v["label"]) for k, v in API_PROVIDERS.items()
        ]

        for i, (key, label) in enumerate(options, 1):
            marker = " <-" if key == current else ""
            print(f"  |  [{i}] {label[:38]:<38}{marker}|")

        print("  |  [Enter]  Back                           |")
        print("  +==========================================+")
        print()

        # Show API key status
        print("  API key status:")
        for k, v in API_PROVIDERS.items():
            key_val = os.environ.get(v["env_key"], "")
            status  = f"[OK] set ({v['env_key']})" if key_val else f"[X] missing ({v['env_key']})"
            print(f"    {k:<12} {status}")
        print()
        print(f"  Set keys: setx ANTHROPIC_API_KEY \"sk-ant-...\"  (Windows, then restart terminal)")
        print()

        raw = input("  Choice: ").strip()

        if raw in ("", "q", "back"):
            break

        if raw.isdigit() and 1 <= int(raw) <= len(options):
            selected_key, selected_label = options[int(raw) - 1]

            # Check API key if not Ollama
            if selected_key:
                prov    = API_PROVIDERS[selected_key]
                api_key = os.environ.get(prov["env_key"], "")
                if not api_key:
                    print(f"\n  ⚠  No API key for {selected_label}")
                    print(f"  Set: setx {prov['env_key']} \"your-key\"")
                    confirm = input("  Set anyway (will fail at generate)? [y/N]: ").strip().lower()
                    if confirm not in ("y", "yes"):
                        continue

            _SESSION_API_PROVIDER = selected_key
            label = selected_label if selected_key else "Local Ollama"
            print(f"  [OK] Provider set: {label}")

        else:
            print("  Invalid choice.")

    return _SESSION_API_PROVIDER


# -- Queue management -------------------------------------------------------