# pipeline/suitability_gate.py
# Evaluates research quality using chunk_meta from _retrieve_context().
# Called from generate_run.py after _retrieve_context(), before Focus Picker.
#
# chunk_meta structure (from generate_run.py):
#   {"id": "C01", "score": float, "trust": str, "src": str,
#    "domain": str, "url": str, "collection": str, "text": str}
#
# Gate logic:
#   1. Hard gate    -- research chunk count < GATE_MIN_CHUNKS  -> REJECT
#   2. Quality gate -- avg reranker score < GATE_MIN_AVG_SCORE -> REJECT
#      (all-zero scores = reranker offline; quality gate relaxed automatically)
#   3. Source diversity -- unique domains < GATE_MIN_SOURCES   -> contributes to THIN
#
# Returns (passed: bool, verdict: str, reason: str, stats: dict)
# verdict: "PASS" | "THIN" | "CRITICAL"

from __future__ import annotations
from typing import Any


def check_research_quality(
    chunk_meta: list[dict[str, Any]],
    context_research: str = "",
    *,
    min_chunks: int       = 5,
    min_avg_score: float  = 0.25,
    min_sources: int      = 2,
) -> tuple[bool, str, str, dict[str, Any]]:
    """
    Evaluate research quality from chunk_meta returned by _retrieve_context().

    Parameters
    ----------
    chunk_meta       : list of chunk dicts (id, score, trust, src, domain, url, text)
    context_research : raw research context string (for char count)
    min_chunks       : minimum research chunk count (hard gate)
    min_avg_score    : minimum avg reranker score (quality gate)
                       Ignored if all scores are 0.0 (reranker offline).
    min_sources      : minimum unique source domains (contributes to THIN verdict)

    Returns
    -------
    passed  : bool   -- True = proceed
    verdict : str    -- "PASS" | "THIN" | "CRITICAL"
    reason  : str    -- human-readable summary
    stats   : dict   -- full metrics for logging / queue field
    """

    # Research chunks only (exclude persona, evergreen)
    research = [c for c in (chunk_meta or []) if c.get("src") not in ("persona", "evergreen")]
    count    = len(research)

    # Reranker scores
    scores        = [float(c["score"]) for c in research if "score" in c and c["score"] is not None]
    scored_count  = len(scores)
    avg_score     = (sum(scores) / scored_count) if scored_count > 0 else 0.0
    reranker_live = scored_count > 0 and avg_score > 0.001  # all-zero = reranker offline

    # Source diversity
    domains = set()
    for c in research:
        d = c.get("domain", "")
        if d and d not in ("unknown", ""):
            domains.add(d)
    unique_sources = len(domains)

    chars = len(context_research) if context_research else 0

    stats = {
        "research_chunks":  count,
        "avg_score":        round(avg_score, 4),
        "scored_count":     scored_count,
        "reranker_live":    reranker_live,
        "unique_sources":   unique_sources,
        "context_chars":    chars,
        "min_chunks":       min_chunks,
        "min_avg_score":    min_avg_score,
        "min_sources":      min_sources,
    }

    issues = []

    # --- Hard gate: chunk count ---
    if count == 0:
        return False, "CRITICAL", "Zero research chunks -- article will hallucinate.", stats

    if count < min_chunks:
        issues.append(f"only {count} research chunk(s), minimum {min_chunks}")

    # --- Quality gate: avg reranker score (skip if reranker offline) ---
    if reranker_live and avg_score < min_avg_score:
        issues.append(f"avg reranker score {avg_score:.3f} below threshold {min_avg_score:.3f}")

    # --- Source diversity ---
    if unique_sources < min_sources:
        issues.append(f"only {unique_sources} unique domain(s), recommend {min_sources}+")

    if not issues:
        verdict = "PASS"
        reason  = (
            f"{count} chunks, avg score {avg_score:.3f}, {unique_sources} sources"
            + (" [reranker offline]" if not reranker_live else "")
        )
        return True, verdict, reason, stats

    verdict = "CRITICAL" if count < min_chunks else "THIN"
    passed  = False
    reason  = "; ".join(issues)
    return passed, verdict, reason, stats


def print_gate_result(
    passed: bool,
    verdict: str,
    reason: str,
    stats: dict[str, Any],
) -> None:
    if passed:
        label = "PASS" if stats.get("context_chars", 0) >= 8000 else "OK"
        print(
            f"   [gate] {label} -- {stats['research_chunks']} chunks | "
            f"avg score {stats['avg_score']:.3f} | {stats['unique_sources']} sources | "
            f"{stats['context_chars']} chars"
        )
    else:
        print(f"   [gate] {verdict} -- {reason}")
        print(f"   [gate] chunks={stats['research_chunks']} | "
              f"avg_score={stats['avg_score']:.3f} | "
              f"sources={stats['unique_sources']} | "
              f"chars={stats['context_chars']}")


def gate_prompt_interactive(verdict: str, reason: str) -> str:
    """
    Interactive prompt when gate fails in non-night-run mode.
    Returns: 'continue' | 'skip' | 'delete'
    """
    print()
    print("  " + "=" * 56)
    print(f"  GATE {verdict}: {reason}")
    print("  " + "=" * 56)
    print("  [C] Continue anyway (accept risk)")
    print("  [R] Skip -- retry research later")
    print("  [D] Delete topic from queue")
    print()
    while True:
        choice = input("  Choice [C/R/D]: ").strip().upper()
        if choice == "C":
            return "continue"
        if choice == "R":
            return "skip"
        if choice == "D":
            return "delete"
        print("  Enter C, R, or D.")
