# fix_unicode_to_ascii.py
# Replace unicode box-drawing, arrows, bullets with plain ASCII equivalents
# Run after fix_mojibake.py on any file or directory
# Usage: python fix_unicode_to_ascii.py <file_or_dir>

import sys
import os

REPLACEMENTS = {
    # Box drawing
    '\u2500': '-', '\u2501': '-', '\u2502': '|', '\u2503': '|',
    '\u250c': '+', '\u250d': '+', '\u250e': '+', '\u250f': '+',
    '\u2510': '+', '\u2511': '+', '\u2512': '+', '\u2513': '+',
    '\u2514': '+', '\u2515': '+', '\u2516': '+', '\u2517': '+',
    '\u2518': '+', '\u2519': '+', '\u251a': '+', '\u251b': '+',
    '\u251c': '+', '\u251d': '+', '\u251e': '+', '\u251f': '+',
    '\u2520': '+', '\u2521': '+', '\u2522': '+', '\u2523': '+',
    '\u2524': '+', '\u2525': '+', '\u2526': '+', '\u2527': '+',
    '\u2528': '+', '\u2529': '+', '\u252a': '+', '\u252b': '+',
    '\u252c': '+', '\u252d': '+', '\u252e': '+', '\u252f': '+',
    '\u2530': '+', '\u2531': '+', '\u2532': '+', '\u2533': '+',
    '\u2534': '+', '\u2535': '+', '\u2536': '+', '\u2537': '+',
    '\u2538': '+', '\u2539': '+', '\u253a': '+', '\u253b': '+',
    '\u253c': '+', '\u2550': '=', '\u2551': '|',
    '\u2552': '+', '\u2553': '+', '\u2554': '+', '\u2555': '+',
    '\u2556': '+', '\u2557': '+', '\u2558': '+', '\u2559': '+',
    '\u255a': '+', '\u255b': '+', '\u255c': '+', '\u255d': '+',
    '\u255e': '+', '\u255f': '+', '\u2560': '+', '\u2561': '+',
    '\u2562': '+', '\u2563': '+', '\u2564': '+', '\u2565': '+',
    '\u2566': '+', '\u2567': '+', '\u2568': '+', '\u2569': '+',
    '\u256a': '+', '\u256b': '+', '\u256c': '+',
    # Arrows
    '\u2190': '<-', '\u2192': '->', '\u2191': '^', '\u2193': 'v',
    '\u21d2': '=>',
    # Bullets and misc
    '\u2022': '*', '\u2023': '>',
    '\u2013': '-', '\u2014': '--',
    '\u2018': "'", '\u2019': "'",
    '\u201c': '"', '\u201d': '"',
    '\u2026': '...',
    '\u2713': '[OK]', '\u2717': '[X]',
    '\u2714': '[OK]', '\u2718': '[X]',
    '\u2605': '*', '\u2606': '*',
    '\u25b6': '>', '\u25cf': '*', '\u25cb': 'o',
    '\u2588': '#', '\u2592': '#',
    '\u00ab': '<<', '\u00bb': '>>',
    '\u00b7': '.',
}

# Files to skip entirely
SKIP_FILES = {
    'queue.json',
    'queue.json.bak',
}

def fix_file(path):
    fname = os.path.basename(path)
    if fname in SKIP_FILES:
        print("  SKIP (protected): " + path)
        return 0

    try:
        with open(path, 'r', encoding='utf-8') as f:
            content = f.read()
    except UnicodeDecodeError:
        try:
            with open(path, 'r', encoding='latin-1') as f:
                content = f.read()
        except Exception as e:
            print("  SKIP (read error): " + path + " - " + str(e))
            return 0

    original = content
    for char, replacement in REPLACEMENTS.items():
        content = content.replace(char, replacement)

    if content == original:
        return 0

    changed = sum(1 for a, b in zip(original, content) if a != b)
    with open(path, 'w', encoding='utf-8') as f:
        f.write(content)
    print("  Fixed " + str(changed) + " chars: " + path)
    return changed

def fix_path(path):
    if os.path.isfile(path):
        fix_file(path)
    elif os.path.isdir(path):
        total = 0
        for root, dirs, files in os.walk(path):
            dirs[:] = [d for d in dirs if d not in ('venv', '__pycache__', '.git', 'node_modules')]
            for fname in files:
                if fname.endswith(('.py', '.md', '.txt', '.json', '.yml', '.yaml', '.cfg', '.ini')):
                    total += fix_file(os.path.join(root, fname))
        print("  Total chars fixed: " + str(total))
    else:
        print("  ERROR: path not found: " + path)

if __name__ == '__main__':
    if len(sys.argv) < 2:
        print("Usage: python fix_unicode_to_ascii.py <file_or_directory>")
        sys.exit(1)
    fix_path(sys.argv[1])
