# core/content_filter.py -- RAG garbage detection
# Extracted from pipeline/generate_run.py (S25)
# Import via: from core.content_filter import is_garbage

import re as _re


def _rag_is_garbage(text: str) -> bool:
    """Fast garbage check for RAG retrieval -- rejects low-quality chunks."""
    if not text or len(text) < 80:
        return True
    import re as _re
    t = text.lower()
    patterns = [
        r"function\s*\(\s*\)\s*\{",
        r"document\.cookie",
        r"addsize\(\[",
        r"^\s*//\s*addsize",
        r'"@type"\s*:\s*"person"',
        r'"author"\s*:\s*\{',
        r"when you buy through links",
        r"(use the|remember to use).{0,30}coupon code",
        r"earn a commission",
        r"syndication partners may earn",
        r"although he loves everything that.s hardware",
        r"he has a soft spot for (cpus|gpus|ram)",
        r"although his background is in legal",
        r"news.{0,20}world report.{0,20}lifewire",
        r"when not wr(iting|apping)",
        r"contributing (writer|editor) at",
        r"^[-\w/]+ - reply\b",
        r"reply\b.{0,20}\bas a westerner\b",
        r"the first value corresponds to the average frames per second",
        r"the first value corresponds to",
        r"1% low fps.*metric for measuring",
        r"by selecting premium components across the board.*review",
        r"tom.s hardware verdict.*the .{3,30} offers",
        r"omega seamaster",
        r"lenovo.{0,20}sells a bunch",
        r"save over.{0,30}on this.{0,30}gaming pc",
        r"score a big discount",
        r"get rtx power for less",
        r"007.*first light",
        r"first light.*io interactive",
        r"britannica\.com.*state and society",
        r"substantial social and economic disruption followed in china",
        r"the spread of this culture was also supported",
        r"confucianism was a leading philosophy",
        r"china is an east asian country.{0,30}situated",
        r"making up around one.fifth of the world.s economy",
        r"the country was unstable and fragmented during the warlord",
        r"chinahighlights\.com",
        r"worldatlas\.com.*(maps?|geography|country|located|situated)",
        # Author bio patterns (not caught by existing rules)
        r"has been an? (editor|writer|contributor) (for|at|with)",
        r"is a staff (writer|editor|reporter) (for|at|with)",
        r"is a (freelance|senior|junior|associate) (writer|editor|reporter)",
        r"joined \w+ in 20\d\d (as|and)",
        r"previously (wrote|worked|covered) (for|at)",
        r"you can (follow|find|reach) (her|him|them) on",
        # Navigation / login leaks
        r"sign in to your \w+ account",
        r"log in (to|with) your \w+ account",
        r"create (a free )?account to",
        r"already have an account\?",
        # Tracking pixels and ad fragments
        r"t\.co/\d+/i/adsct",
        r"bci=\d+&dv=",
        r"!\[image\s*\d*\]\(https?://t\.co",
        r"!\[image\s*\d*\]\(https?://.*\.(gif|png|jpg)\?",
        # Author byline boilerplate
        r"published on \w+ \d+,? \d{4}",
        r"^news(\.){0,3}by \w+ \w+ contributor",
        r"\bcontributor\b.{0,30}\bpublished on\b",
        # YouTube / trailer noise
        r"watch on youtube",
        r"official (launch|gameplay|reveal|cinematic) trailer",
        r"- official (launch|gameplay|reveal|cinematic)",
        # Pure markdown image lines with no text content
        r"^\s*!\[.*?\]\(https?://",
        r"theworldfactbook\.org",
        r"great leap forward.*cultural revolution",
        r"mao zedong|mao died in 197",
        r"(accept all cookies|cookie consent|privacy policy).{0,100}$",
        r"hey y.?all",
        r"first time encountering this tactic",
        r"first game (ever |against a real)",
        r"obviously a blunder",
        # SaaS / marketing CTA
        r"request a demo",
        r"click below and (let|ask)",
        r"power up (lead generation|your)",
        r"let ai summaris?e and analys?e this post",
        r"chatgptperplexity",
        r"sign up for (free|our newsletter) (today|now|below)",
        r"book (a|your) (free |demo )?(call|session|consultation)",
        r"schedule (a|your) (free )?(demo|call|consultation)",
        r"start (your )?(free )?trial",
        r"get (started|access) (for free|today|now)",
        r"tags\s+(ai agents?|autonomous ai)",
        r"tl;dr\s*/\s*summary\s+most b",
        r"in this guide.{0,30}we will discover",
        r"the practical framework for",
        r"industries\s+\*\s+saas",
        # Author role / masthead boilerplate
        r"(founder|editor.in.chief|managing editor).{0,60}(bring|brings|with) over \d+",
        r"as (the |this site.s )?(founder|editor.in.chief|ceo|cto)",
        r"i bring over \d+ years? of experience",
        r"my expertise (spans?|covers?|includes?)",
        # Social share button blocks
        r"share\s+copy\s+url\s*(email|print|whatsapp)",
        r"(copy url|copylink|copy link).{0,30}(whatsapp|facebook|twitter|email)",
        r"^(share|copy url|email|print|whatsapp|facebook|linkedin|reddit){3,}",
        # Affiliate / commission disclaimers
        r"may receive a commission if you (purchase|buy)",
        r"(receives?|earn).{0,20}commission.{0,20}(purchase|buy|click|link)",
        r"affiliate (link|disclosure|commission).{0,60}(purchase|buy|click)",
        # Related / recommended articles nav
        r"(related|recommended|more) (articles?|stories?|news|posts?)\s*[:\*]",
        r"^\s*(next|previous) (article|post|story)\s*[:\-]",
        r"also read\s*[:\-]",
        r"see also\s*[:\-]",
        # arXiv boilerplate
        r"report issue for preceding element",
        r"submitted to arxiv on",
        r"(accepted|presented) at (neurips|icml|iclr|cvpr|emnlp|acl|iccv)",
        r"anonymous authors?,? paper under",
        r"preprint\. under review",
        r"arxiv:\d{4}\.\d{4,5}",
        r"©\s*\d{4}\s+the authors?\.",
        # Wikipedia / wiki structure
        r"this (article|page|section) is a stub",
        r"\[\s*edit\s*(this page|section)?\s*\]",
        r"\[\s*view history\s*\]",
        r"coordinates\s*:\s*\d+°",
        r"retrieved (from|on) .{0,40}wikipedia",
        r"wikimedia (foundation|commons)",
        r"this (list|article) is incomplete",
        # Hacker News UI
        r"\d+\s+point[s]?\s+by\s+\w+\s+\d+\s+(hour|day|minute)[s]?\s+ago",
        r"^\s*ask hn\s*:",
        r"^\s*show hn\s*:",
        r"\|\s*hide\s*\|\s*past\s*\|\s*favorite",
        r"\d+\s+comment[s]?\s*\|\s*(flag|hide|past)",
        # Medium / Substack boilerplate
        r"member.only story",
        r"\d+\s+min\s+read",
        r"clap\s+\d+",
        r"\d+\s+response[s]?",
        r"follow(ing)? to never miss",
        r"get (unlimited )?access (to )?all stories",
        r"already a member\? sign in",
        # GitHub noise
        r"star\s+\d+\s+fork\s+\d+",
        r"\d+\s+commit[s]?\s+\d+\s+branch",
        r"(watch|fork|star)\s+notifications?\s+fork",
        r"(issues|pull requests|actions|projects|wiki|security|insights)\s+\d+",
        r"clone (with https|using github cli|with ssh)",
        r"use git or checkout with svn",
        # Paywall / subscription fragments
        r"(subscribe|upgrade) (to|for) (premium|pro|full access)",
        r"you.ve reached your (free )?\d+ article",
        r"(this|read) (content|article|story) (is |for )?behind a paywall",
        r"unlock (this |full |unlimited )?access",
        r"(register|sign up) (for free )?to (read|continue|access)",
        r"subscribe (now|today) (to|for) (unlimited|full|exclusive)",
        # TOC / navigation blocks
        r"^#{1,3}\s+jump to section",
        r"^\*\s+jump to section",
        r"^[-\*]\s+(what (is|are)|how (to|does)|why (is|does)).{5,60}$",
        # FAQ boilerplate
        r"^#{1,3}\s+frequently asked questions",
        r"^\d+\.\s+#{1,3}\s+\d+\.",
        # Hacker News thread navigation
        r"parent\s*\|\s*prev\s*\|\s*next",
        r"\[\[.{1,5}\]\]\(javascript:void",
        # Spec/comparison tables with no prose
        r"^\|\s*(area|feature|spec|model|price|cost|context|speed|tokens?)\s*\|",
        # Datestamp-only fragments
        r"^last verified:\s*\*\*",
        r"^last updated:\s*\*\*",
        # "Fast Verdict" / routing decision headers with no argument
        r"^#{1,3}\s+fast verdict",
        # Social media aggregator / tweet thread fragments (digg.com, similar)
        r"^\d{6,}\s+\w",                          # numeric tweet ID + text fragment
        r"\|\s*\n\s*[A-Z][a-z]+\s+\w+@\w+",  # pipe separator + @handle
        r"Ask Question\s+No Digg Deeper",            # Digg UI boilerplate
        r"Pos\s+\d+\.\d+%\s+Neg\s+\d+\.\d+%",   # sentiment widget
        r"Views\s+-\s+Comments\s+-\s+Reposts",    # engagement stats widget
        r"Most Activity\s+Most ActivityTimeline",    # Digg timeline widget
        r"Expand post\s*\|",                        # Twitter/X expand button
        r"^Via\s*$",                                 # standalone "Via" link text
        r"VIEWS\d+\.?\d*[KM]BOOKMARKS\d+",       # compact engagement stats
        r"^#{1,3}\s+routing decision",
        r"^#{1,3}\s+quick (verdict|summary|take)",
    ]
    for pat in patterns:
        if _re.search(pat, t, _re.MULTILINE):
            return True

    # Navigation/menu garbage
    nav_patterns = [
        r"^\s*\*\s*(news|reviews|features|guides|videos|home|about)\s*$",
        r"skip to (main content|navigation|footer)",
        r"(follow|followed|like|share|subscribe)\s+\|",
        r"menu\s+(follow|home|news|reviews)",
        r"×\s+search\s+menu",
        r"\*\s+h\s+home\s+\*\s+n",
        r"open main menu",
        r"browse\s+\w+\.\w+\s+\*\s+news\s+\*\s+reviews",
        r"(log in|sign in|sign up)\s+(to|with)\s+(continue|facebook|google)",
        r"instagram (log in|sign up) close",
        r"never miss a post from",
        r"^(\s*[\*\-]\s+\w+){4,}\s*$",
        r"#\s+related answers section",
        # Twitter/X embedded thread navigation
        r"Expand post\s+\|\s+\w+@\w+",
        r"^\s*\|\s*$",                            # lone pipe separator lines
        r"stories\s*\*\s*github\s*\*\s*rankings",  # Digg nav footer
        r"\d+\s+upvotes\s+.\s+\d+\s+comments",
        r"^\s*\*\s*\*\s*\*\s*$",
        # Reddit UI fragments
        r"\w+\s+\*\s+\d+[dhmwy]\s+ago",
        r"^\s*(top|best|new|controversial|rising)\s+(comments?|posts?)\s*$",
        r"sorted by:\s*(top|best|new|controversial)",
        r"\d+\s+comment(s)?\s+share\s+save",
        r"view (all )?\d+ comments?",
        r"^\s*u/\w+\s+\*\s+",
        r"reddit\s+\d+\s+(point|upvote|comment)",
        r"(constrained|deleted|removed)\s+\w+\s+\*\s+\d+[dhmwy]\s+ago",
    ]
    for pat in nav_patterns:
        if _re.search(pat, t, _re.MULTILINE):
            return True

    return False


# Public alias
is_garbage = _rag_is_garbage
