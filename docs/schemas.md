# NonSequitur — Article Schemas

Schemas define the structure of generated articles: section names, opening rule, closing rule, and word count ranges. Schema selection is the strongest quality lever after research quality — the same research on the same topic can produce a 5.3 or an 8.0 depending on schema choice.

The Schema Suggester (BM25 + LLM) recommends a schema after research. Trust it over manual override unless you have a specific reason.

---

## Schema Reference

### games_analysis
**Game analysis / opinion piece**
Default length: long

Use for: argumentative pieces about a game's design, mechanics, or cultural significance. Requires a falsifiable thesis — not "this game explores X" but "this game proves X at the expense of Y."

Sections: The argument → Evidence → Counter-view → Broader context → Conclusion

Opening rule: First sentence states the specific argument being made — not background, not context.
Closing rule: Where the argument lands and why it matters beyond this specific game.

---

### games_announcement
**Game announcement / reveal**
Default length: short

Use for: new game announcements, release date reveals, platform expansions. Factual coverage with editorial take in the final section.

Sections: What was announced → Key details → Platform/availability → Unconfirmed facts → Our take

Opening rule: First sentence states what was announced and why it matters now.
Closing rule: One-sentence verdict on whether this announcement changes anything.

---

### games_early_access
**Early access / game preview**
Default length: medium

Use for: coverage of games currently in early access. Judge current state, not promised state.

Sections: Current state → Core loop → Technical state → Roadmap reality check → Buy now or wait

Opening rule: First sentence states whether the current build is worth buying now.
Closing rule: Honest assessment of whether the developer can deliver on the roadmap.

---

### games_review
**Game review**
Default length: long

Use for: reviews of released games. Verdict-driven.

Sections: Verdict upfront → What works → What doesn't → Who it's for → Final score context

Opening rule: First sentence is the verdict — score and one-line justification.
Closing rule: Who should play this and why, in concrete terms.

---

### ai_news
**AI news / announcement**
Default length: medium

Use for: model releases, research papers, company announcements in AI space.

Sections: What happened → Technical details → Practical impact → Competitive context → Verdict

Opening rule: First sentence states what changed and why it matters.
Closing rule: What this means for developers or users in practical terms.

---

### ai_technical
**AI technical analysis**
Default length: long

Use for: deep dives into AI techniques, architectures, training approaches.

Sections: The claim → How it works → Evidence → Limitations → Implications

Opening rule: First sentence states the technical claim being examined.
Closing rule: Whether the technique delivers on its promise and what that means for the field.

---

### software_review
**Software / tool review**
Default length: medium

Use for: developer tools, applications, libraries.

Sections: What it is → Core functionality → Strengths → Weaknesses → Who should use it

Opening rule: First sentence states what the software does and for whom.
Closing rule: Concrete recommendation — use it, avoid it, or wait for a specific version.

---

### industry_analysis
**Industry / market analysis**
Default length: long

Use for: trends, market shifts, business analysis in gaming or tech.

Sections: The shift → Evidence → Who benefits → Who loses → What comes next

Opening rule: First sentence names the specific industry change being analyzed.
Closing rule: Concrete prediction or implication, not a summary.

---

### hardware_review
**Hardware review**
Default length: medium

Use for: GPU, CPU, peripherals, consumer hardware.

Sections: Specs and positioning → Performance → Value proposition → Who it's for → Verdict

Opening rule: First sentence states the hardware's market position and target buyer.
Closing rule: Buy recommendation with specific alternatives named.

---

### explainer
**Explainer / how-it-works**
Default length: medium

Use for: explaining concepts, technologies, or systems to an informed but non-specialist reader.

Sections: What it is → Why it matters → How it works → Where it falls short → The bottom line

Opening rule: First sentence states what is being explained and why it matters now.
Closing rule: One concrete takeaway — what should the reader do or think differently after reading this.

---

### opinion
**Opinion / commentary**
Default length: medium

Use for: takes on industry events, cultural commentary, editorial positions.

Sections: The position → Why now → The case for → The case against → Where this lands

Opening rule: First sentence states the opinion directly — not "some argue" but "X is wrong because Y."
Closing rule: The implication — if the argument is right, what follows?

---

### default
**General purpose**
Default length: medium

Fallback schema when no other fits. Minimal structural constraints. Use sparingly — schema-specific schemas produce measurably better results.

---

## Choosing a Schema

The Schema Suggester is right most of the time. Override it when:

- The suggester picks `games_announcement` for a topic you want to argue about → use `games_analysis`
- The suggester picks `ai_news` for a technical deep dive → use `ai_technical`
- The research is about an unreleased game being discussed analytically → `games_analysis` not `games_early_access`

The biggest quality difference is between **news schemas** (announcement, early_access) and **analysis schemas** (games_analysis, industry_analysis). News schemas produce structured factual coverage. Analysis schemas demand a falsifiable thesis and produce argumentative pieces. Choosing the wrong type is the most common schema mistake.

---

## Length Tiers

All schemas support three lengths. The schema definition sets a default; override with `[z]` in queue inspect.

| Length | Typical word range | Best for |
|--------|-------------------|---------|
| short | 800–1300 words | Quick takes, news items |
| medium | 1100–1800 words | Standard coverage |
| long | 1800–2400 words | Deep analysis, reviews |

Note: `short` with an argumentative schema often produces a truncated article that cannot land its conclusion properly. If using `games_analysis` or `industry_analysis`, prefer `medium` or `long`.
