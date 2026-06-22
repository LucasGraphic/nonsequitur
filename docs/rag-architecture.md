# NonSequitur — RAG Architecture

NonSequitur uses a hybrid retrieval system combining dense vectors, sparse BM25 vectors, and neural reranking. This document explains how research chunks flow from web fetch to article prompt.

---

## Collections in Qdrant

Each article topic has chunks spread across multiple collections:

| Collection | Contents | Lifetime |
|-----------|----------|---------|
| `research_{category}` | Raw chunks from web research for active queue items | Until item removed from queue |
| `knowledge_{category}` | Curated facts extracted by LLM from research, human-approved | Permanent |
| `knowledge_evergreen` | Reusable concepts not tied to a specific topic | Permanent |
| `persona_{name}` | Author voice chunks | Permanent |

Research chunks are temporary. Once an item is removed from queue, its research chunks are deleted. Knowledge chunks survive and improve future articles on similar topics.

---

## Vector Schema

Every chunk is stored with three vector types:

- **`dense`** — 4096-dim embedding from `qwen3-embedding:8b-q8_0`
- **`sparse`** — BM25 sparse vector for keyword matching
- **`trigger_dense`** — 4096-dim embedding of the trigger field (persona chunks only)

Hybrid search uses Reciprocal Rank Fusion (RRF) to merge dense and sparse results.

---

## Retrieval Flow

### 1. BM25 Pre-ranking

Before any network calls, research chunks are pre-ranked locally using BM25 against the article topic and focus. This runs on all chunks in the collection via `scroll()` (100% coverage), not HNSW approximate search. Top 100 chunks advance.

HNSW was abandoned because it returned only ~18% of valid chunks on niche topics. BM25 scroll gives full coverage.

### 2. Semantic Dedup

Before reranking, research chunks are deduplicated by cosine similarity (threshold 0.92) on their dense vectors. Duplicate articles from different sources often produce near-identical chunks — dedup removes them before they consume reranker capacity.

### 3. Neural Reranking

The top pool (research + knowledge + evergreen, up to 100 chunks) goes to `BAAI/bge-reranker-v2-m3` as a cross-encoder. The reranker scores each chunk against the full query context (topic + focus + sub-queries). This is the main quality filter — BM25 selects candidates, the reranker selects the best.

Persona chunks **bypass the reranker**. They are ranked by trigger similarity (cosine distance between the chunk's `trigger_dense` vector and the article's focus vector) and injected separately.

### 4. Context Assembly

After reranking, `_build_prompt()` assembles the final context in this order (recency bias — model attends most strongly to content closest to the task):

```
=== BACKGROUND KNOWLEDGE ===   <- evergreen concepts
=== RESEARCH FACTS ===         <- reranked research + knowledge chunks
=== AUTHOR VOICE ===           <- persona chunks (not reranked)
=== ARTICLE DIRECTION ===      <- focus angle
=== ARTICLE STRUCTURE ===      <- schema sections, opening/closing rules
TASK: write the article
```

---

## Per-Source Score Normalization

Qdrant RRF scores vary by collection size:
- Large collections (research, 200-500 chunks): scores ~0.01–0.08
- Small collections (evergreen, knowledge, 3-20 chunks): scores ~0.93–0.99

Without normalization, small collections would dominate every retrieval. Each source group is normalized independently to [0,1] and then weighted:

| Source | Weight |
|--------|--------|
| research | 1.00 |
| knowledge | 0.85 |
| evergreen | 0.70 |
| persona | 0.60 |

---

## Knowledge Base

The knowledge base is a permanent collection of curated facts that persist across article cycles. Every article on a similar topic benefits from everything curated before it.

### Two types of knowledge chunks

**`knowledge_{category}`** — topic-specific facts. Named products, specific releases, benchmark numbers, named people. Linked to a `topic_slug` for targeted retrieval.

**`knowledge_evergreen`** — reusable concepts. Explanations of techniques, historical context, frameworks. Retrieved by category match, not topic slug.

### Extraction flow

After research, LLM reads each fetched URL and distills 3–6 factual paragraphs per source. Human reviews in the Knowledge menu (keep / delete / edit). Approved chunks are embedded and moved to the permanent collection. Research chunks are then wiped.

Use `[8] Extract facts (LLM)` in the Knowledge menu — always better than `[3] Inspect & Promote` for crawled content. Promote is for manually written content only.

---

## Suitability Gate

Before generation, the suitability gate checks research quality:

- **Hard gate:** minimum 5 research chunks
- **Quality gate:** average reranker score ≥ 0.25 (skipped if reranker was offline)
- **Source diversity:** minimum 2 unique domains (soft, contributes to THIN verdict)

On THIN result:
- Interactive mode: prompts `[C]ontinue / [R]etry research / [D]elete`
- Night run: marks topic as `insufficient_research`, skips generation

---

## Uber Research

When research is thin, Uber Research runs a second pass:

1. LLM gap analysis: reads existing chunks, generates 4 questions about what is missing (guided by article focus)
2. For each question: extracts 4-6 keywords → SearXNG search → fetch → BM25 URL pre-score → relevance filter (3+ topic keyword matches) → chunk + embed + index
3. Gate re-check after indexing

Uber Research is triggered automatically on THIN gate result, or manually via queue `[r] → [3]`.

---

## Domain Trust

`data/domains_trusted.json` — 328 entries across 12 categories with per-entry boost values (0.50–0.95). Trusted domains get a retrieval boost applied before reranking.

`data/domains_blocked.json` — 50+ entries. Blocked domains are filtered before fetch, not after. No compute wasted on known-bad sources.

Domain categories: `press`, `trusted`, `community`, `academic`, `official`, `aggregator`, `unknown`.
