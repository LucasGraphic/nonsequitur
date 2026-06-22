# CHANGELOG — NonSequitur

Notable changes. Dates are session dates, not commit dates.
Format: Added / Fixed / Changed / Removed.

---

## [2026-06-22] S32

### Added
- `pipeline/suitability_gate.py` — research quality gate before generate; blocks night run on thin research, prompts interactively otherwise
- `pipeline/uber_research.py` — gap analysis + targeted search pass for thin topics; LLM generates 4 missing questions, searches and indexes new chunks per question; integrated into queue `[r] → [3]` and auto-triggered on THIN gate result
- Dynamic persona list in Discovery — reads `persona_*` collections live from Qdrant instead of hardcoded `config.PERSONAS`
- Generation history — score, verdict, schema, length, focus tracked per article in `queue.json`; `[v]` in queue inspect shows history table with best-generation marker and Notepad open by number
- `config.py`: `GATE_MIN_CHUNKS`, `GATE_MIN_AVG_SCORE`, `GATE_MIN_SOURCES`, `UBER_RESEARCH_MODEL`, `UBER_GAP_QUESTIONS`, `UBER_MAX_URLS_PER_Q`

### Fixed
- `_retrieve_context()`: `item.get('persona')` inside function where `item` was not in scope — replaced with `persona` parameter
- Scoring cache: `scoring_verdict` and `scoring_score` now always written to queue after generate (was only written on FAIL/WEAK)
- Uber Research: relevance filter raised to 3 keyword matches; BM25 URL pre-scoring before fetch; global `seen_urls` across all questions; gap prompt now explicitly uses article focus angle; query construction extracts keywords instead of sending full question sentences

### Changed
- Old `[research-gate]` block (char/source/trust heuristics, warn-only) replaced by `suitability_gate.py` (chunk count + reranker score, hard block on night run)
- `domains_blocked.json`: added `kinguin.net`, `yumpu.com`, `dokumen.pub`, `scribd.com`, `archive.org/stream`, `jotun.games` removed from blocked (gave valid chunks)
- Queue `[r]` on done items: added `[3] Uber Research` option alongside existing reset options

---

## [2026-06-20] S31

### Added
- Persona Builder: `[T]` now opens chunk text in Notepad for editing (fallback to inline on error)
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
- Pure relevance ranking with trigger similarity threshold (0.25)
- `focus_picker.py`: completeness validation, num_predict raised to 1800
- `focus_validator.py`: BM25 + LLM pre-generation focus check
- BM25 pre-ranking in schema suggester
- Scoring: `model_supports_thinking()` helper
- Queue: `[v]` view article command, `[p]` persona selector
- `domains_trusted.json`: 328 entries across 12 categories
- `domains_blocked.json`: 44 entries

### Fixed
- MoE models (35b-a3b, 122b) crashing with think=True on long scoring prompts
- Focus picker routing through dead code — now correctly uses `pipeline/focus_picker.py`
- Dead code removed: `_load_persona()`, `persona_block`, `persona_text` from `generate_run.py`
- Author voice block moved to end of context for recency bias

### Changed
- Persona retrieval: scroll() + local trigger_sim ranking replaces HNSW query_points
- Research retrieval: scroll() + BM25 pre-ranking for 100% chunk coverage (was 18% via HNSW)
- `PERSONA_TRIGGER_W`: 0.4 → 1.0 (pure trigger matching)
- `PERSONA_THRESHOLD`: 0.15 → 0.25

---

## [2026-06-16] S24–S26

### Added
- Scoring pass (`pipeline/scoring_pass.py`): 8-phase editorial scoring with PRESCRIPTION block
- Schema suggester: BM25 pre-ranking + LLM selection from 12 schemas
- `focus_validator.py` module
- Settings menu: scoring model, default model, scoring think on/off
- Queue list redesign: SCORE/SCHEMA/LEN columns
- Unicode fix: `fix_unicode_to_ascii.py` (905,365 chars replaced)

### Fixed
- `_rag_is_garbage()` extracted to `core/content_filter.py`
- RRF score incompatibility: per-source min-max normalization with priority weights

---

## [2026-06-13] S19–S23

### Added
- RAG: scroll() + local BM25 pre-ranking for research chunks (100% coverage vs 18% HNSW)
- Extract dedup: cross-encoder reranker at threshold 0.85
- Persona system: single unified `persona_lukasz` collection
- `persona_lukasz`: 53 chunks across 11 dimensions
- Norway article project: "Norweski paradoks" completed in Polish, English, Norwegian

### Fixed
- Mojibake encoding: `fix_mojibake.py` (ftfy-based)

### Changed
- Chunk size: 2500/250 (size/overlap)

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
