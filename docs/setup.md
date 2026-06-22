# NonSequitur — Setup Guide

Two machines is the practical minimum. The architecture assumes:
- **Machine 1 (Windows)** — LLM inference via Ollama
- **Machine 2 (Ubuntu)** — embedding, vector DB, web crawling, caching, CMS

Running everything on one machine is possible but expect constant VRAM pressure. The embedding server, Qdrant, reranker, SearXNG, and Crawl4AI need to stay running 24/7 without competing with the main LLM for GPU memory.

---

## Machine 2 — Ubuntu (Services)

### 1. Ollama (embedding)

```bash
curl -fsSL https://ollama.com/install.sh | sh
ollama pull qwen3-embedding:8b-q8_0
```

By default Ollama only listens on localhost. To expose it on the network:

```bash
sudo systemctl edit ollama
```

Add under `[Service]`:
```ini
Environment="OLLAMA_HOST=0.0.0.0:11434"
```

```bash
sudo systemctl daemon-reload
sudo systemctl restart ollama
```

---

### 2. Qdrant

```bash
docker pull qdrant/qdrant
docker run -d --name qdrant \
  -p 6333:6333 \
  -v $(pwd)/qdrant_storage:/qdrant/storage \
  --restart unless-stopped \
  qdrant/qdrant
```

Verify: `curl http://localhost:6333/healthz`

---

### 3. BAAI Reranker Service

The reranker runs as a FastAPI service on port 8766.

```bash
pip install fastapi uvicorn sentence-transformers torch --break-system-packages
```

Create `/opt/reranker/reranker_service.py`:

```python
from fastapi import FastAPI
from sentence_transformers import CrossEncoder
from pydantic import BaseModel
from typing import List, Tuple

app = FastAPI()
model = CrossEncoder("BAAI/bge-reranker-v2-m3", device="cuda")

class RerankRequest(BaseModel):
    query: str
    passages: List[str]

@app.post("/rerank")
def rerank(req: RerankRequest):
    pairs = [[req.query, p] for p in req.passages]
    scores = model.predict(pairs).tolist()
    return {"scores": scores}
```

Create systemd unit `/etc/systemd/system/reranker.service`:

```ini
[Unit]
Description=BAAI Reranker Service
After=network.target

[Service]
User=YOUR_USER
WorkingDirectory=/opt/reranker
ExecStart=uvicorn reranker_service:app --host 0.0.0.0 --port 8766
Restart=always

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable reranker
sudo systemctl start reranker
```

---

### 4. Crawl4AI

```bash
pip install crawl4ai --break-system-packages
crawl4ai-setup  # installs Chromium
```

Create `/opt/crawl4ai/crawl4ai_service.py`:

```python
from fastapi import FastAPI
from crawl4ai import AsyncWebCrawler
from pydantic import BaseModel
import asyncio

app = FastAPI()

class FetchRequest(BaseModel):
    url: str
    timeout: int = 15

@app.post("/fetch")
async def fetch(req: FetchRequest):
    async with AsyncWebCrawler() as crawler:
        result = await crawler.arun(url=req.url, timeout=req.timeout)
        return {"text": result.markdown or "", "success": result.success}
```

Create systemd unit `/etc/systemd/system/crawl4ai.service`:

```ini
[Unit]
Description=Crawl4AI Service
After=network.target

[Service]
User=YOUR_USER
WorkingDirectory=/opt/crawl4ai
ExecStart=uvicorn crawl4ai_service:app --host 0.0.0.0 --port 8777
Restart=always

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable crawl4ai
sudo systemctl start crawl4ai
```

---

### 5. SearXNG

```bash
docker pull searxng/searxng
docker run -d --name searxng \
  -p 8080:8080 \
  -v $(pwd)/searxng:/etc/searxng \
  --restart unless-stopped \
  searxng/searxng
```

Edit `searxng/settings.yml` to enable your preferred engines. At minimum enable:
- `google`, `bing`, `duckduckgo` for general search
- `reddit` for community content
- `google news` for recent articles

Set `server.secret_key` to a random string. Set `search.formats: [html, json]` to enable JSON API.

---

### 6. Valkey (Redis-compatible cache)

```bash
docker pull valkey/valkey
docker run -d --name valkey \
  -p 6379:6379 \
  --restart unless-stopped \
  valkey/valkey
```

---

### 7. Playwright Fallback Service

```bash
pip install playwright fastapi uvicorn --break-system-packages
playwright install chromium
```

Create `/opt/playwright/playwright_service.py`:

```python
from fastapi import FastAPI
from playwright.async_api import async_playwright
from pydantic import BaseModel
import asyncio

app = FastAPI()

class FetchRequest(BaseModel):
    url: str
    timeout: int = 15000

@app.post("/fetch")
async def fetch(req: FetchRequest):
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page()
        try:
            await page.goto(req.url, timeout=req.timeout)
            content = await page.content()
            await browser.close()
            return {"html": content, "success": True}
        except Exception as e:
            await browser.close()
            return {"html": "", "success": False, "error": str(e)}
```

Create systemd unit `/etc/systemd/system/playwright.service` (same pattern as above, port 8765).

---

## Machine 1 — Windows (LLM Inference)

### 1. Ollama

Download from [ollama.com](https://ollama.com) and install.

Pull required models:

```powershell
ollama pull qwen3.6:27b
ollama pull qwen3.5:35b-a3b
ollama pull qwen2.5:7b
# Optional, high VRAM requirement:
ollama pull qwen3.5:122b
```

---

### 2. Python Environment

```powershell
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
```

---

### 3. Configuration

Copy `.env.example` to `.env` and fill in your Machine 2 IP:

```ini
OLLAMA_URL=http://127.0.0.1:11434

EMBED_URL=http://YOUR_MACHINE2_IP:11434
EMBED_MODEL=qwen3-embedding:8b-q8_0

QDRANT_URL=http://YOUR_MACHINE2_IP:6333
RERANKER_URL=http://YOUR_MACHINE2_IP:8766
CRAWL4AI_URL=http://YOUR_MACHINE2_IP:8777
SEARXNG_URL=http://YOUR_MACHINE2_IP:8080
PLAYWRIGHT_URL=http://YOUR_MACHINE2_IP:8765

VALKEY_HOST=YOUR_MACHINE2_IP
VALKEY_PORT=6379
```

---

## First Run Checklist

Before starting `python nonsequitur.py`, verify:

```powershell
# Check Qdrant
curl http://YOUR_MACHINE2_IP:6333/healthz

# Check reranker
curl http://YOUR_MACHINE2_IP:8766/docs

# Check Crawl4AI
curl http://YOUR_MACHINE2_IP:8777/docs

# Check SearXNG
curl "http://YOUR_MACHINE2_IP:8080/search?q=test&format=json"

# Check embedding Ollama
curl http://YOUR_MACHINE2_IP:11434/api/tags
```

All should return valid responses before starting the pipeline.

---

## Before First Article

**Persona collection:** NonSequitur writes in your voice, not a generic AI voice. Before generating articles, build a `persona_lukasz` (or your own name) collection in Qdrant. See [persona-system.md](persona-system.md) for how to do this.

**Domain lists:** `data/domains_trusted.json` and `data/domains_blocked.json` ship with defaults. Review and adapt to your content area — adding trusted sources in your niche improves research quality significantly.

**SearXNG engines:** Back up `settings.yml` before making changes. Enable engines relevant to your topics. Disable engines that are slow or return low-quality results for your use case.

---

## CMS Integration (Optional)

NonSequitur publishes directly to PayloadCMS via REST API. CMS setup is not covered here — see `config.py` for the relevant fields (`CMS_URL`, `CMS_API_KEY`). The pipeline works without CMS; articles are saved as markdown files in `output/`.
