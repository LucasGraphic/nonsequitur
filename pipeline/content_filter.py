# pipeline/content_filter.py -- Unified content quality filter
# Single source of truth for garbage detection.
# Used by both research_run.py (indexing) and generate_run.py (RAG retrieval).
#
# To add new patterns: edit ONLY this file.

import re


# -- Pre-compiled pattern groups --------------------------------------------

_JS_CODE = [
    re.compile(r"function\s*\(\s*\)\s*\{"),
    re.compile(r"document\.cookie|window\.location"),
    re.compile(r"addsize\(\["),
    re.compile(r'"@type"\s*:\s*"person"'),
    re.compile(r'"author"\s*:\s*\{'),
    re.compile(r"^\s*\(function"),
    re.compile(r"^\s*//\s*addsize"),
]

_AFFILIATE = [
    re.compile(r"when you buy through links"),
    re.compile(r"(use the|remember to use).{0,30}coupon code"),
    re.compile(r"earn a commission|syndication partners may earn"),
]

_AUTHOR_BIOS = [
    re.compile(r"although he loves everything that.s hardware"),
    re.compile(r"he has a soft spot for (cpus|gpus|ram)"),
    re.compile(r"although his background is in legal"),
    re.compile(r"news.{0,20}world report.{0,20}lifewire"),
    re.compile(r"when not wr(iting|apping)"),
    re.compile(r"contributing (writer|editor) at"),
]

_FORUM_NOISE = [
    re.compile(r"^[-\w/]+ - reply\b"),
    re.compile(r"reply\b.{0,20}\bas a westerner\b"),
    re.compile(r"hey y.?all"),
    re.compile(r"first time encountering this tactic"),
    re.compile(r"first game (ever |against a real)|obviously a blunder"),
]

_BENCHMARK_BOILERPLATE = [
    re.compile(r"the first value corresponds to the average frames per second"),
    re.compile(r"the first value corresponds to"),
    re.compile(r"1% low fps.*metric for measuring"),
    re.compile(r"by selecting premium components across the board.*review"),
    re.compile(r"tom.s hardware verdict.*the .{3,30} offers"),
]

_OFF_TOPIC = [
    re.compile(r"omega seamaster|007.*first light|first light.*io interactive"),
    re.compile(r"lenovo.{0,20}sells a bunch"),
    re.compile(r"save over.{0,30}on this.{0,30}gaming pc|score a big discount"),
    re.compile(r"get rtx power for less"),
    re.compile(r"substantial social and economic disruption followed in china"),
    re.compile(r"the spread of this culture was also supported"),
    re.compile(r"confucianism was a leading philosophy"),
    re.compile(r"china is an east asian country.{0,30}situated"),
    re.compile(r"making up around one.fifth of the world.s economy"),
    re.compile(r"the country was unstable and fragmented during the warlord"),
    re.compile(r"chinahighlights\.com"),
    re.compile(r"theworldfactbook\.org"),
    re.compile(r"great leap forward.*cultural revolution"),
    re.compile(r"mao zedong|mao died in 197"),
    re.compile(r"britannica\.com.*state and society"),
]

_FACEBOOK = [
    re.compile(r"facebook\.com/login"),
    re.compile(r"\[log in\].*facebook"),
    re.compile(r"facebook\.com/hashtag/"),
    re.compile(r"title:\s*\d+[km]? views\s*.\s*[\d.,]+[km]? reactions"),
    re.compile(r"\[\d+[hd]\]\(https://www\.facebook\.com"),
    re.compile(r"facebook\.com/sharer"),
]

_STEAM = [
    re.compile(r"view screenshots\s+artwork"),
    re.compile(r"open this page in the steam app"),
    re.compile(r"wishlist.*follow.*purchase", re.DOTALL),
    re.compile(r"\d+\s+in group chat"),
    re.compile(r"award\s*\)\s*\d+"),
    re.compile(r"store page\s+\d+ in group chat"),
    re.compile(r"view\s+store page.*awards?", re.DOTALL),
    re.compile(r"scummy christ|💖 view screenshots"),
    re.compile(r"(players from different countries|languages).{0,60}award"),
]

_NAVIGATION = [
    re.compile(r"home\s*/\s*tech news\s*/\s*featured"),
    re.compile(r"nextstay cc settings"),
    re.compile(r"title:\s*just a moment"),
    re.compile(r"url source:\s*https://www\.resetera\.com"),
    re.compile(r"level up your gaming news"),
    re.compile(r"global-esports\.news is your place"),
    re.compile(r"sign up for our newsletter.*written by"),
    re.compile(r"gaminghq(blog|games)"),
    re.compile(r"login\s+get exitlag"),
    re.compile(r"diablo iv.*arpg.*diablo iii.*arpg"),
    re.compile(r"^\s*\*\s*(news|reviews|features|guides|videos|home|about)\s*$", re.MULTILINE),
    re.compile(r"skip to (main content|navigation|footer)"),
    re.compile(r"(follow|followed|like|share|subscribe)\s+\|"),
    re.compile(r"menu\s+(follow|home|news|reviews)"),
    re.compile(r"×\s+search\s+menu"),
    re.compile(r"\*\s+h\s+home\s+\*\s+n"),
    re.compile(r"open main menu"),
    re.compile(r"browse\s+\w+\.\w+\s+\*\s+news\s+\*\s+reviews"),
    re.compile(r"(log in|sign in|sign up)\s+(to|with)\s+(continue|facebook|google)"),
    re.compile(r"instagram (log in|sign up) close"),
    re.compile(r"never miss a post from"),
    re.compile(r"^(\s*[\*\-]\s+\w+){4,}\s*$", re.MULTILINE),
    re.compile(r"#\s+related answers section"),
    re.compile(r"\d+\s+upvotes\s+.\s+\d+\s+comments"),
    re.compile(r"^\s*\*\s*\*\s*\*\s*$", re.MULTILINE),
]

_SAAS_CTA = [
    re.compile(r"request a demo"),
    re.compile(r"click below and (let|ask)"),
    re.compile(r"power up (lead generation|your)"),
    re.compile(r"let ai summaris.e and analys.e this post"),
    re.compile(r"chatgptperplexity"),
    re.compile(r"sign up for (free|our newsletter) (today|now|below)"),
    re.compile(r"book (a|your) (free |demo )?(call|session|consultation)"),
    re.compile(r"schedule (a|your) (free )?(demo|call|consultation)"),
    re.compile(r"start (your )?(free )?trial"),
    re.compile(r"get (started|access) (for free|today|now)"),
    re.compile(r"tags\s+(ai agents?|autonomous ai)"),
    re.compile(r"tl;dr\s*/\s*summary\s+most b"),
    re.compile(r"in this guide.{0,30}we will discover"),
    re.compile(r"the practical framework for"),
    re.compile(r"industries\s+\*\s+saas"),
]

_WIKIPEDIA_JUNK = [
    re.compile(r"\[\^]\(https://en\.wikipedia\.org/wiki/"),
]

# All pattern groups combined for fast iteration
_ALL_PATTERN_GROUPS = [
    _JS_CODE,
    _AFFILIATE,
    _AUTHOR_BIOS,
    _FORUM_NOISE,
    _BENCHMARK_BOILERPLATE,
    _OFF_TOPIC,
    _FACEBOOK,
    _NAVIGATION,
    _SAAS_CTA,
    _WIKIPEDIA_JUNK,
    _STEAM,
]

_PDF_MARKERS = ["endobj", "endstream", "/Type /Page", "obj <<", ">> endobj"]


# -- Main filter function ---------------------------------------------------

def is_garbage(text: str) -> bool:
    """
    Returns True if text should be rejected -- not indexed or not used in RAG.
    Single source of truth for both research_run.py and generate_run.py.

    To add new patterns: add to the appropriate group above and re-deploy.
    """
    if not text or len(text) < 50:
        return True

    # PDF binary
    if text.lstrip().startswith("%PDF"):
        return True
    if sum(1 for m in _PDF_MARKERS if m in text) >= 3:
        return True

    # High ratio of non-printable chars
    printable = len(re.findall(r"[a-zA-Z0-9 \n\t.,;:!?'\"-]", text))
    if printable / max(len(text), 1) < 0.5:
        return True

    # Mostly numeric
    words = text.split()
    if len(words) > 20:
        numeric = sum(1 for w in words if re.match(r"^[\d.]+$", w))
        if numeric / len(words) > 0.7:
            return True

    t = text.lower()

    # JS startswith checks (can't pre-compile these)
    if t.lstrip().startswith("(function"):
        return True

    # Cookie consent (length-gated)
    if re.search(r"(accept all cookies|cookie consent)", t) and len(text) < 300:
        return True
    if re.search(r"(accept all cookies|cookie consent|privacy policy).{0,100}$", t) and len(text) < 300:
        return True

    # Just a moment (Cloudflare, length-gated)
    if re.search(r"just a moment\.\.\.", t) and len(text) < 200:
        return True

    # Wikipedia junk (length-gated)
    if re.search(r"\[\^]\(https://en\.wikipedia\.org/wiki/", t) and len(text) < 400:
        return True

    # worldatlas -- only off-topic geography content
    if re.search(r"worldatlas\.com", t) and re.search(r"(maps?|geography|country|located|situated)", t):
        return True

    # Pattern groups
    for group in _ALL_PATTERN_GROUPS:
        for pat in group:
            if pat.search(t):
                return True

    return False

