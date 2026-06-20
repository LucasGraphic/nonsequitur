# config.py -- Article Agent central configuration
# Edit only this file -- everything else imports from here.

import os
from dotenv import load_dotenv

load_dotenv()

# -- Paths ------------------------------------------------------------------
BASE_DIR      = os.path.dirname(os.path.abspath(__file__))
QUEUE_FILE    = os.path.join(BASE_DIR, "queue.json")
OUTPUT_DIR    = os.path.join(BASE_DIR, "output")
PERSONAS_DIR  = os.path.join(BASE_DIR, "data", "personas")

# -- Ollama -----------------------------------------------------------------
OLLAMA_URL        = os.getenv("OLLAMA_URL", "http://localhost:11434")        # LLM generate -- Windows RTX 5090
OLLAMA_EMBED_URL  = os.getenv("EMBED_URL", "http://10.0.0.195:11434")      # bge-m3 embed + qwen2.5:7b -- Linux GTX 1080
VALKEY_URL        = "redis://" + os.getenv("VALKEY_HOST", "10.0.0.195") + ":" + os.getenv("VALKEY_PORT", "6379")      # Valkey cache -- Linux Ubuntu
# -- CRAWREL4AI
CRAWL4AI_URL = os.getenv("CRAWL4AI_URL", "http://10.0.0.195:8777")

# Models are loaded dynamically from Ollama at runtime via fetch_ollama_models().
# MODELS_FALLBACK is used only when Ollama is unreachable at import time.
MODELS_FALLBACK = {
    "qwen2.5:7b":      "qwen2.5:7b",
    "qwen3.6:27b":     "qwen3.6:27b",
    "qwen3.5:35b-a3b": "qwen3.5:35b-a3b",
    "qwen3.5:122b":    "qwen3.5:122b",
}

# Name fragments that identify non-generation models -- excluded from picker.
EMBED_FILTER = ("embedding", "embed", "rerank", "reranker")

DEFAULT_MODEL = "qwen3.5:122b"

# Model used for scoring pass.
# "" = use same model as generate (per-item).
# Set via [A] -> [3] Settings menu.
SCORING_MODEL = "qwen3.6:27b"


def fetch_ollama_models(ollama_url: str = None) -> dict:
    """
    Query Ollama /api/tags and return a dict of available generation models.
    Keys and values are both the model name string (e.g. "qwen3.6:27b").
    Filters out embedding/reranker models.
    Falls back to MODELS_FALLBACK if Ollama is unreachable.
    """
    import urllib.request
    import json as _json

    url = (ollama_url or OLLAMA_URL).rstrip("/") + "/api/tags"
    try:
        with urllib.request.urlopen(url, timeout=3) as resp:
            data = _json.loads(resp.read().decode())
        models = {}
        for m in data.get("models", []):
            name = m.get("name", "")
            if not name:
                continue
            low = name.lower()
            if any(f in low for f in EMBED_FILTER):
                continue
            models[name] = name
        return models if models else MODELS_FALLBACK
    except Exception:
        return MODELS_FALLBACK


# Populated at first use by nonsequitur.py or any menu that needs the list.
# Do not import this directly -- call fetch_ollama_models() instead.
MODELS: dict = {}

# Model used for metadata generation (titles, excerpts) -- always fast
META_MODEL = "qwen2.5:7b"

# -- Personas ---------------------------------------------------------------
# Replaces old STYLES system. Each persona has a .md file in data/personas/.
# Per queue item -- stored as item["persona"].
PERSONAS = {
    "lukasz":  {"label": "lucasgraphic.com voice -- personal, expert, direct"},
    "default": {"label": "neutral, factual, no personal voice"},
}
DEFAULT_PERSONA = "lukasz"

# -- API Providers ----------------------------------------------------------
# Keys via environment variables only -- never hardcode.
# Windows: setx ANTHROPIC_API_KEY "sk-ant-..."
API_PROVIDERS = {
    "claude": {
        "label":              "Claude Sonnet (Anthropic)",
        "model":              "claude-sonnet-4-6",
        "env_key":            "ANTHROPIC_API_KEY",
        "url":                "https://api.anthropic.com/v1/messages",
        "price_input_per_m":  3.00,
        "price_output_per_m": 15.00,
    },
    "openai": {
        "label":              "GPT-4o (OpenAI)",
        "model":              "gpt-4o",
        "env_key":            "OPENAI_API_KEY",
        "url":                "https://api.openai.com/v1/chat/completions",
        "price_input_per_m":  2.50,
        "price_output_per_m": 10.00,
    },
    "gemini": {
        "label":              "Gemini 2.0 Flash (Google)",
        "model":              "gemini-2.0-flash",
        "env_key":            "GEMINI_API_KEY",
        "url":                "https://generativelanguage.googleapis.com/v1beta/models",
        "price_input_per_m":  0.10,
        "price_output_per_m": 0.40,
    },
    "deepseek": {
        "label":              "DeepSeek V3",
        "model":              "deepseek-chat",
        "env_key":            "DEEPSEEK_API_KEY",
        "url":                "https://api.deepseek.com/v1/chat/completions",
        "price_input_per_m":  0.27,
        "price_output_per_m": 1.10,
    },
}
DEFAULT_API_PROVIDER = "claude"

# -- SearXNG ----------------------------------------------------------------
SEARXNG_URL        = os.getenv("SEARXNG_URL", "http://10.0.0.195:8080")
SEARXNG_PAGES      = 4
SEARXNG_PAGES_DEEP = 6

# -- Fetch Service (Playwright -- Ubuntu) -----------------------------------
FETCH_SERVICE_URL  = os.getenv("FETCH_SERVICE_URL", "http://10.0.0.195:8765/fetch")

# -- Reranker Service (sentence-transformers FastAPI -- Ubuntu) --------------
# BAAI/bge-reranker-v2-m3 via reranker_service.py
# Start: ~/reranker-venv/bin/python3 ~/reranker_service.py
RERANKER_URL       = os.getenv("RERANKER_URL", "http://10.0.0.195:8766")
RERANKER_TOP_N     = 35   # keep top N chunks after reranking (from top-100 Qdrant results)
RERANKER_FETCH_N   = 100  # fetch this many from Qdrant before reranking

# -- Discovery -------------------------------------------------------------
DISCOVERY_MAX       = 200
DISCOVERY_TOP_N     = 100
DISCOVERY_TOP_N_MAX = 200

# -- Deep Research ----------------------------------------------------------
RESEARCH_FETCH_MAX     = 40
RESEARCH_CHUNK_SIZE    = 2500
RESEARCH_CHUNK_OVERLAP = 250

# -- Qdrant -----------------------------------------------------------------
QDRANT_URL  = os.getenv("QDRANT_URL", "http://10.0.0.195:6333")

# -- Embedding configuration ------------------------------------------------
# EMBED_MODEL must match a model available on OLLAMA_EMBED_URL server.
# EMBED_DIM must match the model's output dimension.
# Changing these requires wiping all Qdrant collections (use [A]->[2] in agent).
#
# Available models on 10.0.0.195:
#   bge-m3:latest              dim=1024  fast  (current)
#   qwen3-embedding:8b         dim=4096  5x slower on GTX 1080
#   qwen3-embedding:8b-q8_0   dim=4096  5x slower on GTX 1080
#
EMBED_MODEL = os.getenv("EMBED_MODEL", "qwen3-embedding:8b")
EMBED_DIM   = 4096

# Collection name helpers -- use these everywhere instead of f-strings
def research_collection(category: str) -> str:
    cat = category.lower().strip()
    if cat not in RESEARCH_CATEGORIES:
        cat = "other"
    return f"research_{cat}"

def knowledge_collection(category: str) -> str:
    return f"knowledge_{category.lower().strip()}"

def persona_collection(persona: str) -> str:
    return f"persona_{persona.lower().strip()}"

QDRANT_COLLECTIONS = {"content": "content"}

# -- Site sections ----------------------------------------------------------
SECTIONS = {
    "1": "DATA",
    "2": "LAB",
    "3": "PORTFOLIO",
}
DEFAULT_SECTION = "DATA"

RESEARCH_CATEGORIES_DATA = [
    "games", "ai-data", "hardware", "software", "security", "entertainment", "other"
]

RESEARCH_CATEGORIES_PORTFOLIO = [
    "photography", "drone", "portrait-studio", "macro",
    "portrait-outdoor", "product", "travel",
    "3d", "3d-exterior", "3d-interior", "ai",
]

RESEARCH_CATEGORIES = RESEARCH_CATEGORIES_DATA + RESEARCH_CATEGORIES_PORTFOLIO + ["other"]

# -- Article length (words) per section ------------------------------------
ARTICLE_LENGTH = {
    "data":      "1200 to 1800",
    "lab":       "300 to 500",
    "portfolio": "150 to 350",
}

# -- LLM generation settings -----------------------------------------------
LLM_THINKING    = False

# Scoring pass think mode.
# True  = qwen3 uses <think> chain before scoring -- slower (~3x) but more accurate.
# False = direct 4-line output, fast.
# NUM_PREDICT must cover think chain + 4-line output.
SCORING_THINK       = True         # set True for calibration runs
SCORING_NUM_PREDICT = 8000          # think=False: 1200 is enough

def model_supports_thinking(model: str) -> bool:
    # MoE models crash with think=True on long prompts (scoring, long context).
    # Dense models (27b) handle thinking correctly.
    # Use this everywhere instead of hardcoding think=True/False per call site.
    MOE_PATTERNS = ("35b-a3b", "122b")
    return not any(p in model for p in MOE_PATTERNS)

                                    # think=True:  set to 6000
LLM_TEMPERATURE = 0.4

# Per-model temperature overrides.
# Takes priority over LLM_TEMPERATURE for listed models.
# Models NOT listed fall back to LLM_TEMPERATURE.
LLM_TEMPERATURE_BY_MODEL = {
    "qwen3.5:122b":    0.2,   # TODO: test 0.2 and 0.6
    "qwen3.6:27b":     0.4,
    "qwen3.5:35b-a3b": 0.4,
    "qwen2.5:7b":      0.4,
}

# -- RAG retrieval settings ------------------------------------------------
RAG_SCORE_MIN = 0.25  # unused -- _min_score filter removed, reranker handles quality

# -- Persona RAG settings ---------------------------------------------------
PERSONA_COLLECTION = "persona_schopenhauer"  # single unified collection (session 17+)
PERSONA_THRESHOLD  = 0.25  # min trigger_sim to include persona chunk
PERSONA_MAX        = 7     # max persona chunks -- one per dimension (8 dimensions)
PERSONA_MIN        = 2     # guaranteed minimum regardless of score
PERSONA_TRIGGER_W  = 1.0   # pure trigger match -- chunk_score excluded from persona selection

PERSONA_DIMENSIONS = {
    # 7 dimensions -- each maps to a distinct rhetorical move.
    # tooltip       : one-line description shown in builder UI
    # example_trigger: canonical trigger for this dimension
    "argument": {
        "tooltip":         "Questioning the dominant framing -- both sides have part of the truth",
        "example_trigger": "when a transition is mistaken for a replacement",
    },
    "critique": {
        "tooltip":         "Exposing the mechanism by which a system fails or protects itself",
        "example_trigger": "when institutional incentives corrupt the product",
    },
    "skepticism": {
        "tooltip":         "Claim exceeds evidence -- hype, benchmarks, demos vs reality",
        "example_trigger": "when product demos substitute for product reality",
    },
    "reference": {
        "tooltip":         "An older work or pattern describes exactly what is happening now",
        "example_trigger": "when the warning was ignored because it arrived as fiction",
    },
    "appreciation": {
        "tooltip":         "Something overlooked deserves recognition -- niche wins, underdogs",
        "example_trigger": "when a small studio punches above its weight",
    },
    "humor": {
        "tooltip":         "Comic register is the right analytical tool -- wit, irony, absurdism",
        "example_trigger": "when the critique requires the thing being critiqued",
    },
    "personal": {
        "tooltip":         "Speaking from direct experience, not from analytical distance",
        "example_trigger": "when a purchase is actually a thesis",
    },
}

# -- Knowledge base settings -----------------------------------------------
KNOWLEDGE_TRUST_MIN = 0.80

KNOWLEDGE_EXPIRY_DAYS = {
    "games":         365,
    "ai-data":       180,
    "hardware":      365,
    "software":      365,
    "security":      180,
    "entertainment": 365,
    "photography":   0,
    "drone":         0,
    "3d":            0,
    "ai":            0,
    "other":         365,
}