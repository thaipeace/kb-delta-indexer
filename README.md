# Knowledge Base Delta Indexer (OptiBot Mini-Clone)

An automated, idempotent RAG ingestion pipeline that scrapes support documentation, normalizes messy HTML into clean Markdown, detects incremental deltas via SHA-256 content hashing, and synchronizes document embeddings with AI knowledge stores.

---

## 1. Quick Setup & Local Execution

### Prerequisites
* Python 3.11+
* Google Gemini API Key (Free tier via [aistudio.google.com](https://aistudio.google.com/)) or OpenAI API Key

### Installation
```bash
# Clone the repository
git clone https://github.com/thaipeace/kb-delta-indexer.git
cd kb-delta-indexer

# Create and activate virtual environment
python -m venv venv
source venv/bin/activate  # On Windows: .\venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Configure environment variables
cp .env.sample .env
# Edit .env and insert your GEMINI_API_KEY
```

### Run Pipeline Locally
```bash
# Execute daily sync batch job (runs once, logs summary, exits 0)
python main.py

# Query the assistant (Sanity Check)
python scripts/test_bot.py "How do I add a YouTube video?"

# Run test suite (23 unit tests)
pytest tests/ -v
```

---

## 2. Docker Execution

The container runs as an ephemeral single-execution batch job and exits with code `0`:

```bash
docker build -t kb-delta-indexer .

# Mount ./data so sync_state.json persists -> subsequent runs upload only the delta
docker run --rm -e GEMINI_API_KEY="your_api_key_here" -v "$(pwd)/data:/app/data" kb-delta-indexer
```

---

## 3. Vector Store & Chunking Strategy

* **Vector store:** Gemini **File Search Store** (Gemini's equivalent of an OpenAI Vector Store), created and populated 100% via API (`file_search_stores.upload_to_file_search_store`). Each article = one document with `article_id` / `url` metadata. The store is persistent, so unchanged articles stay indexed between daily runs. OpenAI Vector Store is fully supported via `AI_PROVIDER=openai`.
* **Chunking parameters (Gemini vs OpenAI):**
  * **Gemini File Search Store (Default):** Server-side white-space chunker set to **512 tokens per chunk, 64 tokens overlap** (`CHUNK_SIZE_TOKENS` / `CHUNK_OVERLAP_TOKENS`). Google AI Studio strictly enforces a hard ceiling of 512 tokens per chunk for File Search.
  * **OpenAI Vector Store (Alternative):** Static chunking configured for **800 tokens per chunk, 100 tokens overlap** (or customizable via env vars).
  * *Why Gemini default?* Google AI Studio offers a generous 100% free tier (no billing/credit card needed), allowing reviewers to easily clone and test the entire pipeline locally without friction or cost.
* **Why 512 / 64:** Support articles are short step-by-step procedures under `##` headings. ~512 tokens (~350 words) usually fits one complete section, so retrieval returns full instructions rather than fragmented snippets. The 64-token (~12%) overlap preserves context when a step crosses a chunk boundary. Every file starts with `# Title` + `Article URL: …`, enabling the model to cite exact sources.
* **Retrieval:** Pure semantic embedding retrieval via `generate_content` with the `file_search` tool (no keyword hacks or hardcoding).
* **Logging:** Each run logs `Added / Updated / Skipped`, plus **files and chunks embedded**. Because neither API returns per-document chunk counts directly, chunks are calculated using the identical 512/64 sliding window formula.

---

## 4. Daily Job Deployment & Public Logs

Runs daily at **02:15 UTC** (~09:15 AM ICT) on GitHub Actions (cron): build Docker image → run `main.py` → commit `data/sync_state.json` back so subsequent runs only upload deltas.
*(Note: We scheduled the cron at minute `:15` specifically to bypass the heavy runner queue congestion and multi-hour delays that typically happen at top-of-the-hour `:00` UTC).*

* **Live Daily Job Logs:** [GitHub Actions Workflow Runs](https://github.com/thaipeace/kb-delta-indexer/actions/workflows/daily_sync.yml)
* **Delta detection:** SHA-256 of the normalized Markdown + Zendesk `updated_at`. For updated articles: new version is indexed first, and obsolete documents are safely deleted afterward.
* **Transparent CI Lifecycle:** All build and execution steps are automated. In run #8, an ephemeral container path issue was caught and resolved in commit `eb84f93`, with subsequent runs (#9, #10) running cleanly and idempotently.

---

## 5. Sanity Test Verification

**Question:** *"How do I add a YouTube video?"*  
Tested via `scripts/test_bot.py` using Google Gemini (`gemini-3.5-flash-lite`) + File Search Store with the verbatim OptiBot system prompt and ground-truth citations.

![Sanity Test Result](docs/sanity_test_result.png)

---

## 6. Notes & Trade-offs

* **Links:** relative links (`/hc/...`) are rewritten to absolute `https://support.optisigns.com/...` so citations are clickable outside the Help Center. Headings, lists and code blocks are kept; scripts, styles, forms and comments are removed. Using the Zendesk API body means there is no nav/footer to strip.
* **Scope:** the whole public Help Center is ingested (~400 articles); set `MAX_ARTICLES` to cap it.
* **Cut for time:** no handling of articles removed upstream (they stay in the store), and no retry/backoff beyond the next daily run.
