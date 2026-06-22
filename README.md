# NonSequitur

An autonomous content pipeline for people who want to write about things the mainstream press ignores, undercovers, or sanitizes — and want the research done for them.

The system searches the web, reads sources, builds a permanent knowledge base, and generates articles in your voice. It runs overnight without supervision. You handle editorial direction; it handles the labour.

**Live output:** [lucasgraphic.com](https://lucasgraphic.com)

---

## What It Does

1. **Discovers** topics from SearXNG, Reddit, and Google News — filtered by domain trust, deduplicated
2. **Researches** each topic: targeted queries, full-text crawling, vector indexing into Qdrant
3. **Generates** articles with a human-chosen thesis angle and your personal voice injected via RAG
4. **Scores** the result — 8-phase editorial scoring with concrete improvement suggestions
5. **Optionally rewrites** via Claude, Gemini, Groq, DeepSeek, or local Ollama

The pipeline runs unattended overnight. Focus angles are chosen by you before generation. The model cannot override the editorial direction.

---

## Architecture

```
Discovery ──► Research ──► Suitability Gate ──► Schema ──► Focus ──► Generate ──► Score
   │              │                               Suggest    Picker       │           │
SearXNG        Qdrant                             BM25+LLM   BM25+LLM   Ollama     qwen3.6
Reddit         Crawl4AI                                      Validator   27b/122b   27b
Google News    Reranker                                                             think=True
                    └──► Uber Research (gap analysis + targeted search, if thin)
```

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
| Cache | Valkey (Redis-compatible) | Ubuntu |
| CMS | PayloadCMS 3.x + MongoDB | Ubuntu |
| Frontend | Next.js 15 + Tailwind CSS v4 | Ubuntu |

### Model Tiers

| Key | Model | Use |
|-----|-------|-----|
| DEV | qwen2.5:7b | Testing, metadata, fast iteration |
| NORMAL | qwen3.6:27b dense | Production generation, scoring, schema suggest |
| NORMAL2 | qwen3.5:35b-a3b MoE | Generate variant for factual articles |
| MAX | qwen3.5:122b MoE | Maximum quality, temperature 0.2 |
| Embed | qwen3-embedding:8b-q8_0 | 4096-dim dense vectors |

---

## Requirements

Two machines is the practical minimum. One machine is possible but expect VRAM contention — the embedding server, Qdrant, reranker, SearXNG, and Crawl4AI need to be available 24/7 alongside the main LLM.

**Machine 1 — Windows (LLM inference):**
- NVIDIA GPU, 24GB+ VRAM (RTX 3090 minimum for 27b models)
- Ollama with models pulled: `qwen3.6:27b`, `qwen3.5:35b-a3b`, `qwen2.5:7b`
- Python 3.11+

**Machine 2 — Ubuntu (services):**
- Any NVIDIA GPU for embedding and reranking (GTX 1080 8GB is sufficient)
- Qdrant, SearXNG, Crawl4AI, Valkey, BAAI reranker, Playwright service

See [docs/setup.md](docs/setup.md) for full installation steps.

---

## Quick Start

```bash
git clone https://github.com/LucasGraphic/nonsequitur
cd nonsequitur
pip install -r requirements.txt
cp .env.example .env
# Edit .env with your Machine 2 IP addresses
python nonsequitur.py
```

Before first run you need:
- Services running on Machine 2 (see [docs/setup.md](docs/setup.md))
- A `persona_{name}` collection in Qdrant (see [docs/persona-system.md](docs/persona-system.md))
- SearXNG configured with your preferred engines

---

## What Works

- [x] Full Discovery → Research → Generate → Score pipeline
- [x] Suitability gate — rejects topics with insufficient research before generation
- [x] Uber Research — gap analysis + targeted search for thin topics
- [x] Hybrid RAG: dense + sparse vectors (BM25/RRF fusion)
- [x] BAAI/bge-reranker-v2-m3 neural reranking
- [x] Permanent knowledge base with LLM extraction + human review
- [x] Persona injection via RAG (trigger-vector ranked, threshold filtered)
- [x] Persona Builder — AI-assisted chunk creation (Paste and Converse modes)
- [x] Dynamic persona list from Qdrant — add new personas without config changes
- [x] Focus angle enforcement — model cannot override
- [x] Schema Suggester — BM25 + LLM selects from 12 article schemas
- [x] Focus Picker — 20 LLM angles, human selects before generation
- [x] Focus Validator — BM25 + LLM check before generation
- [x] Scoring Pass — 8-phase quality scoring with prescription (EXCELLENT/STRONG/PASS/WEAK/FAIL)
- [x] Generation history — score/schema/length tracked per article, viewable in queue
- [x] Night run — autonomous batch pipeline
- [x] Clip — paste URL directly into research queue

## In Development

- [ ] Morning digest — score summary after night run
- [ ] Multi-article synthesis

---

## Documentation

- [docs/setup.md](docs/setup.md) — full installation guide (Ubuntu services + Windows)
- [docs/persona-system.md](docs/persona-system.md) — building and managing author voice
- [docs/rag-architecture.md](docs/rag-architecture.md) — retrieval, reranking, knowledge base
- [docs/schemas.md](docs/schemas.md) — article schemas and when to use each

---

## Performance

| Metric | Value |
|--------|-------|
| Research time (~40 URLs) | 5–6 min |
| Generate time (27b) | 150–220s |
| Article body | 6000–12000 chars |
| RAG pool before reranking | 100 chunks |
| RAG top-k after reranking | ~28 chunks |
| Embedding dimensions | 4096 |

---

## Editorial Mission

NonSequitur covers topics the mainstream gaming and tech press ignores, undercovers, or sanitizes. Quality is measured by argument depth and honest coverage — not trending score or press ratio.

Every article is anchored to a thesis the author chose. The pipeline automates the research. The editorial direction stays human.

---

## Author

**Łukasz Grochal** — photographer, web developer, AI art creator.
[lucasgraphic.com](https://lucasgraphic.com) · Norway

---

*Self-hosted infrastructure. No cloud LLMs in the core pipeline. No subscriptions.*
