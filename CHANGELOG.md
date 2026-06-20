# CHANGELOG — NonSequitur

Notable changes. Dates are session dates, not commit dates.
Format: Added / Fixed / Changed / Removed.

---

## [2026-06-20] S31

### Added
- Persona Builder: [T] now opens chunk text in Notepad for editing (fallback to inline on error)
- Scorer gradation: EXCELLENT (9.0+) and STRONG (8.0+) verdict tiers above PASS

### Fixed
- `menus/settings.py`: SyntaxWarning from invalid escape sequence `\s` in regex
- `requirements.txt`: missing `python-dotenv` dependency

---

## [2026-06-19] S30

### Added
- `persona_default` collection seeded with 14 neutral chunks (2 per dimension), safe for public use

### Fixed
- Research gate: persona chunks (src=persona) excluded from trusted chunk count — was causing false LOW TRUST flags
- Queue `[p]` persona selector: item persona field now wired to pipeline (was cosmetic only)

---

## [2026-06-18] S27–S29

### Added
- Persona Builder (`menus/knowledge/persona_builder.py`): AI-assisted chunk creation via Ollama
  - Paste mode: paste any text, model extracts chunk fields
  - Converse mode: model asks targeted question for weakest dimension, extracts from answer
  - `[S]` sync draft to Qdrant, `[N]` create new collection, `[M]` change model
- `persona_schopenhauer` collection: 58 chunks across 6/7 dimensions
- Persona architecture: 7 dimensions replacing old 11-dimension flat list
  (argument, critique, skepticism, reference, appreciation, humor, personal)
- Pure relevance ranking with trigger similarity threshold (0.25) replacing coverage sampling
- `focus_picker.py`: completeness validation, num_predict raised to 1800
- `focus_validator.py`: BM25 + LLM pre-generation focus check
- BM25 pre-ranking in schema suggester
- Scoring: `model_supports_thinking()` helper — MoE models (35b-a3b, 122b) use think=False
- Scoring: `SCORING_MODEL` setting override in Settings menu
- Queue: `[v]` view article command, `[p]` persona selector
- `domains_trusted.json`: 328 entries across 12 categories
- `domains_blocked.json`: 44 entries

### Fixed
- MoE models (35b-a3b, 122b) crashing with think=True on long scoring prompts
- Focus picker routing through dead code in queue.py — now correctly uses `pipeline/focus_picker.py`
- Dead code removed: `_load_persona()`, `persona_block`, `persona_text` from `generate_run.py`
- Author voice block moved to end of context for recency bias (was at top)

### Changed
- Persona retrieval: scroll() + local trigger_sim ranking replaces HNSW query_points
- Research retrieval: scroll() + BM25 pre-ranking for 100% chunk coverage (was 18% via HNSW)
- Scoring anchor is ceiling, not floor
- `PERSONA_TRIGGER_W`: 0.4 → 1.0 (pure trigger matching)
- `PERSONA_THRESHOLD`: 0.15 → 0.25

---

## [2026-06-16] S24–S26

### Added
- Scoring pass (`pipeline/scoring_pass.py`): 8-phase editorial scoring with PRESCRIPTION block
- Schema suggester: BM25 pre-ranking + LLM selection from 12 schemas
- `focus_validator.py` module (extracted from generate_run.py)
- Settings menu: scoring model, default model, scoring think on/off
- Queue list redesign: SCORE/SCHEMA/LEN columns
- Unicode fix: `fix_unicode_to_ascii.py` run across entire project (905,365 chars replaced)
- GEO infrastructure: `robots.txt`, `llms.txt` deployed to lucasgraphic.com

### Fixed
- `_rag_is_garbage()` extracted to `core/content_filter.py` — single source of truth
- RRF score incompatibility: per-source min-max normalization with priority weights
- Scoring: think=True confirmed as production default for dense models

---

## [2026-06-13] S19–S23

### Added
- RAG: scroll() + local BM25 pre-ranking for research chunks (100% coverage vs 18% HNSW)
- Extract dedup: cross-encoder reranker at threshold 0.85 (replaces cosine similarity)
- Persona system: single unified `persona_lukasz` collection replacing 6 separate collections
- Coverage-based persona sampling: one chunk per dimension via cosine similarity
- `persona_lukasz`: 53 chunks across 11 dimensions including 15 literary reference chunks
- Norway article project: "Norweski paradoks" completed in Polish, English, Norwegian

### Fixed
- Mojibake encoding: `fix_mojibake.py` (ftfy-based), run across all pipeline files

### Changed
- Chunk size: 2500/250 (size/overlap)
- Domain config: gaming press domains expanded

---

## [2026-06-01 to 2026-06-12] S1–S18

Initial development. Core pipeline established:
- Discovery → Research → Extract → Generate → Score flow
- Hybrid RAG: dense + sparse vectors (BM25/RRF)
- BAAI/bge-reranker-v2-m3 neural reranking service
- Permanent knowledge base with LLM extraction and human review
- Article schema system: 12 schemas in `data/schemas/`
- Night run: autonomous batch pipeline
- PayloadCMS + MongoDB CMS integration
- SearXNG self-hosted web search
- Crawl4AI + Playwright web fetching

---

*Breaking changes are noted explicitly. All other changes are additive or internal.*
