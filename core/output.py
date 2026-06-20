"""
core/output.py
--------------
Finds the most recently created article folder and ensures metadata.json exists.
article_agent.py creates the folder directly -- this just verifies and supplements.
"""

import json
import os
import re
import time
from datetime import datetime
from pathlib import Path

OUTPUT_DIR = "./output"


def save_article(topic: str, category: str, section: str,
                 style_label: str, notes: str = "") -> str:
    """
    Find the most recently created article subfolder in OUTPUT_DIR.
    Adds/updates metadata.json if missing.
    Returns: path to article folder.
    """
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    time.sleep(0.5)

    # Find most recently modified subfolder (article_agent creates it)
    subdirs = sorted(
        [p for p in Path(OUTPUT_DIR).iterdir() if p.is_dir()],
        key=lambda p: p.stat().st_mtime,
        reverse=True
    )

    if subdirs:
        art_dir = subdirs[0]
        meta_path = art_dir / "metadata.json"

        # Add metadata if missing
        if not meta_path.exists():
            meta = {
                "topic":        topic,
                "category":     category,
                "section":      section,
                "style":        style_label,
                "notes":        notes,
                "generated_at": datetime.now().isoformat(),
                "payloadcms": {
                    "status": "draft",
                    "hint":   "Paste article.txt into PayloadCMS rich text editor"
                }
            }
            with open(meta_path, "w", encoding="utf-8") as f:
                json.dump(meta, f, indent=2, ensure_ascii=False)

        return str(art_dir)

    # Fallback -- create folder manually if agent didn't
    slug    = re.sub(r"[^\w\s-]", "", topic.lower())
    slug    = re.sub(r"[\s_-]+", "-", slug).strip("-")[:50]
    ts      = datetime.now().strftime("%Y%m%d_%H%M%S")
    art_dir = Path(OUTPUT_DIR) / f"{ts}_{slug}"
    art_dir.mkdir(parents=True, exist_ok=True)

    # Move any loose .txt files
    txt_files = sorted(
        [p for p in Path(OUTPUT_DIR).glob("*.txt")],
        key=lambda p: p.stat().st_mtime, reverse=True
    )
    if txt_files:
        txt_files[0].rename(art_dir / "article.txt")

    meta = {
        "topic": topic, "category": category, "section": section,
        "style": style_label, "notes": notes,
        "generated_at": datetime.now().isoformat(),
        "payloadcms": {"status": "draft",
                       "hint": "Paste article.txt into PayloadCMS rich text editor"}
    }
    with open(art_dir / "metadata.json", "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2, ensure_ascii=False)

    return str(art_dir)
