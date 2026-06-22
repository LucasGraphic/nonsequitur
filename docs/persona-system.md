# NonSequitur — Persona System

The persona system is what makes NonSequitur generate articles in your voice rather than generic AI prose. It works by storing examples of your writing and thinking as structured chunks in Qdrant, then retrieving the most relevant ones at generation time.

---

## How It Works

At generation time, NonSequitur retrieves up to 7 persona chunks from your `persona_{name}` collection and injects them into the prompt as an `=== AUTHOR VOICE ===` block. The model uses these as examples of how you argue, what you find interesting, and how you phrase things.

Chunks are ranked by **trigger similarity** — how closely a chunk matches the article's focus angle — not by a general style score. A chunk about skepticism toward AI hype will only appear in articles where that skepticism is relevant.

---

## Dimensions

Each chunk belongs to one of 7 dimensions:

| Dimension | What it captures |
|-----------|-----------------|
| `argument` | How you question dominant framing |
| `critique` | How you expose mechanisms of failure |
| `skepticism` | How you flag claims that exceed evidence |
| `reference` | Older work or ideas you connect to current events |
| `appreciation` | What you find genuinely well-done |
| `humor` | Your comic register — dry observation, irony |
| `personal` | Direct experience that informs your analysis |

A healthy persona collection has chunks in all 7 dimensions. `appreciation` and `personal` are typically the hardest to build — write about things you genuinely liked, or draw on direct experience.

---

## Building Your Persona

### Option 1: Persona Builder (recommended)

The Persona Builder is accessible from the main menu:

```
[K] Knowledge base → [F] Feeds → [P] → select persona → [3] Persona Builder
```

Two modes:

**Paste mode `[1]`** — paste any text you have written. The model extracts chunk fields (text, dimension, trigger, tags) from it. Works well with blog posts, forum replies, reviews, anything with your voice in it.

**Converse mode `[2]`** — the model identifies your weakest dimension and asks you a targeted question. Answer naturally, the model extracts a chunk from your reply. Good for filling gaps when you do not have source material.

After extraction, review the chunk, edit it in Notepad with `[T]`, and sync to Qdrant with `[S]`.

### Option 2: Manual JSON

Create a JSON file in `data/personas/` following this structure:

```json
[
  {
    "text": "The full chunk text — this is what appears in the article prompt.",
    "dimension": "skepticism",
    "trigger": "AI benchmark claims often measure narrow capability, not general usefulness.",
    "tags": ["ai", "benchmarks", "hype"]
  }
]
```

Then use Persona Builder `[S]` sync or the Knowledge menu to import it.

---

## Chunk Quality

The quality of individual chunks matters more than the quantity. Two precise chunks (threshold 0.25) outperform seven imprecise ones. When writing chunks:

- Write in first person, as if mid-argument
- Be specific — name the thing you are skeptical about, not "AI in general"
- Make the trigger a statement the model can match against article focus angles
- Avoid meta-commentary ("I tend to think...") — just the thought itself

Aim for 80-100 chunks across all 7 dimensions before relying on the system for production articles.

---

## Multiple Personas

NonSequitur supports multiple personas stored as separate `persona_{name}` collections in Qdrant. You can assign a persona per queue item using `[p]` in the queue inspect menu.

To create a new persona collection: Persona Builder → `[N]` create new collection.

The active default persona is set in `config.py` as `PERSONA_COLLECTION`. You can also change it in Settings `[A] → [6]`.

---

## Checking Your Persona

In the queue inspect menu, `[s] RAG sources` shows which persona chunks were used in the last generation, including their dimension and score. Use this to identify which dimensions are firing and which are not.

A chunk that never appears in context usually means its trigger does not match the topics you write about. Rewrite the trigger to be more general, or add chunks with triggers closer to your actual content areas.
