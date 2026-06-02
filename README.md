# NonSequitur — Autonomous Research & Publishing Platform

> *Non sequitur* — in logic, a conclusion that doesn't follow from its premises. In practice, a platform that doesn't follow the industry's obsession with scale, reach, and optimizing for what already ranks.
>
> *The stack exists to serve one purpose: give a single person the research capacity of a newsroom and the editorial independence of nobody's employee.*

Most AI writing tools make it faster to write about what everyone is already writing about. NonSequitur is built for editorial control: any topic, any angle, any voice — with honest analysis and no PR appeasement. Generated entirely on local hardware, published directly to CMS, with no cloud APIs in the core pipeline.

It searches the web, reads dozens of sources, and writes in the author's voice — not by prompt injection, but by retrieving stored opinions and style from a vector database at generation time. Every article is anchored to a human-written thesis the model cannot override. The pipeline runs unattended. The editorial direction does not.

Under the hood: qwen3-embedding at 4096 dimensions, hybrid dense+sparse retrieval with BM25/RRF fusion, BAAI neural reranking, models up to 122B parameters, a self-hosted meta-search engine, Chromium-based full-text crawler, and a Qdrant vector database capable of millisecond similarity search across millions of vectors — all on local hardware, no subscriptions, no external APIs.

Live output: **[lucasgraphic.com](https://lucasgraphic.com)** — DATA / LAB / PORTFOLIO sections.

---

## Architecture

```
Discovery ──► Research ──► Generate ──► Claude Rewrite ──► Payload CMS
   │              │             │              │
SearXNG        Qdrant        Ollama        Anthropic API
Reddit         Crawl4AI      qwen3.5       (optional polish)
Google News    Trafilatura   27b/122b
HuggingFace    Reranker
```

**→ [Full pipeline walkthrough with ASCII diagrams](docs/ARCHITECTURE.md)**

### Pipeline Stages

**1. Discovery** — finds candidate topics via SearXNG (self-hosted meta-search), Reddit, Google News, and HuggingFace. Results are filtered through `domain_config.py` (trusted/blocked domain registry), scored by source quality and keyword relevance, and presented in an interactive terminal selector with per-result domain visibility. Supports live char-by-char slug autocomplete and LLM-generated topic name suggestions.

**2. Deep Research** — for each queued topic, generates 4–6 LLM search queries targeting the article's focus angle, fetches full-text content via Crawl4AI (Chromium-based) with Playwright fallback and Trafilatura as a final extraction layer. Text is chunked at 800 chars with 100-char overlap, embedded with `qwen3-embedding:8b-q8_0` (dim=4096), and indexed into per-category Qdrant collections using hybrid dense+sparse (BM25/RRF) vectors.

**3. Focus Picker** *(in development)* — before generation, presents 20 LLM-derived article angles drawn from the top research chunks. The author selects, edits, or writes their own thesis. Night run skips this step and uses the stored focus directly.

**4. Generate** — retrieves top-20 chunks via hybrid RAG with per-trust-tier score thresholds, reranks with BAAI/bge-reranker-v2-m3 for precision ordering, builds a structured prompt with persona context and research facts in separate blocks, and generates 800–1200 word articles with mandatory focus angle enforcement.

**5. Claude Rewrite** *(optional)* — sends the draft to Anthropic Claude Sonnet for editorial polish while preserving all factual content from the research context. Structural arguments and sourced claims are never altered.

**6. Payload CMS Import** *(in development)* — pushes finished articles directly to PayloadCMS via API. Dev environment only until stable.

---

## Stack

| Component | Technology | Host |
|-----------|-----------|------|
| LLM Generate | Ollama + qwen3.5:27b / 122b | Windows, RTX 5090 |
| LLM Embed | Ollama + qwen3-embedding:8b-q8_0 | Ubuntu, GTX 1080 |
| LLM Score / Focus | Ollama + qwen3.5:4b | Ubuntu, GTX 1080 |
| Vector DB | Qdrant | Ubuntu |
| Neural Reranker | BAAI/bge-reranker-v2-m3 (FastAPI) | Ubuntu, GTX 1080 |
| Web Search | SearXNG (self-hosted) | Ubuntu |
| Web Crawler | Crawl4AI (FastAPI + Chromium) | Ubuntu |
| Fallback Fetch | Playwright service | Ubuntu |
| Cache | Valkey (Redis-compatible) | Ubuntu |
| CMS | PayloadCMS 3.x + MongoDB | Ubuntu |
| Frontend | Next.js 15 + Tailwind CSS v4 | Ubuntu (PM2/nginx) |

### Model Tiers

| Key | Model | Parameters | Use |
|-----|-------|-----------|-----|
| DEV | qwen2.5:7b | 7B | Fast iteration, testing |
| NORMAL | qwen3.5:27b | 27B | Daily production |
| NORMAL2 | qwen3.5:35b-a3b | 35B MoE | Evaluation variant |
| MAX | qwen3.5:122b | 122B | Maximum quality, CPU offload |
| Embed | qwen3-embedding:8b-q8_0 | 8B | 4096-dim dense vectors |
| Score | qwen3.5:4b | 4B | Knowledge scoring, Focus Picker |

All qwen3.5 models require `/api/chat` with `think: false`. `num_predict=2500` prevents infinite generation loops inherent to the architecture.

---

## Neural Reranker

After hybrid RAG retrieval, all candidate chunks are passed through **BAAI/bge-reranker-v2-m3** — a cross-encoder model that scores each chunk against the full query context, not just vector similarity. This is the difference between *finding chunks that look related* and *finding chunks that actually answer the question*.

The reranker runs as a dedicated FastAPI service on the Ubuntu/GTX 1080 machine. It receives the raw retrieval pool (up to 35 chunks) and returns a precision-ordered list. Only the top 20 survive into `_build_prompt()`.

Why this matters: embedding similarity is a blunt instrument. A chunk about "game engine performance" scores highly for almost any gaming query. The reranker demotes generic context and promotes chunks with direct factual relevance to the specific article angle. The result is a tighter, less diluted research block — which directly improves output specificity.

---

## Vector Database & Scale

Qdrant stores all research, knowledge, and persona context as high-dimensional vectors across per-category collections. Each vector has 4096 dimensions — the full output of `qwen3-embedding:8b-q8_0`, not a compressed approximation.

**What this means in practice:**

- Millisecond approximate nearest-neighbor search across collections with hundreds of thousands of vectors
- Hybrid retrieval combining dense semantic vectors with sparse BM25 keyword vectors, fused via RRF (Reciprocal Rank Fusion)
- Per-payload filters on `topic_slug`, `category`, and `evergreen` flags — applied at query time without post-processing
- Named vectors per collection: `dense` and `sparse` stored separately, queried jointly
- HNSW indexing maintains sub-10ms query times regardless of collection size

**Collections:**

```
research_{category}               ← per-item research, ~50-300 chunks per article
knowledge_{category}              ← curated evergreen content, human-reviewed
knowledge_{category}_candidates   ← 7-day staging window, pending review
knowledge_evergreen               ← cross-category permanent context
persona_{name}                    ← author voice and opinion chunks
```

At current embedding density (4096 dims × float32 = 16KB per vector), a collection of 100,000 chunks occupies ~1.6GB of raw vector data before HNSW graph overhead. Qdrant handles this in memory with on-disk overflow — query latency stays flat.

The practical ceiling for this hardware configuration is in the millions of chunks. The pipeline will never reach it.

---

## Knowledge Base

Human-curated context that persists beyond individual article research cycles. Every promoted chunk carries a `topic_slug`, `category`, and `evergreen` flag — allowing RAG retrieval to be filtered by subject matter at query time rather than relying on pure semantic similarity.

**RAG retrieval flow (example: games/baldurs-gate-2):**

```
generate(category=games, slug=baldurs-gate-2)
  → research_games        (hybrid, top_k=20, filter: item_id)
  → knowledge_games       (hybrid, top_k=7,  filter: topic_slug=baldurs-gate-2)
  → knowledge_evergreen   (hybrid, top_k=5,  filter: category=games)
  → persona_lukasz        (dense,  top_k=20)
  → reranker              (cross-encode all → top 20)
  → _build_prompt()
```

Knowledge feed supports three input methods: URL clip, paste from clipboard (chunk_size 1500), and manual entry. Garbage detection runs automatically before review — stripping GDPR/TCF boilerplate, newsletter CTAs, Discourse comment metadata, cookie consent fragments, and affiliate copy. What reaches the knowledge base is signal, not noise.

---

## Prompt Architecture

`pipeline/generate_run.py:_build_prompt()` separates context into distinct semantic blocks. The model receives information in layers — voice first, facts second, direction last:

```
=== YOUR PERSONA ===          ← worldview, editorial stance, tone (HOW to write)
=== AUTHOR VOICE ===          ← RAG chunks from persona_{name} collection
=== RESEARCH FACTS ===        ← RAG chunks from research + knowledge collections
=== ARTICLE DIRECTION ===     ← mandatory focus angle (WHAT to argue)
```

**Hard constraints enforced in prompt:**
- Never invent benchmark numbers, version strings, or release dates not present in research context
- Focus angle is mandatory — the model cannot substitute or dilute it
- No generic CTAs, no PR framing, no hedging language
- H2 headers every 2–3 paragraphs with specific section angles, not topic labels
- No repetition between sections
- No bold topic artefacts as opening lines

`_build_prompt()` is treated as critical infrastructure. Every change affects all article output quality — modifications require full generation test before commit.

---

## Content Filter

`pipeline/content_filter.py` — unified garbage detection, single source of truth for both indexing and RAG retrieval.

Filtered patterns:
- PDF binary data and non-printable character sequences
- JavaScript fragments and JSON-LD structured data leaks
- Affiliate links and coupon copy
- Author biography boilerplate
- SaaS marketing CTAs (`request a demo`, `book a free call`, `start your free trial`)
- Facebook and Instagram navigation leaks
- Cookie consent and GDPR/TCF fragments
- Discourse forum metadata and reply noise
- Off-topic geographic and historical filler content
- Benchmark boilerplate (`scores X in Y benchmark`)

`data/domains_blocked.json` — master blocked domain list covering social media, video platforms, key shops, price aggregators, and dictionary sites. Applied at fetch time and discovery filter time.

`data/domains_trusted.json` — per-category trust tiers (`trusted`, `press`, `community`) with retrieval score boost multipliers (0.55–0.95). Tier determines minimum RAG score threshold and reranker weight.

---

## Discovery UI

Interactive terminal selector:
- Per-result domain display for live source evaluation
- Live char-by-char slug autocomplete via `msvcrt` (Windows) / `termios` (Linux), sourced from `data/slugs.json`
- 10 LLM-generated topic name suggestions per article
- FOCUS field — editorial thesis always human-written, never LLM-suggested
- Merge/separate decision before naming for multi-URL imports
- Queue commands use labeled shortcuts (`[cd]`, `[re]`, `[rr]`) to avoid numeric conflicts

---

## What Works

- [x] Full Discovery → Research → Generate pipeline
- [x] Hybrid RAG with dense + sparse vectors (BM25/RRF fusion)
- [x] Per-category Qdrant collections with payload indexes
- [x] BAAI/bge-reranker-v2-m3 neural reranking
- [x] Persona injection via RAG (separate from research context)
- [x] Focus angle enforcement in prompt — model cannot override
- [x] Unified content filter (`content_filter.py`)
- [x] Blocked domain filter at fetch time
- [x] Per-trust-tier RAG score thresholds
- [x] Live slug autocomplete
- [x] Claude Sonnet rewrite stage
- [x] Qdrant cleanup on queue item removal and reset
- [x] 10-topic LLM suggestion picker in discovery
- [x] Knowledge base with human review flow (clip / paste / manual)
- [x] Auto-garbage detection before knowledge review
- [x] `knowledge_evergreen` cross-category retrieval with category filter
- [x] Dynamic model selection from Ollama `/api/tags`

## In Development

- [ ] Focus Picker — interactive angle selector before generation
- [ ] Payload CMS import — dev environment first
- [ ] Night run — batch pipeline with morning digest
- [ ] `_finalize_item()` — unified URL/query/upgrade flow
- [ ] SearXNG engine tuning — paging support, more sources
- [ ] Uber Research — iterative research with coverage scoring
- [ ] Personas — neutral / critic / paranoic `.md` style files

---

## Performance Benchmarks

| Metric | Value |
|--------|-------|
| Generate time (27b, focused) | ~75–90s |
| Article body length | ~6000–9000 chars |
| Token output | ~1800–2500 |
| RAG top_k | 20 (after reranking from 35) |
| num_predict cap | 2500 |
| Target word count (27b) | 800–1200 words |
| Embedding dimensions | 4096 |
| Vector size per chunk | ~16KB (float32) |

---

## Editorial Mission

NonSequitur is built to cover topics the mainstream gaming and tech press ignores, undercovers, or sanitizes — contrarian angles, honest criticism, analysis without PR appeasement. Quality is measured by argument depth and coverage utility, not trending score or press ratio.

The platform is designed to write what IGN, PC Gamer, and VentureBeat won't — because they are optimising for a different audience, different advertisers, and different incentives. Every article is anchored to a thesis the author chose and the model cannot override. The pipeline automates the labour. The editorial direction remains human.

---

## Screenshots

### Main Menu
![Main menu](docs/splash_main_menu.png)

### Knowledge Menu
![Knowledge menu](docs/knowledge_menu.png)

### Discovery
![Discovery](docs/discovery.png)

### Queue
![Queue](docs/queue.png)

### Research
![Research](docs/research.png)

### Generate
*(screenshot pending)*

---

## Author

**Łukasz Grochal** — photographer, web developer, AI art creator.  
[lucasgraphic.com](https://lucasgraphic.com) · Norway

---

*Built entirely on self-hosted infrastructure. No cloud LLMs in the core pipeline. No subscriptions. No data leaving the local network except for the optional Claude rewrite stage.*
