# NonSequitur — Pipeline Demo

## What happens when you run it

```
┌─────────────────────────────────────────────────────────────────┐
│                        NONSEQUITUR                              │
│         Autonomous Research & Publishing Platform               │
└─────────────────────────────────────────────────────────────────┘
```

---

### Stage 1 — Discovery

You type a query. The agent searches across multiple engines simultaneously.

```
Query: "Grim Dawn Fangs of Asterkarn"
                    │
        ┌───────────┼───────────┬──────────┐
        ▼           ▼           ▼          ▼
    SearXNG      Reddit    Google News  HuggingFace
    (4 pages)   (community)  (news)     (papers)
        │           │           │          │
        └───────────┴───────────┴──────────┘
                         │
              [fetch_all] Blocked filter: -25 results
                         │
                    ┌────▼────┐
                    │  24     │  clean results
                    │ results │  with domain column
                    └────┬────┘
                         │
        ╔══ TOPIC ════════════════════════╗
        ║  [ 1] Grim Dawn Final Chapter   ║
        ║  [ 2] Crate Entertainment Last  ║
        ║  [ 3] Fangs of Asterkarn Scope  ║
        ║   ...                           ║
        ║  [0] Write custom               ║
        ╚═════════════════════════════════╝
                         │
        ╔══ FOCUS ════════════════════════╗
        ║  One sentence thesis —          ║
        ║  human-written, LLM enforces it ║
        ╚═════════════════════════════════╝
```

---

### Stage 2 — Deep Research

The agent generates targeted search queries from your focus angle, crawls full article text, and indexes everything into Qdrant.

```
Focus: "Ten years of Grim Dawn proves patient indie
        development beats live-service churn"
                    │
         LLM generates 4-6 search queries
                    │
    ┌───────────────┼───────────────┐
    ▼               ▼               ▼
 Query 1         Query 2         Query 3
 "Grim Dawn      "indie ARPG     "Fangs of
  10 year        development     Asterkarn
  history"       model 2026"     size scope"
    │               │               │
    └───────────────┴───────────────┘
                    │
              Crawl4AI + Playwright
              (full text extraction)
                    │
            ┌───────▼────────┐
            │  content_filter │  ← removes garbage
            │  .is_garbage()  │     before indexing
            └───────┬────────┘
                    │
              qwen3-embedding
              8b-q8_0 (dim=4096)
                    │
            ┌───────▼────────┐
            │    Qdrant       │
            │  research_games │  256 chunks indexed
            │  (hybrid BM25)  │
            └────────────────┘
```

---

### Stage 3 — Generate

The agent retrieves the most relevant chunks, reranks them, and generates the article with strict prompt constraints.

```
            ┌────────────────┐
            │    Qdrant       │
            │  research_games │
            │  persona_lukasz │
            └───────┬────────┘
                    │
              Hybrid RAG retrieval
              top-100 → reranker → top-35
                    │
            per-trust-tier filter:
            press/trusted  ≥ 0.001
            community      ≥ 0.010
            unknown        ≥ 0.020
                    │
    ┌───────────────┼───────────────┐
    ▼               ▼               ▼
AUTHOR VOICE    RESEARCH        ARTICLE
(persona RAG)   FACTS (RAG)     DIRECTION
HOW to write    WHAT to say     mandatory thesis
    │               │               │
    └───────────────┴───────────────┘
                    │
              _build_prompt()
                    │
              Ollama qwen3.5:27b
              /api/chat think:false
                    │
            ┌───────▼────────┐
            │   Article       │
            │  1500-2200 words│
            │  H2 every 2-3 ¶ │
            │  thesis enforced│
            └───────┬────────┘
                    │
              ✓ Saved: output/
              ✓ Slug: grim-dawn-fangs-of-asterkarn
```

---

### Stage 4 — Rewrite (optional)

```
            ┌───────────────┐
            │  Local draft   │
            └───────┬───────┘
                    │
              Anthropic API
              Claude Sonnet
              (editorial polish)
                    │
            ┌───────▼───────┐
            │  Final article │
            └───────┬───────┘
                    │
              Payload CMS     ← in development
              (auto-publish)
```

---

## Knowledge Base structure

```
Qdrant collections:

  RESEARCH (temporary — cleared after article removed from queue)
  ├── research_games
  ├── research_ai-data
  ├── research_hardware
  └── research_software

  KNOWLEDGE (permanent — human-reviewed, evergreen)
  ├── knowledge_games
  ├── knowledge_games_candidates   ← 7-day review window
  ├── knowledge_ai-data
  └── knowledge_ai-data_candidates

  PERSONAS (permanent — author voice)
  └── persona_lukasz               ← opinions, style, worldview
```

---

## Domain trust system

```
domains_trusted.json                domains_blocked.json
─────────────────────               ────────────────────
press     boost 0.80-0.92           youtube.com
trusted   boost 0.85-0.95           twitter.com / x.com
community boost 0.55-0.72           instagram.com
                                    resetera.com
         ↓                          neogaf.com
per-tier RAG threshold              steamcommunity.com
                                    merriam-webster.com
press/trusted  ≥ 0.001              netflix.com
community      ≥ 0.010              eneba.com / g2a.com
unknown        ≥ 0.020              + 50 more
```

---

## Terminal UI — Discovery selector

```
  [ 1]  Grim Dawn Final DLC Fangs of Asterkarn Launches July 23   [searxng]  pcgamer.com         (14.5)
  [ 2]  Fangs of Asterkarn is 86% the Size of Base Grim Dawn      [searxng]  soren.com           (14.5)
  [ 3]  Grim Dawn: Fangs of Asterkarn on Steam                    [searxng]  store.steampowered  (14.5)
  [ 4]  Grim Dawn promises major UI changes before expansion       [searxng]  massivelyop.com     (12.5)
  [ 5]  Grim Dawn marks its 10th anniversary                      [searxng]  pcgamer.com         (10.5)

  Commands: number | range 1-5 | list 1,3,7 | a=all  c=clear  p=preview  Enter=confirm
```

---

## Slug autocomplete (live)

```
  Topic slug:
  > grim-da
  Suggestions:
    [1] grim-dawn-fangs-of-asterkarn   (5)
    [2] grim-dawn                      (5)
  > 1
  ✓ Slug: grim-dawn-fangs-of-asterkarn
```
