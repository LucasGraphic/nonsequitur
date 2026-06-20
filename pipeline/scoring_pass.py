# pipeline/scoring_pass.py -- article quality scoring
#
# Scores a generated article against editorial criteria.
# Called after _save_article() in generate_run.py.
# Writes "scoring" block to metadata.json.
# Does NOT block pipeline -- flags only.
#
# Usage:
#   from pipeline.scoring_pass import score_article
#   score_article(article_dir, item, model, ollama_url)

import os
import json
import re
import requests
from datetime import datetime

# ---------------------------------------------------------------------------

from config import SCORING_MODEL
SCORE_TIMEOUT   = 600

_SCORING_THINK_DEFAULT       = False
_SCORING_NUM_PREDICT_DEFAULT = 1200


SCORING_PROMPT = """You are an editorial quality assessor. Read the article below and score it.
{schema_context}
======================================================================
PHASE 1 -- ANCHOR SCORE
======================================================================
Before counting any signals, read the article and assign an anchor score
based on your overall impression. This is your gut read as an editor.

Anchors:
  9 = exceptional -- argument is original, voice is distinct, memorable
  8 = strong -- clear argument, good voice, minor weaknesses
  7 = solid -- competent, some personality, argument present but safe
  6 = mediocre -- generic tone, weak argument, forgettable
  5 = poor -- no argument, paraphrasing only, no voice

Write: ANCHOR: <number>
Write: ANCHOR_REASON: one sentence explaining your anchor choice

======================================================================
PHASE 2 -- ARGUMENT STRUCTURE
======================================================================
Evaluate whether the article has a coherent argument arc.

A1. THESIS -- Does the opening contain a clear, arguable claim?
    yes / partial / no
A2. DEVELOPMENT -- Does the argument build across sections or repeat itself?
    builds / repeats / flat
A3. CONSEQUENCE -- Does the closing land on something not already said?
    yes / no
A4. SOURCE_USE -- Does the article synthesize multiple sources or lean on one?
    synthesized / single_source / paraphrase_only

Write each as: A1: yes|partial|no  (reason in brackets)

======================================================================
PHASE 3 -- DISQUALIFIERS
======================================================================
Check each. A single present disqualifier = automatic FAIL.

D1. First sentence describes instead of argues (no thesis in opening)
D2. More than 3 anonymous voices ("critics say", "reviewers note", "some players")
D3. Neutral conclusion -- does not take a position
D4. Over 40% of text paraphrases a single source
D5. Banned phrases present: "it is worth noting", "raises questions", "remains to be seen"
D6. Opens with historical context instead of argument
D7. Every paragraph ends with same conclusion -- no argument progression
D8. Title does not match the article's actual argument
D9. Article is "fair" -- presents both sides without taking position
D10. Passive constructions dominate ("it has been noted" instead of "Blizzard failed")

Write each as: D1: present|absent  (reason if present)

======================================================================
PHASE 4 -- QUALITY SIGNALS
======================================================================
Check each. Each present signal = +0.5 to anchor score.

Q1. First sentence could stand alone as a tweet
Q2. At least one categorical, controversial statement
Q3. Own argument not directly lifted from research sources
Q4. At least one moment of humor or dry observation
Q5. Rhythm -- short sentences (under 10 words) are present
Q6. Article has a "moment" -- one sentence you remember after reading
Q7. Original analogy or metaphor
Q8. Author clearly knows more than sources -- adds interpretation beyond facts

Write each as: Q1: present|absent  (quote or location if present)

======================================================================
PHASE 5 -- GENERICITY SIGNALS
======================================================================
Check each. Each present signal = -0.5 from anchor score.

G1. Superlatives without argument ("revolutionary", "groundbreaking")
G2. Sentences that could be about any game/product/company
G3. "the company" used instead of the company's actual name
G4. Conclusion could serve as intro to a different article

Write each as: G1: present|absent  (location if present)

======================================================================
PHASE 6 -- VOICE CONSISTENCY
======================================================================
V1. Does the author voice stay consistent across all sections?
    consistent / fades / absent
V2. If voice fades -- in which section does it disappear?
    section name or "n/a"

Write as: V1: consistent|fades|absent
         V2: <section name or n/a>

======================================================================
PHASE 7 -- CONFIDENCE
======================================================================
How confident are you in this score?
  high   = article is clearly good or clearly bad
  medium = borderline case, reasonable people could disagree
  low    = unusual article type, hard to apply standard criteria

Write as: CONFIDENCE: high|medium|low
         CONFIDENCE_REASON: one sentence

======================================================================
PHASE 8 -- FINAL CALCULATION
======================================================================
ANCHOR is the CEILING -- the score can only go down from here.
1. Start with ANCHOR score
2. Subtract -0.3 for each Q signal marked ABSENT (missing quality)
3. Subtract -0.5 for each G signal marked PRESENT (genericity penalty)
4. Clamp to 0-10
5. If any D marked present -> verdict = fail (regardless of score)
6. If no D present AND score >= 7.0 -> verdict = pass
7. If no D present AND score < 7.0 -> verdict = weak

Recommendation:
  fail -> regenerate
  weak -> rewrite
  pass -> ready

======================================================================
FINAL OUTPUT
======================================================================
After all phases, output EXACTLY this block (no extra text after it):

ANCHOR: <number>
ANCHOR_REASON: <one sentence>
A1: <yes|partial|no> [<reason>]
A2: <builds|repeats|flat> [<reason>]
A3: <yes|no> [<reason>]
A4: <synthesized|single_source|paraphrase_only> [<reason>]
D1: <present|absent> [<reason if present>]
D2: <present|absent> [<reason if present>]
D3: <present|absent> [<reason if present>]
D4: <present|absent> [<reason if present>]
D5: <present|absent> [<reason if present>]
D6: <present|absent> [<reason if present>]
D7: <present|absent> [<reason if present>]
D8: <present|absent> [<reason if present>]
D9: <present|absent> [<reason if present>]
D10: <present|absent> [<reason if present>]
Q1: <present|absent> [<quote or location>]
Q2: <present|absent> [<quote or location>]
Q3: <present|absent> [<quote or location>]
Q4: <present|absent> [<quote or location>]
Q5: <present|absent> [<quote or location>]
Q6: <present|absent> [<quote or location>]
Q7: <present|absent> [<quote or location>]
Q8: <present|absent> [<quote or location>]
G1: <present|absent> [<location if present>]
G2: <present|absent> [<location if present>]
G3: <present|absent> [<location if present>]
G4: <present|absent> [<location if present>]
V1: <consistent|fades|absent>
V2: <section name or n/a>
CONFIDENCE: <high|medium|low>
CONFIDENCE_REASON: <one sentence>
VERDICT: <pass|fail|weak>
SCORE: <0-10>
ISSUES: <max 3 specific problems semicolon-separated, or "none">
RECOMMENDATION: <ready|rewrite|regenerate>
PRESCRIPTION: <max 3 specific fixes, semicolon-separated. For each absent Q signal or present D: one concrete action sentence referencing actual content in the article. Example: 'Replace closing paragraph with a broader industry implication; Add one punchy sentence under 8 words to open section 2; Remove hedging phrase in paragraph 4'>

ARTICLE TITLE: {title}
CATEGORY: {category}

ARTICLE:
{article}"""

# ---------------------------------------------------------------------------

SCHEMA_OVERRIDES = {
    "games_announcement": {
        "suppress": ["D9"],
        "context": (
            "This article covers a game ANNOUNCEMENT -- not a review or analysis. "
            "It is structurally correct to list unknowns and unanswered questions. "
            "D9 does NOT apply: announcements cannot take a full position on unreleased content. "
            "A section listing confirmed unknowns is editorial discipline, not fence-sitting. "
            "Evaluate whether the author takes a clear stance on the ANNOUNCEMENT ITSELF "
            "(studio credibility, significance of the reveal, what it means for the genre) -- "
            "not on the unreleased game."
        ),
    },
    "games_review": {
        "suppress": [],
        "context": "This is a full game review. All criteria apply at full weight.",
    },
    "games_analysis": {
        "suppress": [],
        "context": "This is an opinion/analysis piece. All criteria apply at full weight.",
    },
    "ai_technical": {
        "suppress": ["D9"],
        "context": (
            "This is a technical AI article. Presenting limitations alongside strengths "
            "is analytical discipline, not fence-sitting. D9 does NOT apply if the author "
            "takes a clear verdict on significance and practical value."
        ),
    },
    "ai_news": {
        "suppress": ["D9"],
        "context": (
            "This is a news article. Listing open questions at the end is standard practice. "
            "D9 does NOT apply if the author clearly states what the news means."
        ),
    },
}


def _call_ollama(prompt: str, ollama_url: str, model: str = "") -> str:
    try:
        from config import SCORING_THINK, SCORING_NUM_PREDICT, model_supports_thinking
    except ImportError:
        SCORING_THINK       = _SCORING_THINK_DEFAULT
        SCORING_NUM_PREDICT = _SCORING_NUM_PREDICT_DEFAULT
    if SCORING_THINK:
        from config import model_supports_thinking
        _think_actual = SCORING_THINK and model_supports_thinking(model if model else SCORING_MODEL)
        print(f"   [scoring] think={_think_actual}, num_predict={SCORING_NUM_PREDICT}")
    try:
        r = requests.post(
            f"{ollama_url}/api/chat",
            json={
                "model":   model if model else SCORING_MODEL,
                "think":   SCORING_THINK and model_supports_thinking(model if model else SCORING_MODEL),
                "stream":  False,
                "options": {"num_predict": SCORING_NUM_PREDICT},
                "messages": [{"role": "user", "content": prompt}],
            },
            timeout=SCORE_TIMEOUT,
        )
        if r.status_code != 200:
            print(f"   [scoring] Ollama error {r.status_code}")
            return ""
        data = r.json()
        return data.get("message", {}).get("content", "").strip()
    except Exception as e:
        print(f"   [scoring] Ollama exception: {e}")
        return ""


def _parse_scoring_response(text: str) -> dict:
    """
    Parse verbose scoring response.
    Extracts all signals plus final verdict/score/issues/recommendation.
    """
    result = {
        "verdict":        "unknown",
        "score":          None,
        "issues":         [],
        "prescription":   [],
        "recommendation": "ready",
        "anchor":         None,
        "anchor_reason":  "",
        "argument":       {},
        "disqualifiers":  {},
        "quality":        {},
        "genericity":     {},
        "voice":          {},
        "confidence":     "",
        "confidence_reason": "",
    }

    lines = [l.strip() for l in text.strip().splitlines() if l.strip()]

    for line in lines:
        lower = line.lower()

        # Anchor
        if lower.startswith("anchor:") and "reason" not in lower:
            try:
                result["anchor"] = float(re.search(r"[\d.]+", line.split(":",1)[1]).group())
            except Exception:
                pass
        elif lower.startswith("anchor_reason:"):
            result["anchor_reason"] = line.split(":",1)[1].strip()

        # Argument structure A1-A4
        elif re.match(r"^a[1-4]:", lower):
            key = line[:2].upper()
            val = line.split(":",1)[1].strip()
            result["argument"][key] = val

        # Disqualifiers D1-D10
        elif re.match(r"^d\d+:", lower):
            key = re.match(r"^(d\d+):", lower).group(1).upper()
            val = line.split(":",1)[1].strip()
            result["disqualifiers"][key] = val

        # Quality signals Q1-Q8
        elif re.match(r"^q[1-8]:", lower):
            key = re.match(r"^(q[1-8]):", lower).group(1).upper()
            val = line.split(":",1)[1].strip()
            result["quality"][key] = val

        # Genericity signals G1-G4
        elif re.match(r"^g[1-4]:", lower):
            key = re.match(r"^(g[1-4]):", lower).group(1).upper()
            val = line.split(":",1)[1].strip()
            result["genericity"][key] = val

        # Voice
        elif lower.startswith("v1:"):
            result["voice"]["V1"] = line.split(":",1)[1].strip()
        elif lower.startswith("v2:"):
            result["voice"]["V2"] = line.split(":",1)[1].strip()

        # Confidence
        elif lower.startswith("confidence:") and "reason" not in lower:
            result["confidence"] = line.split(":",1)[1].strip().lower()
        elif lower.startswith("confidence_reason:"):
            result["confidence_reason"] = line.split(":",1)[1].strip()

        # Final verdict
        elif lower.startswith("verdict:"):
            v = line.split(":",1)[1].strip().lower()
            if v in ("pass", "fail", "weak"):
                result["verdict"] = v

        elif lower.startswith("score:"):
            try:
                num = re.search(r"[\d.]+", line.split(":",1)[1])
                if num:
                    result["score"] = round(float(num.group()), 1)
            except Exception:
                pass

        elif lower.startswith("issues:"):
            raw = line.split(":",1)[1].strip()
            if raw.lower() != "none":
                result["issues"] = [i.strip() for i in raw.split(";") if i.strip()]

        elif lower.startswith("recommendation:"):
            rec = line.split(":",1)[1].strip().lower()
            if rec in ("ready", "rewrite", "regenerate"):
                result["recommendation"] = rec


        elif lower.startswith("prescription:"):
            raw_p = line.split(":",1)[1].strip()
            if raw_p.lower() not in ("none", ""):
                result["prescription"] = [p.strip() for p in raw_p.split(";") if p.strip()]
    # Derive verdict from parsed signals when model omits explicit verdict: line
    if result["verdict"] == "unknown":
        _dq = result.get("disqualifiers", {})
        _has_fail_dq = any(
            "present" in str(v).lower()
            for v in _dq.values()
        )
        _q_present = sum(
            1 for v in result.get("quality", {}).values()
            if "present" in str(v).lower()
        )
        _g_present = sum(
            1 for v in result.get("genericity", {}).values()
            if "present" in str(v).lower()
        )
        if _has_fail_dq:
            result["verdict"] = "fail"
            if result["recommendation"] == "ready":
                result["recommendation"] = "regenerate"
        elif _q_present >= 4 and _g_present == 0:
            result["verdict"] = "pass"
        elif _q_present >= 2:
            result["verdict"] = "weak"
            if result["recommendation"] == "ready":
                result["recommendation"] = "rewrite"

    return result


def _update_metadata(metadata_path: str, scoring: dict) -> bool:
    try:
        with open(metadata_path, encoding="utf-8") as f:
            meta = json.load(f)
        meta["scoring"] = scoring
        with open(metadata_path, "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False, indent=2)
        return True
    except Exception as e:
        print(f"   [scoring] metadata update failed: {e}")
        return False


def _print_breakdown(parsed: dict, raw_response: str) -> None:
    """Print full verbose breakdown to console."""
    print()
    print("   [scoring] -- full breakdown ----------------------------------")

    if parsed.get("anchor"):
        print(f"   ANCHOR: {parsed['anchor']}  {parsed.get('anchor_reason','')}")

    if parsed.get("argument"):
        print("   -- Argument structure --")
        for k, v in parsed["argument"].items():
            print(f"   {k}: {v}")

    disq = parsed.get("disqualifiers", {})
    present_d = {k: v for k, v in disq.items() if "present" in v.lower()}
    absent_d  = {k: v for k, v in disq.items() if "absent" in v.lower()}
    if present_d:
        print("   -- Disqualifiers PRESENT --")
        for k, v in present_d.items():
            print(f"   {k}: {v}")
    if absent_d:
        print("   -- Disqualifiers absent --")
        print("   " + "  ".join(absent_d.keys()))

    qual = parsed.get("quality", {})
    present_q = {k: v for k, v in qual.items() if "present" in v.lower()}
    absent_q  = {k: v for k, v in qual.items() if "absent" in v.lower()}
    if present_q:
        print("   -- Quality signals PRESENT --")
        for k, v in present_q.items():
            print(f"   {k}: {v}")
    if absent_q:
        print("   -- Quality signals absent --")
        print("   " + "  ".join(absent_q.keys()))

    gen = parsed.get("genericity", {})
    present_g = {k: v for k, v in gen.items() if "present" in v.lower()}
    absent_g  = {k: v for k, v in gen.items() if "absent" in v.lower()}
    if present_g:
        print("   -- Genericity signals PRESENT --")
        for k, v in present_g.items():
            print(f"   {k}: {v}")
    if absent_g:
        print("   -- Genericity signals absent --")
        print("   " + "  ".join(absent_g.keys()))

    if parsed.get("voice"):
        print("   -- Voice --")
        for k, v in parsed["voice"].items():
            print(f"   {k}: {v}")

    if parsed.get("confidence"):
        print(f"   CONFIDENCE: {parsed['confidence']}  {parsed.get('confidence_reason','')}")

    print("   --------------------------------------------------------------")


def score_article(
    article_dir: str,
    item: dict,
    ollama_url: str,
    article_type: str = "",
    model: str = "",
) -> dict:
    from config import model_supports_thinking
    _FALLBACK_MODEL = "qwen3.6:27b"
    # Priority: SCORING_MODEL (Settings override) > item model > fallback
    if SCORING_MODEL:
        _scoring_model = SCORING_MODEL
    elif model:
        _scoring_model = model
    else:
        _scoring_model = _FALLBACK_MODEL
        _scoring_model = _FALLBACK_MODEL
    article_path  = os.path.join(article_dir, "article.md")
    metadata_path = os.path.join(article_dir, "metadata.json")

    try:
        with open(article_path, encoding="utf-8") as f:
            raw = f.read()
        article_text = re.sub(r"<!--.*?-->", "", raw, flags=re.DOTALL).strip()
    except Exception as e:
        print(f"   [scoring] Cannot read article: {e}")
        return {}

    if len(article_text) < 300:
        print("   [scoring] Article too short to score -- skipping")
        return {}

    title    = item.get("topic", "")
    category = item.get("category", "other")
    try:
        with open(metadata_path, encoding="utf-8") as f:
            meta = json.load(f)
        title    = meta.get("title", title)
        category = meta.get("category", category)
    except Exception:
        pass

    article_for_prompt = article_text[:16000]
    if len(article_text) > 16000:
        article_for_prompt += "\n\n[... article truncated for scoring ...]"

    _article_type = article_type or item.get("article_type", "")
    _override     = SCHEMA_OVERRIDES.get(_article_type, {})
    _suppressed   = _override.get("suppress", [])
    _schema_ctx   = ""
    if _override:
        _schema_ctx  = f"ARTICLE TYPE: {_article_type}\n"
        _schema_ctx += _override.get("context", "") + "\n"
        if _suppressed:
            _schema_ctx += f"SUPPRESSED CRITERIA (do NOT apply): {', '.join(_suppressed)}\n"
        _schema_ctx += "\n"

    prompt = SCORING_PROMPT.format(
        title          = title,
        category       = category,
        article        = article_for_prompt,
        schema_context = _schema_ctx,
    )

    print(f"   [scoring] Scoring article ({_scoring_model})...")
    raw_response = _call_ollama(prompt, ollama_url, _scoring_model)

    if not raw_response:
        print("   [scoring] No response -- skipping score")
        return {}

    parsed = _parse_scoring_response(raw_response)

    scoring = {
        "attempts":          1,
        "verdict":           parsed["verdict"],
        "score":             parsed["score"],
        "issues":            parsed["issues"],
        "recommendation":    parsed["recommendation"],
        "anchor":            parsed.get("anchor"),
        "argument":          parsed.get("argument", {}),
        "disqualifiers":     parsed.get("disqualifiers", {}),
        "quality":           parsed.get("quality", {}),
        "genericity":        parsed.get("genericity", {}),
        "voice":             parsed.get("voice", {}),
        "confidence":        parsed.get("confidence", ""),
        "confidence_reason": parsed.get("confidence_reason", ""),
        "prescription":      parsed.get("prescription", []),
        "scored_at":         datetime.now().isoformat(),
        "model":             _scoring_model,
        "raw_response":      raw_response,
    }

    _update_metadata(metadata_path, scoring)

    score_str = f"{scoring['score']}/10" if scoring["score"] is not None else "?"
    flag_str  = "  [FLAGGED]" if scoring["verdict"] in ("fail", "weak") else ""
    print(f"   [scoring] {scoring['verdict'].upper()}  score={score_str}  rec={scoring['recommendation']}{flag_str}")
    if scoring["issues"]:
        for issue in scoring["issues"]:
            print(f"             - {issue}")
    if scoring.get("prescription"):
        print(f"   [prescription]")
        for fix in scoring["prescription"]:
            print(f"             -> {fix}")

    _print_breakdown(parsed, raw_response)

    return scoring