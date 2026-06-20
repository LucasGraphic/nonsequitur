# menus/knowledge/domains.py -- domains_trusted.json editor v2

import json
import os
try:
    import readline
except ImportError:
    readline = None

DOMAINS_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "data", "domains_trusted.json"
)

BLOCKED_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "data", "domains_blocked.json"
)

TIERS     = ["press", "trusted", "community", "unknown"]
TIER_ICON = {"press": "\u2605 press", "trusted": "\u2713 trusted",
             "community": "~ community", "unknown": "? unknown"}
PAGE_SIZE = 20


def _load() -> dict:
    with open(DOMAINS_PATH, encoding="utf-8") as f:
        return json.load(f)


def _save(data: dict) -> None:
    with open(DOMAINS_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    try:
        from domain_config import reload_all
        reload_all()
    except Exception:
        pass
    print("  \u2713 Saved.")


def _categories(data: dict) -> list:
    return [k for k in data.keys() if not k.startswith("_")]


def _input_with_complete(prompt: str, candidates: list) -> str:
    """Input with tab-completion from candidates list."""
    def completer(text, state):
        matches = [c for c in candidates if c.lower().startswith(text.lower())]
        return matches[state] if state < len(matches) else None
    try:
        if readline:
            readline.set_completer(completer)
            readline.parse_and_bind("tab: complete")
        result = input(prompt).strip()
        if readline:
            readline.set_completer(None)
        return result
    except Exception:
        return input(prompt).strip()


# -- Category browser ----------------------------------------------------------

def _pick_category(data: dict) -> str | None:
    cats = _categories(data)
    print()
    print(f"  {'#':<4} {'CATEGORY':<22} {'DOMAINS'}")
    print(f"  {'-'*35}")
    for i, c in enumerate(cats, 1):
        n = len(data.get(c, {}))
        print(f"  [{i:<2}] {c:<22} {n}")
    print()
    raw = input("  Category [1-9] or Enter=back: ").strip()
    if not raw or not raw.isdigit():
        return None
    idx = int(raw) - 1
    if 0 <= idx < len(cats):
        return cats[idx]
    print("  Invalid.")
    return None


# -- Domain browser with pagination -------------------------------------------

def _browse_category(data: dict, category: str) -> None:
    while True:
        domains = data.get(category, {})
        items   = sorted(domains.items())  # alphabetical
        total   = len(items)
        pages   = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
        page    = 0

        while True:
            start = page * PAGE_SIZE
            end   = min(start + PAGE_SIZE, total)
            chunk = items[start:end]

            print()
            print(f"  -- [{category}] -- {total} domains -- page {page+1}/{pages} ------------------")
            print(f"  {'#':<5} {'DOMAIN':<40} {'TIER':<14} {'BOOST'}")
            print(f"  {'-'*70}")
            for i, (domain, info) in enumerate(chunk, start + 1):
                tier  = info.get("tier", "unknown")
                boost = info.get("boost", 0.0)
                icon  = TIER_ICON.get(tier, tier)
                print(f"  [{i:<3}] {domain:<40} {icon:<14} {boost:.2f}")
            print()
            if pages > 1:
                nav = "  [n] next  [p] prev  " if pages > 1 else "  "
            else:
                nav = "  "
            print(f"{nav}[a] add  [r N] remove  [s] search  [Enter] back")
            print()
            raw = input("  > ").strip().lower()

            if raw in ("", "q", "back"):
                return

            # Pagination
            if raw == "n" and page < pages - 1:
                page += 1
                continue
            if raw == "p" and page > 0:
                page -= 1
                continue

            # Add
            if raw == "a":
                _add_domain(data, category)
                data = _load()  # reload
                domains = data.get(category, {})
                items   = sorted(domains.items())
                total   = len(items)
                pages   = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
                continue

            # Search: `s` alone -> prompt, `s <query>` -> inline
            if raw == "s" or raw.startswith("s "):
                if raw == "s":
                    query = input("  Search: ").strip().lower()
                else:
                    query = raw[2:].strip().lower()
                if query:
                    matched = [(i+1, d, info) for i, (d, info) in enumerate(items) if query in d.lower()]
                    if not matched:
                        print(f"  Not found: '{query}'")
                    else:
                        print()
                        for num, domain, info in matched:
                            tier  = info.get("tier", "unknown")
                            boost = info.get("boost", 0.0)
                            print(f"  [{num}] {domain:<40} {TIER_ICON.get(tier, tier):<14} {boost:.2f}")
                        print()
                continue

            # Remove: r N
            parts = raw.split()
            if len(parts) == 2 and parts[0] == "r" and parts[1].isdigit():
                n = int(parts[1]) - 1
                if 0 <= n < total:
                    domain = items[n][0]
                    confirm = input(f"  Remove '{domain}'? [y/N]: ").strip().lower()
                    if confirm in ("y", "yes"):
                        del data[category][domain]
                        _save(data)
                        data  = _load()
                        items = sorted(data.get(category, {}).items())
                        total = len(items)
                        pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
                        page  = min(page, pages - 1)
                else:
                    print("  Out of range.")
                continue

            # Select by number -> edit
            if raw.isdigit():
                n = int(raw) - 1
                if 0 <= n < total:
                    domain = items[n][0]
                    _edit_domain_entry(data, category, domain)
                    data  = _load()
                    items = sorted(data.get(category, {}).items())
                    total = len(items)
                    pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
                else:
                    print("  Out of range.")
                continue

            # Implicit search -- anything that's not a command
            matched = [(i+1, d, info) for i, (d, info) in enumerate(items) if raw in d.lower()]
            if not matched:
                print(f"  Not found: '{raw}'")
            else:
                print()
                for num, domain, info in matched:
                    tier  = info.get("tier", "unknown")
                    boost = info.get("boost", 0.0)
                    print(f"  [{num}] {domain:<40} {TIER_ICON.get(tier, tier):<14} {boost:.2f}")
                print()


# -- Edit single domain entry --------------------------------------------------

def _edit_domain_entry(data: dict, category: str, domain: str) -> None:
    info = data[category][domain]
    print()
    print(f"  Editing: {domain}  [{category}]")
    print(f"  tier={info['tier']}  boost={info['boost']}")
    print()
    print("  Tier:")
    for i, t in enumerate(TIERS, 1):
        marker = " \u2190" if t == info["tier"] else ""
        print(f"  [{i}] {t}{marker}")
    raw_t = input(f"  Tier [1-4] Enter=keep: ").strip()
    if raw_t.isdigit() and 1 <= int(raw_t) <= len(TIERS):
        data[category][domain]["tier"] = TIERS[int(raw_t) - 1]

    raw_b = input(f"  Boost (0.0-1.0) Enter=keep [{info['boost']}]: ").strip()
    if raw_b:
        try:
            boost = round(max(0.0, min(1.0, float(raw_b))), 2)
            data[category][domain]["boost"] = boost
        except ValueError:
            print("  Invalid -- keeping current.")

    _save(data)


# -- Add domain ----------------------------------------------------------------

def _add_domain(data: dict, category: str = "") -> None:
    if not category:
        cats = _categories(data)
        print()
        for i, c in enumerate(cats, 1):
            print(f"  [{i}] {c}")
        raw = input("  Category: ").strip()
        if not raw.isdigit() or not (1 <= int(raw) <= len(cats)):
            print("  Cancelled.")
            return
        category = cats[int(raw) - 1]

    # Autocomplete from all known domains
    all_domains = []
    for cat_domains in data.values():
        if isinstance(cat_domains, dict):
            all_domains.extend(cat_domains.keys())

    domain = _input_with_complete("  Domain (Tab=autocomplete): ", all_domains).lower()
    if not domain or "." not in domain:
        print("  Invalid domain.")
        return
    if domain in data.get(category, {}):
        print(f"  Already exists in [{category}].")
        return

    print("  Tier:")
    for i, t in enumerate(TIERS, 1):
        print(f"  [{i}] {t}")
    raw_t = input("  > ").strip()
    if not raw_t.isdigit() or not (1 <= int(raw_t) <= len(TIERS)):
        print("  Cancelled.")
        return
    tier = TIERS[int(raw_t) - 1]

    raw_b = input("  Boost (0.0-1.0, Enter=0.75): ").strip()
    try:
        boost = round(max(0.0, min(1.0, float(raw_b))), 2) if raw_b else 0.75
    except ValueError:
        boost = 0.75

    if category not in data:
        data[category] = {}
    data[category][domain] = {"tier": tier, "boost": boost}
    _save(data)
    print(f"  \u2713 Added: {domain} \u2192 [{category}] tier={tier} boost={boost}")


# -- Global search -------------------------------------------------------------

def _search_all(data: dict) -> None:
    query = input("  Search all domains: ").strip().lower()
    if not query:
        return
    found = []
    for cat, domains in data.items():
        if cat.startswith("_") or not isinstance(domains, dict):
            continue
        for domain, info in sorted(domains.items()):
            if query in domain.lower():
                found.append((cat, domain, info))
    if not found:
        print(f"  Not found: '{query}'")
        return
    print()
    print(f"  {'CATEGORY':<14} {'DOMAIN':<40} {'TIER':<14} {'BOOST'}")
    print(f"  {'-'*74}")
    for cat, domain, info in found:
        tier  = info.get("tier", "unknown")
        boost = info.get("boost", 0.0)
        print(f"  {cat:<14} {domain:<40} {TIER_ICON.get(tier, tier):<14} {boost:.2f}")
    print(f"\n  {len(found)} result(s)")


# -- Blocked domains browser ---------------------------------------------------

def _load_blocked() -> dict:
    try:
        with open(BLOCKED_PATH, encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            # Legacy flat list -- migrate to dict
            data = {"_comment": "Blocked domains", "domains": sorted(data), "url_patterns": [], "title_patterns": []}
            _save_blocked(data)
        return data
    except FileNotFoundError:
        return {"_comment": "Blocked domains", "domains": [], "url_patterns": [], "title_patterns": []}


def _save_blocked(data: dict) -> None:
    with open(BLOCKED_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    try:
        from domain_config import reload_all
        reload_all()
    except Exception:
        pass
    print("  \u2713 Saved.")


def _browse_blocked() -> None:
    while True:
        data    = _load_blocked()
        domains = sorted(data.get("domains", []))
        total   = len(domains)
        pages   = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
        page    = 0

        while True:
            start = page * PAGE_SIZE
            end   = min(start + PAGE_SIZE, total)
            chunk = domains[start:end]

            print()
            print(f"  -- BLOCKED DOMAINS -- {total} total -- page {page+1}/{pages} --------------")
            print()
            for i, domain in enumerate(chunk, start + 1):
                print(f"  [{i:<3}] {domain}")
            print()
            print(f"  [a] add  [r N] remove  [n] next  [p] prev  [Enter] back")
            print()
            raw = input("  > ").strip().lower()

            if raw in ("", "q", "back"):
                return

            if raw == "n" and page < pages - 1:
                page += 1
                continue
            if raw == "p" and page > 0:
                page -= 1
                continue

            if raw == "a":
                domain = input("  Domain to block: ").strip().lower()
                if not domain or "." not in domain:
                    print("  Invalid domain.")
                    continue
                domain = domain.removeprefix("www.")
                if domain in data["domains"]:
                    print(f"  Already blocked: {domain}")
                    continue
                data["domains"] = sorted(set(data["domains"]) | {domain})
                _save_blocked(data)
                domains = sorted(data["domains"])
                total   = len(domains)
                pages   = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
                continue

            parts = raw.split()
            if len(parts) == 2 and parts[0] == "r" and parts[1].isdigit():
                n = int(parts[1]) - 1
                if 0 <= n < total:
                    domain = domains[n]
                    confirm = input(f"  Remove '{domain}' from blocked? [y/N]: ").strip().lower()
                    if confirm in ("y", "yes"):
                        data["domains"] = [d for d in data["domains"] if d != domain]
                        _save_blocked(data)
                        domains = sorted(data["domains"])
                        total   = len(domains)
                        pages   = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
                        page    = min(page, max(0, pages - 1))
                else:
                    print("  Out of range.")
                continue

            # Implicit search
            matched = [(i+1, d) for i, d in enumerate(domains) if raw in d.lower()]
            if matched:
                print()
                for num, d in matched:
                    print(f"  [{num}] {d}")
                print()
            else:
                print(f"  Not found: '{raw}'")


# -- Main menu -----------------------------------------------------------------

def _domains_menu() -> None:
    while True:
        data    = _load()
        cats    = _categories(data)
        trusted_total = sum(len(data[c]) for c in cats if isinstance(data[c], dict))
        blocked_total = len(_load_blocked().get("domains", []))

        print()
        print(f"  DOMAINS")
        print(f"  {'-'*35}")
        print(f"  [1]  Trusted              {trusted_total:>5} domains")
        print(f"  [2]  Blocked              {blocked_total:>5} domains")
        print()
        print("  [1] trusted  [2] blocked  [Enter] back")
        print()

        raw = input("  > ").strip().lower()

        if raw in ("", "q", "back"):
            return

        if raw == "1":
            _trusted_menu(data, cats)

        elif raw == "2":
            _browse_blocked()

        else:
            print("  1 = trusted | 2 = blocked | Enter = back")


def _trusted_menu(data: dict, cats: list) -> None:
    while True:
        total = sum(len(data[c]) for c in cats if isinstance(data[c], dict))
        print()
        print(f"  TRUSTED DOMAINS -- {total} total")
        print(f"  {'-'*35}")
        for i, c in enumerate(cats, 1):
            n = len(data.get(c, {}))
            print(f"  [{i:<2}] {c:<22} {n} domains")
        print()
        print("  [1-9] browse category  [s] search all  [a] add  [Enter] back")
        print()

        raw = input("  > ").strip().lower()

        if raw in ("", "q", "back"):
            return

        if raw == "s" or raw.startswith("s "):
            data = _load()
            if raw == "s":
                _search_all(data)
            else:
                query = raw[2:].strip().lower()
                if query:
                    found = []
                    for cat, domains in data.items():
                        if cat.startswith("_") or not isinstance(domains, dict):
                            continue
                        for domain, info in sorted(domains.items()):
                            if query in domain.lower():
                                found.append((cat, domain, info))
                    if not found:
                        print(f"  Not found: '{query}'")
                    else:
                        print()
                        print(f"  {'CATEGORY':<14} {'DOMAIN':<40} {'TIER':<14} {'BOOST'}")
                        print(f"  {'-'*74}")
                        for cat, domain, info in found:
                            tier  = info.get("tier", "unknown")
                            boost = info.get("boost", 0.0)
                            print(f"  {cat:<14} {domain:<40} {TIER_ICON.get(tier, tier):<14} {boost:.2f}")
                        print(f"\n  {len(found)} result(s)")
            continue

        if raw == "a":
            data = _load()
            _add_domain(data)
            data = _load()
            cats = _categories(data)
            continue

        if raw.isdigit() and 1 <= int(raw) <= len(cats):
            data = _load()
            _browse_category(data, cats[int(raw) - 1])
            data = _load()
            cats = _categories(data)
            continue

        print("  1-9 = category | s = search | a = add | Enter = back")
