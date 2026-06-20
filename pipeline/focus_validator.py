# pipeline/focus_validator.py -- Article focus quality validator
# Called from run_generate() after focus is set, before _build_prompt().
# Night run: LLM check skipped, BM25 check only.
# Does NOT block pipeline -- flags only.

import re
import requests


def run_focus_validator(topic: str, article_focus: str, context_research: str,
                        model: str, ollama_url: str,
                        night_run: bool = False) -> dict:
    """Validate article focus quality.

    Checks:
    1. BM25: do top research chunks support the focus? (local, always runs)
    2. LLM: is the focus falsifiable and specific? (skipped in night_run)

    Returns:
        {
            "valid":   bool,
            "score":   int (1-5),
            "issues":  list[str],
            "skipped": bool,
        }
    """
    result = {"valid": True, "score": 5, "issues": [], "skipped": False}

    if not article_focus or not article_focus.strip():
        return {"valid": False, "score": 0, "issues": ["No focus set"], "skipped": False}

    focus = article_focus.strip()

    # -- BM25 check: does research support this focus? ---------------------
    bm25_score = _bm25_support(focus, context_research)
    result["bm25_score"] = round(bm25_score, 3)

    bm25_issues = []
    if bm25_score < 0.5:
        bm25_issues.append(f"Research weakly supports focus (BM25={bm25_score:.2f}) -- focus may be unsupported by available chunks")
    elif bm25_score < 1.5:
        bm25_issues.append(f"Research moderately supports focus (BM25={bm25_score:.2f}) -- consider narrowing")

    result["issues"].extend(bm25_issues)

    # -- LLM check: is focus falsifiable and specific? --------------------
    if night_run:
        result["skipped"] = True
        # Derive score from BM25 only
        if bm25_score >= 2.0:
            result["score"] = 4
        elif bm25_score >= 1.0:
            result["score"] = 3
        elif bm25_score >= 0.5:
            result["score"] = 2
        else:
            result["score"] = 1
        result["valid"] = result["score"] >= 2
        return result

    llm_result = _llm_check(topic, focus, context_research[:4000], model, ollama_url)
    result["issues"].extend(llm_result.get("issues", []))
    llm_score = llm_result.get("score", 3)

    # Combined score: average BM25-derived + LLM score
    bm25_derived = min(5, max(1, int(bm25_score * 1.5 + 1)))
    result["score"] = round((bm25_derived + llm_score) / 2)
    result["valid"] = result["score"] >= 3 and not llm_result.get("hard_fail", False)

    return result


def _bm25_support(focus: str, context_research: str) -> float:
    """BM25 score of focus query against research chunks (split by double newline)."""
    if not context_research:
        return 0.0

    chunks = [c.strip() for c in context_research.split("\n\n") if len(c.strip()) > 50]
    if not chunks:
        return 0.0

    focus_toks = re.findall(r"[a-z0-9]+", focus.lower())
    focus_tf = {}
    for t in focus_toks:
        if len(t) > 2:
            focus_tf[t] = focus_tf.get(t, 0) + 1
    focus_total = max(len(focus_toks), 1)
    query = {t: c / focus_total for t, c in focus_tf.items()}

    if not query:
        return 0.0

    k1, b, avdl = 1.5, 0.75, 300
    scores = []
    for chunk in chunks:
        toks = re.findall(r"[a-z0-9]+", chunk.lower())
        tf_map = {}
        for t in toks:
            if len(t) > 2:
                tf_map[t] = tf_map.get(t, 0) + 1
        dlen = max(len(toks), 1)
        sc = 0.0
        for tok, qw in query.items():
            if tok in tf_map:
                tf = tf_map[tok]
                sc += qw * (tf * (k1 + 1)) / (tf + k1 * (1 - b + b * dlen / avdl))
        scores.append(sc)

    scores.sort(reverse=True)
    # Average of top-5 chunks
    top = scores[:5]
    return sum(top) / len(top) if top else 0.0


def _llm_check(topic: str, focus: str, research_preview: str,
               model: str, ollama_url: str) -> dict:
    """LLM check: is focus falsifiable, specific, supported by research?"""

    prompt = (
        "You are an editorial strategist. Evaluate this article focus statement.\n\n"
        "Topic: " + topic + "\n"
        "Focus: " + focus + "\n\n"
        "Research excerpt:\n" + research_preview + "\n\n"
        "Check these criteria:\n"
        "F1: Is the focus a declarative statement (not a question, not a topic description)?\n"
        "F2: Is the focus falsifiable -- could someone argue the opposite?\n"
        "F3: Does the focus contain a concrete claim (names, numbers, comparisons, or causal argument)?\n"
        "F4: Does the research above contain facts that directly support this focus?\n"
        "F5: Is the focus narrow enough to be argued in 1000-1500 words?\n\n"
        "Output EXACTLY this block, nothing else:\n"
        "F1: <yes|no>\n"
        "F2: <yes|no>\n"
        "F3: <yes|no>\n"
        "F4: <yes|no>\n"
        "F5: <yes|no>\n"
        "SCORE: <1-5 integer, 5=excellent>\n"
        "ISSUES: <specific problems semicolon-separated, or none>"
    )

    try:
        payload = {
            "model":    model,
            "stream":   False,
            "messages": [{"role": "user", "content": prompt}],
            "options":  {"num_predict": 300, "temperature": 0.1},
            "think":    False,
        }
        r = requests.post(f"{ollama_url}/api/chat", json=payload, timeout=60)
        r.raise_for_status()
        raw = r.json().get("message", {}).get("content", "").strip()
        if "</think>" in raw:
            raw = raw.split("</think>")[-1].strip()
    except Exception as e:
        print(f"  [focus-validator] LLM error: {e} -- skipping LLM check")
        return {"score": 3, "issues": [], "hard_fail": False}

    return _parse_llm_response(raw)


def _parse_llm_response(text: str) -> dict:
    result = {"score": 3, "issues": [], "hard_fail": False}
    flags = {}

    for line in text.strip().splitlines():
        lower = line.strip().lower()
        for fx in ("f1", "f2", "f3", "f4", "f5"):
            if lower.startswith(fx + ":"):
                val = line.split(":", 1)[1].strip().lower()
                flags[fx] = "yes" in val
        if lower.startswith("score:"):
            try:
                result["score"] = int(re.search(r"\d", line.split(":", 1)[1]).group())
            except Exception:
                pass
        if lower.startswith("issues:"):
            raw = line.split(":", 1)[1].strip()
            if raw.lower() != "none":
                result["issues"] = [i.strip() for i in raw.split(";") if i.strip()]

    # Hard fail: focus is a question or not falsifiable
    if flags.get("f1") is False:
        result["issues"].insert(0, "Focus is not a declarative statement -- rewrite as a thesis")
        result["hard_fail"] = True
    if flags.get("f2") is False:
        result["issues"].append("Focus is not falsifiable -- too vague to argue")

    return result


def print_validation(result: dict) -> None:
    """Print focus validation result to console."""
    score = result.get("score", 0)
    valid = result.get("valid", False)
    skipped = result.get("skipped", False)
    bm25 = result.get("bm25_score", 0.0)

    status = "OK" if valid else "WEAK"
    skip_note = " [night-run: LLM skipped]" if skipped else ""
    print(f"  [focus-validator] {status}  score={score}/5  BM25={bm25:.2f}{skip_note}")
    for issue in result.get("issues", []):
        print(f"    ! {issue}")
