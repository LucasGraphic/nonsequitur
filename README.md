# NonSequitur — Autonomous Research & Publishing Platform

> *Non sequitur* — in logic, a conclusion that doesn't follow from its premises. In practice, a platform that doesn't follow the industry's obsession with scale, reach, and optimizing for what already ranks.
>
> *The stack exists to serve one purpose: give a single person the research capacity of a newsroom and the editorial independence of nobody's employee.*

Most AI writing tools make it faster to write about what everyone is already writing about. NonSequitur is built for the opposite: any topic, any angle, any voice — with honest analysis and no PR appeasement. The system runs on local hardware, no cloud APIs in the core pipeline, and publishes directly to CMS.

It searches the web, reads dozens of sources, extracts and curates factual knowledge into a permanent vector database, and writes in the author's voice — not by prompt injection, but by retrieving stored opinions, style, and curated facts from Qdrant at generation time. Every article is anchored to a human-written thesis the model cannot override. The pipeline runs unattended overnight. The editorial direction does not.

**Live output:** [lucasgraphic.com](https://lucasgraphic.com) — DATA / LAB / PORTFOLIO sections.

---

## Who This Is For

This is not a weekend project. It is not a demo. It is not something you clone, run `pip install`, and get articles from.

NonSequitur was built for and by someone who already runs a home lab — two or more machines on a local network, comfortable with Linux administration, Python debugging at the source level, and the kind of patience required when a service silently drops connections at 2am and you have to figure out why.

**You are probably the right person if:**

- You have a dedicated Windows machine with a high-end NVIDIA GPU (RTX 3090 minimum, RTX 4090/5090 recommended) running Ollama with 27B+ models at acceptable speed
- You have a separate always-on Linux server handling embedding, vector search, web crawling, and caching — because these services need to run 24/7 without competing for VRAM with the LLM
- You have self-hosted SearXNG before, understand why it breaks, and know how to fix it
- You are comfortable reading Python stack traces, editing config files, and adapting code written for one hardware configuration to yours
- You understand what a vector database is and why retrieval quality matters more than prompt engineering

**You are not the right person if:**

- You want a working system in an afternoon
- You have one machine and plan to run everything on it (possible, but painful — the architecture assumes network separation)
- You expect the persona system to work out of the box (it won't — you have to build it yourself from your own writing)
- You want a UI (there isn't one — it's a terminal application)

This is a tool built by one person for their own use, published because the architecture decisions might be useful to others. The code is not polished for external consumption. There is no installer, no Docker Compose that actually works end-to-end, no support channel.

---

## What It Actually Does

The system is a fully autonomous content pipeline with a human in the editorial loop — not the production loop. Here is what happens without manual intervention:

1. **Discovers** candidate topics from SearXNG, Reddit, and Google News — filtered by domain trust tier, scored by relevance, deduplicated
2. **Researches** each topic: generates targeted search queries, fetches full-text content via Chromium crawler, chunks and embeds into Qdrant with hybrid dense+sparse vectors
3. **Extracts knowledge** using an LLM to distill key facts from research into a permanent, curated knowledge base — facts that persist across article cycles and improve future generation quality
4. **Suggests a schema** — BM25-based analysis of research chunks selects the most appropriate article structure from 12 predefined schemas
5. **Picks a focus angle** — 20 LLM-generated thesis angles drawn from the top research chunks, human-selected before generation
6. **Validates the focus** — BM25 + LLM check: is the focus falsifiable, declarative, and supported by research?
7. **Generates** 800–2400 word articles with enforced thesis, persona voice injection, and neural reranking of research context
8. **Scores** the result — 8-phase scoring pass with quality signals, disqualifiers, and a concrete prescription for improvement
9. **Optionally rewrites** with Claude Sonnet for editorial polish

The result: one person running a content operation that would typically require a research team, with full editorial control and zero cloud dependency in the core pipeline.

---

## Architecture

```
Discovery ──► Research ──► Schema ──► Focus ──► Generate ──► Score ──► Claude Rewrite
   │              │         Suggest    Picker       │           │          (optional)
SearXNG        Qdrant      BM25+LLM   BM25+LLM   Ollama     qwen3.6
Reddit         Crawl4AI               Validator   qwen3.6    27b
Google News    Reranker                           27b/122b   (think=True)
```

### Pipeline Stages

**1. Discovery** — SearXNG meta-search, Reddit, Google News. Results filtered through `domain_config.py` (trusted/blocked domain registry with per-category tier weights), scored, deduplicated, and presented in an interactive terminal selector.

**2. Deep Research** — generates 4–6 LLM search queries targeting the article's focus angle. Fetches full-text via Crawl4AI (Chromium), Playwright fallback, Trafilatura extraction layer. Text is chunked at section boundaries (markdown headings as hard splits), embedded with `qwen3-embedding:8b-q8_0` (4096 dims), and indexed into per-category Qdrant collections with hybrid dense+sparse (BM25/RRF) vectors. Blocked domains are filtered before fetch, not after.

**3. Knowledge Extraction** — after research, `qwen3.6:27b` reads each fetched URL and distills 3–6 factual paragraphs per source. Human reviews extracted facts (keep/delete/edit), approves, and they are embedded into a permanent `knowledge_{category}` collection. Research chunks are then wiped. Knowledge accumulates across article cycles — each new article benefits from everything curated before it.

**4. Schema Suggester** — BM25 pre-ranks all research chunks against each of the 12 article schemas. Top 12 chunks are passed to `qwen3.6:27b` to select the most appropriate schema. Schema determines section structure, opening rule, closing rule, and word count range. Schema is the strongest quality lever after research quality.

**5. Focus Picker** — before generation, presents 20 LLM-derived thesis angles from the top research chunks. Human selects, edits, or writes their own. Night run uses the stored focus directly.

**6. Focus Validator** — BM25 check (research coverage) + LLM check (is focus falsifiable, declarative, and narrow enough?). Flags weak focus before generation. Does not block pipeline — flags only. Note: BM25 underscores argumentative schemas (games_analysis, industry_analysis) where a strong thesis synthesizes from weak signals. A WEAK validator score does not predict article quality for argumentative schemas.

**7. Generate** — hybrid RAG retrieves from `research_{cat}`, `knowledge_{cat}`, `knowledge_evergreen`, and `persona_{name}` simultaneously. BAAI/bge-reranker-v2-m3 cross-encodes all candidates against the full query context. Top chunks enter `_build_prompt()`. Prompt separates persona voice, research facts, and thesis direction into distinct semantic blocks. The model cannot override the focus angle or invent facts not present in context.

**8. Scoring Pass** — `qwen3.6:27b` (think=True) scores the generated article on 8 phases: anchor ceiling, argument structure (A1–A4), disqualifiers (D1–D10), quality signals (Q1–Q8), genericity signals (G1–G4), voice consistency, confidence, and final calculation. Outputs a score/10, verdict (pass/weak/fail), and a PRESCRIPTION block with 3 concrete fixes. Scoring results cached in queue.json.

**9. Claude Rewrite** *(optional)* — Anthropic Claude Sonnet for editorial polish. Factual content and sourced claims are never altered.

---

## Stack

| Component | Technology | Host |
|-----------|-----------|------|
| LLM Generate | Ollama + qwen3.6:27b / qwen3.5:122b | Windows, RTX 5090 |
| LLM Embed | Ollama + qwen3-embedding:8b-q8_0 | Ubuntu, GTX 1080 |
| LLM Score | Ollama + qwen3.6:27b (think=True) | Windows, RTX 5090 |
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
| DEV | qwen2.5:7b | 7B | Fast iteration, testing, metadata |
| NORMAL | qwen3.6:27b | 27B dense | Daily production, scoring, schema suggest |
| NORMAL2 | qwen3.5:35b-a3b | 35B MoE | Generate variant (factual articles) |
| MAX | qwen3.5:122b | 122B MoE | Maximum quality, temperature 0.2 |
| Embed | qwen3-embedding:8b-q8_0 | 8B | 4096-dim dense vectors |

MoE models (35b-a3b, 122b) use `think=False` — enabling thinking on long prompts causes crashes. Dense models (27b) use `think=True` for scoring. `model_supports_thinking()` in `config.py` is the single source of truth.

---

## Article Schemas

12 schemas in `data/schemas/`, each defining section structure, opening rule, closing rule, and word count ranges by length tier (short/medium/long).

| Schema | Use case | Default length |
|--------|----------|---------------|
| games_analysis | Opinion/argument piece about a game or phenomenon | long |
| games_review | Full game review with verdict | long |
| games_announcement | New game announcement, limited facts | short |
| games_early_access | EA/preview coverage | medium |
| ai_technical | Deep-dive: model, system, or technique | medium |
| ai_news | AI announcement or release | medium |
| hardware_review | Hardware review with benchmarks | long |
| hardware_announcement | Pre-release hardware coverage | medium |
| software_review | Software/tool review | medium |
| industry_analysis | Trend piece with named winners/losers | long |
| entertainment_review | Film, series, or book review | medium |
| default | Generic fallback | medium |

Schema selection is the strongest quality lever after research quality. The same topic with games_analysis vs games_early_access schema can produce an 8.0 PASS vs a 5.3 FAIL — because the schema determines what the closing paragraph is allowed to argue.

---

## Why The Knowledge Base Changes Everything

Most RAG pipelines throw away research after each article. NonSequitur accumulates it.

After researching a topic, `qwen3.6:27b` reads every fetched source and distills the factual content into 3–6 dense paragraphs per URL. A human reviews each extraction — keeping what is accurate and useful, deleting what is not — and approves them into a permanent `knowledge_{category}` collection. The raw research chunks are then wiped.

The result: each new article about a topic the system has covered before starts with curated, high-quality context already in Qdrant. The model does not re-derive what Phoenix Corp is or when Unreal Engine 6 was announced — it already knows, and that knowledge came from human-reviewed sources, not a language model's training data.

Over time, `knowledge_games`, `knowledge_hardware`, and `knowledge_evergreen` become a dense, accurate, noise-free reference base. The gap between a first article on a topic and a tenth article grows wider with each cycle.

### Knowledge Architecture

```
Research chunks (temporary)
  └── qwen3.6:27b extraction
        └── Human review (keep / delete / edit)
              └── knowledge_{category}  ←  permanent, per topic_slug
              └── knowledge_evergreen   ←  permanent, cross-category

knowledge_{category}
  ├── topic_slug: "replaced"            ← game-specific facts
  ├── topic_slug: "unreal-engine-6"     ← engine-specific facts
  └── topic_slug: "doom-the-dark-ages"  ← etc.

knowledge_evergreen
  ├── category: "games"                 ← genre/industry context
  ├── category: "hardware"              ← GPU architecture, benchmarks
  └── category: "ai-data"              ← AI/ML concepts
```

### Extract Flow

```
research_games (525 chunks, 31 URLs)
  │
  ├── [auto-clean]  garbage removed (~150 chunks)
  │
  ├── [extract]  qwen3.6:27b reads each URL
  │     ├── URL 1: analogstickgaming.com  → 4 facts extracted
  │     ├── URL 2: thegamer.com           → 5 facts extracted
  │     ├── URL 3: quora.com              → BLOCKED before fetch
  │     └── ...
  │
  ├── [human review]  keep / delete / edit per fact
  │
  ├── [deduplicate]  cross-URL duplicate removal
  │
  └── knowledge_games: 6 clean chunks  ←  permanent
      research_games: wiped
```

---

## Persona System

This is the part most people will get wrong, skip, and then wonder why the output sounds like ChatGPT.

The persona system does not work from a system prompt or a description of how you write. It works from a Qdrant collection of chunks extracted from your actual writing — stored as 4096-dimensional vectors, retrieved at generation time, placed at the end of the prompt for recency bias. The model reads what you actually wrote, not what you think you write like.

**There is no persona builder yet.** The Persona Builder is listed under "In Development." Until it ships, you build the collection manually: write chunks in a text file, embed them via the Knowledge menu, push them into a `persona_{name}` collection in Qdrant. Tedious. Worth it.

Without a persona collection the pipeline still works — it generates competent, neutral editorial prose. It will pass scoring on argument and research quality. But voice-dependent quality signals (Q4–Q7) will be consistently absent, and the output will read like the model, not like you.

### What a Persona Chunk Looks Like

The collection is structured around dimensions — distinct aspects of how you think and write. Each dimension should have 3–6 chunks. The system samples one chunk per dimension at generation time for coverage, not similarity.

```
dimension: criticism_style
"I don't trust reviews that don't name what they expected and explain why
 the thing failed to deliver it. Disappointment without a benchmark is just
 a mood. Tell me what you thought you were getting and I'll tell you if your
 disappointment is earned."

dimension: hype_reaction
"The demo was impressive. So was every demo from this studio for the last
 eight years. Three of those games shipped broken and were patched into
 adequacy six months later. I'll be impressed when I'm playing the retail
 build, not before."

dimension: technical_depth
"When a company says 'proprietary AI upscaling' without naming the technique,
 they mean they licensed something or built something they don't want compared
 directly to DLSS or FSR. The vagueness is the signal."

dimension: humor
"The press release used the word 'revolutionary' four times and 'innovative'
 three times. The feature being described is a minimap."

dimension: worldview
"Most industries reward the appearance of progress. Gaming press rewards
 the appearance of enthusiasm. The overlap is large enough to park a stadium in."

dimension: argumentation
"The counterargument is always worth steelmanning before you dismiss it.
 If you can't make the opposing case stronger than its proponents do, you
 haven't understood it well enough to disagree with it."
```

These are illustrative examples. Your chunks should come from things you have actually written — blog posts, forum arguments, reviews, notes, anything where your real voice is on record. The model will find the register. Your job is to give it enough material to find.

### Persona Collection Structure

Each chunk in Qdrant carries:

```json
{
  "text": "...",
  "dimension": "criticism_style",
  "source": "manual",
  "author": "your_name"
}
```

Minimum viable persona: 6 dimensions, 3 chunks each = 18 chunks. The reference implementation (`persona_lukasz`) has 11 dimensions and 53 chunks, built over several sessions from actual published writing.

---

## Neural Reranker

After hybrid RAG retrieval, all candidate chunks pass through **BAAI/bge-reranker-v2-m3** — a cross-encoder model that scores each chunk against the full query context, not just vector similarity.

The reranker runs as a dedicated FastAPI service on Ubuntu/GTX 1080. It receives up to 100 raw candidates and returns a precision-ordered list. Top 35 enter the final context assembly; top 20 typically reach `_build_prompt()` after persona chunk allocation.

Embedding similarity is a blunt instrument. A chunk about "game engine performance" scores highly for almost any gaming query. The reranker demotes generic context and promotes chunks with direct factual relevance to the specific article angle. The result is a tighter research block — which directly improves output specificity.

---

## Vector Database

Qdrant stores all research, knowledge, and persona context as 4096-dimensional vectors across per-category collections — the full output of `qwen3-embedding:8b-q8_0`, not a compressed approximation.

**Collections:**

```
research_{category}     ← per-item research, temporary (~50–300 chunks)
knowledge_{category}    ← curated extracted facts, permanent, human-reviewed
knowledge_evergreen     ← cross-category permanent context
persona_{name}          ← author voice, opinions, and style chunks
```

**Retrieval per generation:**

```
generate(category=games, slug=replaced)
  → research_games        (scroll all + BM25 pre-rank → top 100, filter: item_id)
  → knowledge_games       (hybrid HNSW+sparse, top_k=7,  filter: topic_slug)
  → knowledge_evergreen   (hybrid HNSW+sparse, top_k=5,  filter: category)
  → persona_lukasz        (dense coverage sampling, 1 chunk per dimension)
  → semantic dedup        (cosine sim threshold 0.92, research chunks only)
  → reranker              (cross-encode pool → top 35)
  → _build_prompt()       (top 20 after persona allocation)
```

Hybrid retrieval fuses dense semantic vectors with sparse BM25 keyword vectors via RRF (Reciprocal Rank Fusion). Research collections use scroll + local BM25 pre-ranking for 100% coverage — HNSW with item_id post-filter returns only ~18% of chunks. HNSW retained for knowledge and persona collections where coverage filtering is not needed.

---

## Prompt Architecture

`_build_prompt()` in `pipeline/generate_run.py` separates context into distinct semantic blocks with a specific order chosen for recency bias — the LLM attends most strongly to content closest to the generation task:

```
=== YOUR PERSONA ===        ← identity, worldview (only if .md persona file exists)
=== BACKGROUND KNOWLEDGE === ← evergreen concepts, weaved in only where relevant
=== RESEARCH FACTS ===      ← RAG chunks from research + knowledge collections
=== AUTHOR VOICE ===        ← RAG chunks from persona_{name} (recency bias position)
=== ARTICLE DIRECTION ===   ← mandatory focus angle
=== ARTICLE STRUCTURE ===   ← schema sections, opening/closing rules, voice rule
TASK: write the article
```

**Hard constraints enforced in prompt:**
- Never invent benchmark numbers, version strings, or release dates not present in context
- Focus angle is mandatory — the model cannot substitute or dilute it
- No generic CTAs, no PR framing
- Banned phrases list: "raises questions", "it remains to be seen", "only time will tell", "begs the question", "stands as a testament", "it is worth noting", and others
- If source does not name the reviewer or critic, state the observation directly — never write "critics note" or "reviewers say"
- Voice rule: author register applies to every sentence of every section, not just opening and closing
- H2 headers with specific section angles from schema, not generic topic labels

`_build_prompt()` is critical infrastructure. Every change affects all output quality.

---

## Domain Trust System

`data/domains_trusted.json` — per-category trust tiers (`trusted`, `press`, `community`) with retrieval boost multipliers. 328 domains across 12 categories. Tier determines RAG score weighting and reranker priority.

**Boost scale:**
```
0.95 — official docs, standards, gov, science, CVE/NVD
0.90 — official blogs, vendor docs, strong institutions
0.85 — solid specialist press, independent trusted sources
0.78 — standard press, good portals
0.70 — useful community/wiki/forum
0.60 — high-noise community
0.50 — unknown/neutral
```

`data/domains_blocked.json` — 44 blocked domains, URL patterns, and title patterns. Applied at three levels: SearXNG result filter, fetch-time skip, and auto-clean during knowledge review. Blocked list wins over trusted list — a domain in both is treated as blocked.

Key blocked domains: `metacritic.com` (anonymous aggregate reviews), `quora.com` (user-generated, no named attribution), `youtube.com` / `youtu.be` (auto-transcripts), `reddit.com`, `twitter.com`, `opencritic.com`, `rottentomatoes.com`.

Both files are the single source of truth. No domains are hardcoded in application code.

---

## Scoring System

Every generated article is automatically scored by `qwen3.6:27b` (think=True) immediately after generation.

**8-phase scoring prompt:**
1. **Anchor** — ceiling score based on overall argument quality (1–10, typically 6–9)
2. **Argument structure** — A1 (opening claim), A2 (builds), A3 (closing implication), A4 (synthesized evidence)
3. **Disqualifiers** — D1–D10 hard failures (descriptive opening, anonymous attribution, hedge phrases, etc.)
4. **Quality signals** — Q1–Q8 positive markers (punchy hook, memorable line, original synthesis, dry humor, etc.)
5. **Genericity signals** — G1–G4 negative markers (boilerplate, vague language, generic praise)
6. **Voice** — V1 (consistency), V2 (n/a for single author)
7. **Confidence** — how certain is the scorer
8. **Calculation** — final score, verdict (pass/weak/fail), recommendation

**PRESCRIPTION block** — 3 concrete, actionable fixes referencing actual article content. Not generic advice.

Scoring results (score, verdict) cached in `queue.json` and displayed in queue list. Articles flagged WEAK or FAIL are marked for human review but pipeline continues.

---

## Content Quality

**Auto-clean** runs before any knowledge review and removes:
- GDPR/TCF consent walls and cookie tables
- Newsletter signup fragments and affiliate copy
- Discourse forum metadata and Reddit comment noise
- Author biography boilerplate
- Site navigation dumps and related-article lists
- JavaScript fragments and structured data leaks
- Wikipedia infobox tables and metadata headers
- Sponsored content markers
- Steam store pages and shopping pages

**Chunk quality:**
- Section-aware chunking — markdown headings act as hard boundaries, overlap never bleeds across sections
- Minimum chunk size enforced (80 chars)
- Language detection — non-English pages rejected before embedding
- Duplicate detection across knowledge collections (cross-encoder threshold 0.85)
- Semantic dedup before reranking (cosine similarity threshold 0.92)

---

## What Works

- [x] Full Discovery → Research → Extract → Generate → Score pipeline
- [x] Hybrid RAG: dense + sparse vectors (BM25/RRF fusion)
- [x] BAAI/bge-reranker-v2-m3 neural reranking
- [x] Permanent knowledge base with LLM extraction + human review
- [x] Knowledge retrieval by `topic_slug` and `category` at generation time
- [x] Persona injection via RAG (separate from research context, recency bias position)
- [x] Focus angle enforcement — model cannot override
- [x] Blocked domain filter at fetch time, discovery time, and auto-clean
- [x] Single-source domain config (trusted + blocked JSON files, 328 + 44 entries)
- [x] Section-aware chunking with heading boundary detection
- [x] Live slug autocomplete in queue
- [x] Claude Sonnet rewrite stage (optional)
- [x] Focus Picker — 20 LLM angles, human selects before generation
- [x] Focus Validator — BM25 + LLM check before generation
- [x] Schema Suggester — BM25 + LLM selects from 12 article schemas
- [x] Scoring Pass — 8-phase quality scoring with prescription
- [x] Auto-garbage detection before knowledge review
- [x] Night run — autonomous batch pipeline

## In Development

- [ ] Persona Builder — extract author voice from own writing samples into Qdrant
- [ ] `_finalize_item()` — unified discovery flow
- [ ] SearXNG engine tuning — deeper pagination, more sources
- [ ] Uber Research — iterative research with coverage scoring

---

## Performance

| Metric | Value |
|--------|-------|
| Research time (40 URLs) | ~5–6 min |
| Generate time (27b) | ~150–220s |
| Extract time per URL | ~15–25s |
| Article body | ~6000–12000 chars |
| RAG pool before reranking | 100 chunks |
| RAG top_k after reranking | 35 chunks |
| Prompt context (top chunks) | ~20 chunks |
| Embedding dimensions | 4096 |
| Valkey cache hit rate | ~30–50% on re-runs |

---

## Setup

Two machines minimum. One won't work well — not because it's impossible, but because the architecture assumes the embedding server, Qdrant, reranker, SearXNG, and Crawl4AI are always available over the network without competing for GPU memory with the main LLM. If you run everything on one machine, expect to fight VRAM pressure constantly.

**Machine 1 — Windows (LLM inference):**
- NVIDIA GPU with 24GB+ VRAM. RTX 3090 runs 27b models. RTX 4090/5090 runs 122b at acceptable speed.
- Ollama installed, models pulled: `qwen3.6:27b`, `qwen3.5:35b-a3b`, `qwen3.5:122b`, `qwen2.5:7b`
- Python 3.11+ with `pip install -r requirements.txt`
- Network access to Machine 2 services

**Machine 2 — Ubuntu (services):**
- Any NVIDIA GPU for embedding and reranking (GTX 1080 8GB is sufficient)
- Ollama with `qwen3-embedding:8b-q8_0`
- Qdrant running on port 6333
- BAAI/bge-reranker-v2-m3 FastAPI service on port 8766
- Crawl4AI FastAPI + Chromium on port 8777
- SearXNG on port 8080
- Playwright fallback service on port 8765
- Valkey (Redis-compatible) on port 6379

**Configuration:**

All service endpoints are configured via `.env` in the project root. Copy `.env.example` to `.env` and fill in your IP addresses:

```ini
# Ollama (Machine 1 — local)
OLLAMA_URL=http://127.0.0.1:11434
OLLAMA_MODEL_NORMAL=qwen3.6:27b
OLLAMA_MODEL_HEAVY=qwen3.5:122b
OLLAMA_MODEL_LIGHT=qwen3.5:4b

# Embeddings (Machine 2)
EMBED_URL=http://10.0.0.195:11434
EMBED_MODEL=qwen3-embedding:8b-q8_0

# Qdrant (Machine 2)
QDRANT_URL=http://10.0.0.195:6333

# Reranker (Machine 2)
RERANKER_URL=http://10.0.0.195:8766

# Crawl4AI (Machine 2)
CRAWL4AI_URL=http://10.0.0.195:8777

# SearXNG (Machine 2)
SEARXNG_URL=http://10.0.0.195:8080

# Valkey / Redis (Machine 2)
VALKEY_HOST=10.0.0.195
VALKEY_PORT=6379

# Paths (Windows)
BASE_DIR=C:\NonSequitur
OUTPUT_DIR=C:\NonSequitur\output
LOG_DIR=C:\NonSequitur\logs
```

Domain trust and block lists are in `data/domains_trusted.json` and `data/domains_blocked.json`. Persona collection in Qdrant — see Persona System section above.

**What you need to build yourself before first run:**
- Your `persona_{name}` Qdrant collection (see Persona System above — no shortcut here)
- SearXNG configured with your preferred engines (back up `settings.yml` before any changes)
- `config.py` pointed at your Machine 2 IP addresses

There is no step-by-step installation guide. The codebase assumes you can read it.

---

## Editorial Mission

NonSequitur covers topics the mainstream gaming and tech press ignores, undercovers, or sanitizes — contrarian angles, honest criticism, analysis without PR appeasement. Quality is measured by argument depth and coverage utility, not trending score or press ratio.

The platform writes what IGN, PC Gamer, and VentureBeat won't — because they optimise for a different audience, different advertisers, and different incentives. Every article is anchored to a thesis the author chose and the model cannot override. The pipeline automates the labour. The editorial direction remains human.

---

## Screenshots

### Main Menu
![Main menu](docs/splash_main_menu.png)

### Discovery
![Discovery](docs/discovery.png)

### Queue
![Queue](docs/queue.png)

### Research
![Research](docs/research.png)

### Knowledge Base
![Knowledge menu](docs/knowledge_menu.png)

### Extract — LLM Fact Review
![Extract](docs/extract.png)

### Focus Picker
![Focus picker](docs/focus_picker.png)

### Generate
![Generate](docs/generate.png)

---

## Author

**Łukasz Grochal** — photographer, web developer, AI art creator.
[lucasgraphic.com](https://lucasgraphic.com) · Norway

---

*Built entirely on self-hosted infrastructure. No cloud LLMs in the core pipeline. No subscriptions. No data leaving the local network except for the optional Claude rewrite stage.*
