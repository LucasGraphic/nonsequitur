# Changelog

All notable changes to NonSequitur are documented here.  
Format: [YYYY-MM-DD] with Added / Fixed / Changed / Removed sections.

# Changelog — Session 2 (2026-06-04)
 
## New Features
 
### `menus/knowledge/extract.py` — LLM Fact Extraction
New module for extracting factual knowledge from research chunks into permanent knowledge base.
- `qwen3.6:27b` extracts 3-6 fact paragraphs per URL (no thinking mode)
- Review UI: `k` keep all / `d N` delete / `e N` edit notepad / `s` skip / `q` quit
- Resume support — skips URLs already extracted in previous sessions
- Meta-response filter — discards model's "I found nothing" explanations
- Immediate upsert per URL — progress saved even if session interrupted
- New chunks land in `knowledge_{cat}` with `source: "extracted"`, `trust_score: 0.85`
### `menus/knowledge/domains.py` — Blocked Domains Browser
New top-level structure in Domains menu:
- `[1] Trusted` — existing category browser
- `[2] Blocked` — new browser with add/remove/search/pagination
- Auto-migration from flat list to dict format on load
### `pipeline/discovery_run.py` — Add to Trusted on URL Input
When adding seed URLs, unverified domains now offer three options:
- `[y]` add URL only (previous behavior)
- `[t]` add URL + interactively add domain to `domains_trusted.json`
- `[n]` skip URL
## Bug Fixes
 
### `menus/knowledge/browse.py`
- Fixed: after `d N` delete, view now refreshes instead of returning to slug list
- Points list is now a mutable local copy to prevent side effects
### `pipeline/research_run.py`
- Fixed: `topic_slug` now saved to Qdrant payload — required for knowledge RAG retrieval
- Fixed: blocked domain check added before seed URL fetch and main fetch loop
### `menus/knowledge/chunk_utils.py`
- Fixed: `_DOMAIN_BLACKLIST` replaced by `domain_config.is_blocked()` — single source of truth
- Fixed: `re.DOTALL | re.MULTILINE` flags added to `_garbage_label` for multi-line patterns
## Improvements
 
### Section-aware chunking (`pipeline/research_run.py`)
- `_chunk()` now splits on markdown headings (`#`, `##`, `###`) as hard boundaries
- Overlap stays within a section, never bleeds across headings
- Heading preserved at top of first chunk in each section for RAG context
- Result: fewer garbage overlap chunks, cleaner section boundaries
### New garbage patterns (`menus/knowledge/chunk_utils.py`)
- `WIKI_HEADER` — Crawl4AI metadata headers from Wikipedia
- `WIKI_INFOBOX` — Wikipedia markdown infobox tables
- `NAV_MENU` — site navigation menus (Xbox store, wccftech nav)
- `ARTICLE_STUB` — article header with author+date but no content
- `SIDEBAR` — popular discussions / active readers dumps
- `SPONSORED` — sponsored content markers
- 3x additional `GDPR` patterns (choices, store/access, withdraw consent)
### Blocked domains architecture
- `domains_blocked.json` is now the single source of truth for all blocking
- Used by: `sources.py` (SearXNG filter), `research_run.py` (fetch filter), `chunk_utils.py` (auto-clean)
- `url_patterns` and `title_patterns` fields active and supported by `domain_config.py`
- New blocked: 15 domains + 2 URL patterns (`store.steampowered.com`, `steampowered.com/app/`)
## Architecture Notes
 
### Knowledge workflow established
```
Research -> Auto-clean -> Extract (LLM) -> Wipe research -> Generate
```
- Research is temporary — wipe after extract
- Knowledge accumulates per `topic_slug`
- RAG retrieves from both `research_{cat}` and `knowledge_{cat}` simultaneously
- Verified working: `knowledge_games [replaced]: +6 chunks` in RAG output
### Known limitation
- Extract `slug` fallback uses `data["topic"]` instead of queue slug when `topic_slug` is empty in research chunks. Fix: pass slug explicitly from queue to extract session (TODO #2).
- 
## [2026-06-04]

### Added
- `review.py` — unified URL detail view with full chunk text always visible
- `review.py` — `k -1 -3` syntax: promote all except selected chunks
- `review.py` — immediate execute: each decision applied instantly, no batch confirm
- `review.py` — garbage auto-flagging on URL entry
- `chunk_utils.py` — `RELATED_NAV` pattern (related articles lists)
- `chunk_utils.py` — `GDPR` pattern (iabgpp, "preferences apply to this website")
- `queue.py` — slug autocomplete from `slugs.json` + Qdrant, numbered suggestions
- `queue.py` — tooltip for `[l]` slug edit
- `queue.py` — `[g]` on done item: auto-reset to researched + regenerate without manual `[r]`
- `knowledge_chunker.py` — auto-save confirmed slug to `data/slugs.json`
- `data/slugs.json` — added `mina-the-hollower`, `unreal-engine-6`

### Removed
- `review.py` — old bulk view commands (`[N] enter URL`, `[k N]`, `[x N-M]`, `[go]`)
- `review.py` — old chunk detail commands (`[d N-M]`, `[df N]`, `[dc]`, `[da]`, `[u N]`, `[e N]`, `[v N]`)
 
### Fixed
- `article_lang` UI in queue edit — `[y]` option added to `_inspect_edit_item`
- Duplicate `elif cmd == "z":` block removed from `menus/queue.py`
- `pipeline/research_run.py` — FFFD replacement chars cleaned, progress bar fixed, section dividers normalized
- Empty lines normalized in `research_run.py` (1918 → 945 lines)
- Domains menu — implicit search: any text that's not a command treated as search query
- Domains menu — `s <query>` inline search without extra prompt
- `review.py` — removed old two-mode flow (bulk view + detail view)
- `review.py` — mojibake cleanup (unicode escapes replaced with ASCII)
- `research_run.py` — seed URLs were truncated at 8000 chars (techspot/engadget lost ~70% of content)
- `data/slugs.json` — BOM fix (PowerShell Set-Content added UTF-8 BOM, json.load failed silently)
- `queue.py` — wrong `run_generate` call signature when regenerating from done status

### Changed
- `│ Lang` field added to queue item detail view
- `config.py` — `RESEARCH_CHUNK_SIZE` 800→1200
- `config.py` — `RESEARCH_CHUNK_OVERLAP` 100→150
- `research_run.py` — overlap implemented in `_chunk()` (was declared but never applied)
- `research_run.py` — fetch cap 8000→25000 chars

## [2026-06-03]

### Added
- **`menus/knowledge/` package** — refactored monolithic `knowledge.py` (1788 lines) into 9 focused modules: `qdrant_ops`, `chunk_utils`, `review`, `browse`, `feed`, `clean`, `menu`, `domains`. Public API unchanged — `agent.py` import requires no modification.
- **`[7] Domains` in Knowledge menu** — full UX redesign of `domains_trusted.json` editor. Categories numbered `[1-9]`, domains listed alphabetically with pagination (20/page), select by number for immediate edit. Commands: `r N` remove, `a` add with Tab-autocomplete, `s` search within category, `n`/`p` pagination. Global search via `[s]` from main view. Menu box shown only on entry and after returning from category — no redundant redraws.
- **`article_length` UI in queue edit** — `[z]` option in item inspect: choose short (1500-2500 chars) / medium (3500-5000 chars) / long (6000-9000 chars). Displayed in queue list as `[S]`/`[L]` badge and in item detail view. Night run reads from queue without prompting, defaults to `medium`.
- **Pass 3 — optional translation** — after body (pass 1) and titles/excerpts (pass 2), optionally translate body to Polish or Norwegian. Interactive `Translate? [pl/no/Enter=skip]` in manual runs; auto-translate in night run if `article_lang` queue flag set. Titles, excerpts, and slug remain English. New queue field: `article_lang` (en/pl/no, default en).

### Fixed
- **`_length_instr` scope bug in `_build_prompt()`** — `_length_map` and `_length_instr` were defined inside `if article_focus:` block, causing `NameError` when focus was empty. Moved outside the conditional — always in scope regardless of focus.
- **`readline` optional import** — `domains.py` used `import readline` which is unavailable on Windows. Now wrapped in `try/except` with graceful fallback to plain `input()`.
- **`removeprefix("www.")` in `chunk_utils.py` and `feed.py`** — consistent with global fix from session 4.

### Changed
- `pipeline/generate_run.py` — added `article_lang` field resolution; pass 3 translation block inserted before `_save_article`.
- `menus/queue.py` — `[z]` handler, length display in list and item detail.


## [2026-06-02]

### Added
- **Focus Picker** (`pipeline/focus_picker.py`) — interactive article angle selector
  - Triggers before `_build_prompt()` when `article_focus` is empty
  - Generates 20 angles from top 12000 chars of research context using loaded model
  - UI: numbered list with word-wrap, full text visible
  - Commands: `[1-20]` select, `[e N]` edit, `[0]` custom, `[s]` skip
  - Night run: skipped when `item["night_run"] = True`
 `_strip_html_comments()` in `generate_run.py` — strips `<!-- -->` from LLM output before parsing

 - **Focus Picker destination** in Knowledge Feed (`menus/knowledge.py`)
  - Feed Paste and Clip now ask: `[1] knowledge_{category}` or `[2] knowledge_evergreen`
  - Evergreen chunks: no slug required, category filter only in RAG
  - `_ask_slug_and_category()` returns `(category, slug, collection)` — three values
  - **Domain fallback** in `research_run.py` — empty `domain` field now populated from `page["url"]`
  - `_strip_html_comments()` placeholder rules in `_build_prompt()` — strengthened absolute ban
  - FORBIDDEN filler patterns in `_build_prompt()`:
  - `"It is a bold move."` / `"It is a risky move."` style sentences
  - Short sentence stacks (3+ consecutive sentences under 12 words)

### Changed
- **NORMAL model** switched from `qwen3.5:27b` → `qwen3.6:27b`
  - `think: false` works identically — no pipeline changes required
  - Generate time: ~65-80s (was ~79s), output quality improved
  - Benchmark: GPQA Diamond 87.8 vs 85.5, AIME 2026 94.1 vs 92.6
- `num_predict` raised from `2500` → `3200` — fixes truncated TITLES/EXCERPTS

### Fixed
- Duplicate `RESEARCH_CATEGORIES` definition removed from `config.py` — now single source of truth
- `research_collection()` validates against full category list including portfolio categories
- `article_focus` empty check now strips `'"'` characters — fixes `'""'` stored by queue editor
- `num_predict` raised from `3200` → `4500` — fixes missing TITLES/EXCERPTS on longer article

### Data
- `dexerto.com` added to `domains_trusted.json` / games — `press`, boost `0.75`
- `games.gg` and `game.gg` added to `domains_blocked.json`

### RAG garbage patterns added (`_rag_is_garbage()`)
- Tracking pixels: `t.co/adsct`, `bci=N&dv=`
- Image markdown fragments: `![Image N](https://t.co/...`
- Author bylines: `Published on`, `Contributor`
- YouTube trailer noise: `Watch on YouTube`, `Official Launch Trailer`
- Author bio variants: `has been an editor for`, `is a staff writer at`, `joined X in 20NN`
- Navigation leaks: `sign in to your X account`, `log in with your account`

---

## [2026-06-02] — Session 1

### Fixed
- `IsNullCondition` bug in RAG — replaced with `Filter(must=[slug match])`
- `knowledge_evergreen` retrieval — category filter only, no slug filter, `ev_top_k = top_k // 4`
- `top_k=20` passed explicitly to `_retrieve_context()`
- Article length target: `800-1200` words for 27b/35b (was 1500-2200)
- No-repetition rule added to `_build_prompt()`
- Queue commands `[4]`, `[5]`, `[6]` renamed to `[cd]`, `[re]`, `[rr]` — fixes numeric conflict


## [2026-06-01] — Knowledge Base Redesign

### Added
- `menus/knowledge.py` v2 — complete rewrite of knowledge base menu
- **Auto-clean before review** (mandatory) — runs before every Inspect & Promote session:
  - Removes GDPR/TCF consent walls, newsletter signups, author bios, Discourse forum comments, Reddit comments, affiliate disclaimers
  - Detects and removes duplicate URL crawls (chunk_idx gap > 50 = duplicate crawl)
  - Full report per label: `GDPR: 312  NEWSLETTER: 45  AUTHOR_BIO: 12  DUPLICATES: 89`
- **Pre-screening URL list** — before reviewing chunks, shows all URLs with garbage analysis:
  - `⚠ 11/13 garbage (84%)` — immediately visible which URLs are trash
  - Trust label per URL: `★ press`, `✓ trusted`, `~ community`, `? unknown`
- **Bulk decisions on URL list** — without entering chunk view:
  - `k N` / `e N` / `x N` / `s N` — mark URL for knowledge/evergreen/delete/skip
  - `x N-M` — delete URL range
  - `go` — execute all pending decisions
- **Auto-skip community/unknown URLs** — optional prompt at item start, skips all `~ community` and `? unknown` URLs automatically
- **topic_slug in payload** — every promoted chunk gets `topic_slug` from queue, enabling slug-based RAG retrieval
- **Slug edit at promote** — mandatory review of topic_slug before K/E confirm, editable inline
- **Browse knowledge [4]** — new menu option to browse knowledge collections per slug:
  - Lists all slugs with chunk counts and top domains
  - Per-slug chunk view with delete options
- Chunk commands: `d N-M` range, `df N` delete-from, `dc` forum/comments, `da` all garbage, `v N` preview single chunk
- `n` / `b` navigation (next/previous URL), `q` quit review at any point
- `xd` — delete all URLs from current domain in item

### Changed
- Menu simplified: 11 options → 6 options (`Stats`, `Feed`, `Inspect & Promote`, `Browse knowledge`, `Clean`, `Wipe`)
- `[10] Review candidates` and `[11] Score candidates` removed — candidates flow eliminated, direct research → knowledge promote
- `_clean_markdown()` applied automatically on every K/E promote — strips `[label](url)`, `[[N]]`, `**bold**`, headers, image refs
- Clean menu consolidated: auto-clean, deduplicate, stale removal, domain removal in one place

### Architecture
- Knowledge base philosophy clarified: `topic_slug` per technology/topic (e.g. `nvidia-dlss`, `grim-dawn`), not per article
- Slugs aggregate across multiple research runs — knowledge grows with each article on the same topic
- RAG in `generate_run.py` already filters by `topic_slug` via Qdrant `should` filter — pipeline fully connected end-to-end
- `knowledge_games` / `knowledge_evergreen` split: evergreen = studio history, mechanics, timeless facts; games = current reviews, patches, prices

## [2026-06-01]

### Fixed
- `_generate_ollama()` used `/api/generate` instead of `/api/chat` — qwen3.5 models return empty output with `/api/generate`. Switched to `/api/chat` with `think: false`
- `STATUS_ERROR` items showed only "Reset → pending" in `[r] redo` — now treated like `STATUS_RESEARCHED`, offering "Keep research, regenerate only" option
- CJK characters (Chinese/Japanese/Korean) appearing in search queries generated by qwen2.5:7b — added post-processing filter in `_generate_search_queries()`
- Infinite generation loop — added `num_predict=4096` to `_generate_ollama()` options

### Changed
- `_build_prompt()` style rules — 9 active constraints:
  - Forbidden: "proves that" (zero uses)
  - Forbidden sentence starters: "This [anything]" including "This is not/the/a/why/how"
  - Forbidden: `**bold**` as substitute for `## headers`
  - Forbidden: HTML comments and annotations
  - H2 headers: organic placement only — not evenly distributed
  - H2 headers: must contain a verb or specific claim
  - Conclusion: one analytical paragraph, not a list of observations
  - Never invent benchmark scores not present in research context
  - Forbidden starters apply inside `=== EXCERPTS ===` as well
- `CONCLUSION RULE` prompt text rewritten — shorter, less verbose to prevent model from quoting it verbatim
- `patch_prompt_reminders.py` bad reminder reverted — aggressive "MANDATORY FINAL CHECK" caused infinite generation loop

### Added
- `patch_generate_ollama_chat.py` — switches Ollama backend to `/api/chat`
- `patch_query_cjk_filter.py` — removes CJK queries before SearXNG fetch
- `patch_queue_redo_error.py` — STATUS_ERROR handling in `[r] redo`
- `patch_trusted_games.py` — 32 new gaming press domains in `domains_trusted.json`

### GitHub
- `nonsequitur` repo created at github.com/LucasGraphic/nonsequitur
- README.md, CHANGELOG.md, docs/ARCHITECTURE.md published
- Profile README updated with NonSequitur as active project

---

## [2026-05-30]

### Added
- `pipeline/content_filter.py` — unified garbage detection, single source of truth for both research indexing and RAG retrieval. Replaces duplicated `_is_garbage_text()` in `research_run.py` and `_rag_is_garbage()` in `generate_run.py`
- `data/slugs.json` — local slug registry, auto-populated after every successful generate
- Live char-by-char slug autocomplete in discovery UI via `msvcrt` (Windows) / `termios` (Linux) — suggestions appear after 2 characters without pressing Enter
- 10 LLM-generated topic name suggestions per article in URL and Query discovery modes
- Clean FOCUS input box across all three discovery modes (URL / Query / Upgrade) — no LLM suggestions, editorial thesis is always human-written
- Domain column in discovery selector — every result now shows source domain for evaluation before selecting
- Per-tier RAG minimum score thresholds — `press/trusted: 0.001`, `community: 0.010`, `unknown: 0.020`
- Blocked domain filter in `discovery/sources.py:fetch_all()` — domains from `domains_blocked.json` are now rejected at fetch time, before reaching Qdrant
- Qdrant chunk cleanup on `reset → pending` in queue inspect (`[r] redo → [2]`)
- Qdrant chunk cleanup on `[6] reset researched` in queue main menu
- `_append_slug_registry()` in `generate_run.py` — appends slug to `slugs.json` after every `✓ Saved`
- `_suggest_topics()` in `discovery_run.py` — LLM topic name generator with live picker
- `_ask_focus_simple()` in `discovery_run.py` — clean focus input without LLM angle suggestions

### Fixed
- `fetch_all` in `discovery/sources.py` was not applying `is_blocked()` filter — blocked domains (instagram.com, ttms.com, etc.) were entering Qdrant research collections
- URL mode in discovery asked for topic per URL before merge/separate decision — now asks merge/separate first, then topic once for merged items
- URL display in queue inspect truncated at 80 chars — increased to 120
- `reset → pending` did not clean Qdrant research chunks — old chunks from blocked domains persisted across re-runs
- `[6] reset researched` did not clean Qdrant chunks
- `_suggest_focus()` in discovery generated angles in Polish when source URL had Polish title — flow restructured so LLM receives English topic before generating suggestions
- `slug_autocomplete()` returned empty results when `knowledge_{category}` collection was empty — now also reads from `data/slugs.json` and `queue.json`
- SyntaxError in `_slug_live_input()` from escaped string literals — function rewritten cleanly

### Changed
- `research_run.py` and `generate_run.py` now import `is_garbage` from `pipeline/content_filter.py` — old function bodies left as dead code, safe to remove
- Prompt in `_build_prompt()`: removed ambiguous "topic as subject area" note that gave model license to deviate from focus angle
- Prompt: added rule "NEVER invent benchmark scores, version numbers, or model comparisons not present in research context"
- Discovery URL mode: merge/separate decision moved before topic questions
- Discovery URL mode: redundant "Custom topic name" prompt removed (TOPIC picker handles this)
- Quality score threshold in discovery: `QUALITY_THRESHOLD 0.4 → 0.25`

### Removed (domains_blocked.json)
- Added 27+ domains including: `youtube.com`, `twitter.com`, `x.com`, `instagram.com`, `dailymotion.com`, `twitch.tv`, `resetera.com`, `neogaf.com`, `steamcommunity.com`, `steamdb.info`, `backloggd.com`, `mobygames.com`, `netflix.com`, `eneba.com`, `megagames.com`, `ruh.ai`, `innobu.com`, `aimadetools.com`, `ampcome.com`, `theaicronicle.com`, `ttms.com`, `merriam-webster.com`, `dictionary.cambridge.org`, `vocabulary.com`

---

## [2026-05-29]

### Added
- HERETIC model (huihui-gpt-120b) installed on Windows inference server
- Dynamic model selection from Ollama `/api/tags` in discovery menu
- `[l] slug` in queue inspect — edit slug with auto-slugify
- `[x] remove` in queue inspect — cleans research chunks from Qdrant before removal
- `[r] redo` in queue inspect — checks if chunks exist before offering regeneration options
- Knowledge → Feed submenu with `clip.py` and `manual_feed.py`
- `SEARXNG_PAGES=4`, `SEARXNG_PAGES_DEEP=6` in config

### Fixed
- `seen_texts` dedup bug in RAG — duplicate chunks no longer entered context
- `lstrip("www.")` corrupting domain names (e.g. `wccftech.com` → `ccftech.com`) — replaced with `startswith()` check
- `sys.path.insert` in `discovery/sources.py` moved to top-level import
- `time_range` parameter removed from SearXNG fetch — was returning 0 results
- `_input()` wrapper in discovery — `q` now returns to menu instead of killing process
- UTF-8 encoding fix for Windows PowerShell in `agent.py`
- Auto-cleanup of research chunks after generate disabled — chunks now persist until item is removed from queue

### Changed
- Quality score in discovery rewritten — coverage/relevance/diversity instead of press ratio
- Refinement prompt — mainstream bias (IGN/GameSpot) removed
- `deep=False` → `deep=True` in Discovery fetch
- Persona/research split in generate — `context_persona` and `context_research` as separate blocks
- H2 headers — `every 2-3 paragraphs` instead of `only when topic changes`
- Focus enforcement — MANDATORY thesis, model cannot substitute angle
- `<!-- INTERNAL LINK -->` added to list of forbidden HTML comments
- SearXNG engines: added startpage, qwant; removed duckduckgo (CAPTCHA on page 2)
- Google engine: added `paging: true` in SearXNG `settings.yml`
- RSS sources removed from all categories in `data/source_config.json`
- Title examples (good vs bad) added to generate prompt

---

*NonSequitur is under active development. Breaking changes may occur between sessions.*
