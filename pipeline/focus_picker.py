# pipeline/focus_picker.py -- Interactive article focus selector
# Called from run_generate() when item["article_focus"] is empty.
# Uses already-loaded Ollama model -- no extra load cost.
# Night run: not called (caller checks item.get("night_run", False))

import re
import requests


def run_focus_picker(topic: str, context_research: str, model: str,
                     ollama_url: str) -> str:
    """Generate 20 article angles and let user pick one interactively.

    Returns:
        Selected/edited focus string, or "" if skipped.
    """
    research_preview = context_research[:12000]

    prompt = f"""You are an editorial strategist for a tech and gaming blog.
Based on the research below, propose exactly 20 unique article angles for this topic: "{topic}"

Rules:
- Each angle = one specific thesis: "X proves Y because Z" or "X is happening because Y"
- Each angle must be supported by facts present in the research below
- Angles must be DIFFERENT from each other -- no overlap in argument
- Prefer niche, non-obvious angles over mainstream takes
- No PR spin, no corporate framing
- No questions -- each angle is a declarative statement

Research:
{research_preview}

Output EXACTLY 20 numbered angles, one per line, nothing else.
Format: N. [angle text]
"""

    print("\n  -- Focus Picker --------------------------------------------------")
    print(f"  Topic: {topic[:70]}")
    print("  Generating 20 angles... (~15-30s)")

    try:
        payload = {
            "model":    model,
            "stream":   False,
            "messages": [{"role": "user", "content": prompt}],
            "options":  {"num_predict": 1800, "temperature": 0.85},
            "think":    False,
        }
        r = requests.post(f"{ollama_url}/api/chat", json=payload, timeout=120)
        r.raise_for_status()
        raw = r.json().get("message", {}).get("content", "").strip()
    except Exception as e:
        print(f"  [picker] LLM error: {e}")
        return ""

    if not raw:
        print("  [picker] Empty response -- skipping.")
        return ""

    # Parse numbered lines
    angles = []
    for line in raw.splitlines():
        m = re.match(r"^\s*(\d+)[.)]\s+(.+)", line)
        if m:
            angles.append(m.group(2).strip())

    if not angles:
        print("  [picker] Could not parse angles -- skipping.")
        return ""

    angles = angles[:20]
    _display(topic, angles)

    return _prompt_loop(angles)



def _validate_angle(text: str) -> bool:
    """Return True if angle looks complete -- not truncated mid-sentence."""
    if not text or len(text.strip()) < 20:
        return False
    t = text.strip()
    # Truncated if ends mid-word (no punctuation or sentence-ending char)
    last = t[-1]
    if last.isalpha() or last in (',', ';', '(', '[', ':'):
        return False
    return True

def _display(topic: str, angles: list) -> None:
    print()
    print(f"  -- Focus Picker {'-' * 50}")
    print(f"  Topic: {topic[:80]}")
    print(f"  {'-' * 66}")
    for i, angle in enumerate(angles, 1):
        # Wrap long angles at 70 chars
        prefix = f"  [{i:2}]  "
        wrap_w = 70
        if len(angle) <= wrap_w:
            print(f"{prefix}{angle}")
        else:
            # First line
            print(f"{prefix}{angle[:wrap_w]}")
            # Continuation lines indented to match
            cont = " " * len(prefix)
            rest = angle[wrap_w:]
            while rest:
                print(f"{cont}{rest[:wrap_w]}")
                rest = rest[wrap_w:]
    print(f"  {'-' * 66}")
    print(f"  [1-{len(angles)}] select  [e N] edit  [0] custom  [s] skip")
    print()


def _prompt_loop(angles: list) -> str:
    while True:
        raw = input("  > ").strip()

        # Skip
        if raw.lower() == "s":
            print("  [picker] Skipped -- generating without focus.")
            return ""

        # Custom
        if raw == "0":
            custom = input("  Custom focus: ").strip()
            if custom:
                print(f"  [picker] [OK] Custom: {custom[:80]}")
                if not _validate_angle(custom):
                    print("  [picker] Focus appears incomplete -- please retype.")
                    continue
                return custom
            print("  (empty -- try again)")
            continue

        # Edit: e N
        if raw.lower().startswith("e "):
            parts = raw.split(None, 1)
            if len(parts) == 2 and parts[1].isdigit():
                n = int(parts[1])
                if 1 <= n <= len(angles):
                    prefilled = angles[n - 1]
                    print(f"  Editing [{n}]: {prefilled}")
                    edited = input("  > ").strip()
                    if edited:
                        print(f"  [picker] [OK] Edited: {edited[:80]}")
                        return edited
                    print("  (empty -- keeping original)")
                    continue
            print("  Usage: e N  (e.g. 'e 3')")
            continue

        # Select by number
        if raw.isdigit():
            n = int(raw)
            if 1 <= n <= len(angles):
                selected = angles[n - 1]
                print(f"  [picker] [OK] [{n}]: {selected[:80]}")
                if not _validate_angle(selected):
                    print("  [picker] Angle appears truncated -- try editing with [e N] or enter custom.")
                    continue
                return selected
            print(f"  Enter 1-{len(angles)}, [e N] to edit, [0] custom, [s] skip.")
            continue

        print("  Unknown. Enter number, [e N], [0], or [s].")

