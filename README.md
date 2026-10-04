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

* **Vector store:** Gemini **File Search Store** (Gemini's equivalent of an OpenAI Vector Store), created and populated 100% via API (`file_search_stores.upload_to_file_search_store`). Each article = one document with `article_id` / `url` metadata. The store is persistent, so unchanged articles stay indexed between daily runs. OpenAI Vector Store is supported via `AI_PROVIDER=openai`.
* **Chunking:** server-side white-space chunker, **512 tokens per chunk, 64 tokens overlap** (`CHUNK_SIZE_TOKENS` / `CHUNK_OVERLAP_TOKENS`). 512 is the maximum Gemini File Search accepts.
* **Why 512 / 64:** support articles are short step-by-step procedures under `##` headings. ~512 tokens (~350 words) usually fits one whole section, so retrieval returns complete instructions instead of fragments. The 64-token (~12%) overlap keeps context when a step crosses a chunk boundary. Every file starts with `# Title` + `Article URL: …`, so the model can cite the source.
* **Retrieval:** `generate_content` with the `file_search` tool (embedding search); no keyword hacks.
* **Logging:** each run logs `Added / Updated / Skipped`, plus **files and chunks embedded**. The APIs don't return per-document chunk counts, so chunks are computed with the same 512/64 window formula.

---

## 4. Daily Job Deployment & Public Logs

Runs daily at **02:00 UTC** on GitHub Actions (cron): build Docker image → run `main.py` → commit `data/sync_state.json` back so the next run only uploads the delta.

* **Live Daily Job Logs:** [GitHub Actions Workflow Runs](https://github.com/thaipeace/kb-delta-indexer/actions/workflows/daily_sync.yml)
* **Delta detection:** SHA-256 of the normalized Markdown + Zendesk `updated_at`. Updated articles: new version uploaded first, old document deleted after it is indexed. Failed uploads are retried on the next run (non-zero exit).

---

## 5. Sanity Test Verification

**Question:** *"How do I add a YouTube video?"* (Gemini + File Search Store, verbatim OptiBot system prompt)

![Sanity Test Result](docs/sanity_test_result.png)

---

## 6. Notes & Trade-offs

* **Links:** relative links (`/hc/...`) are rewritten to absolute `https://support.optisigns.com/...` so citations are clickable outside the Help Center. Headings, lists and code blocks are kept; scripts, styles, forms and comments are removed. Using the Zendesk API body means there is no nav/footer to strip.
* **Scope:** the whole public Help Center is ingested (~400 articles); set `MAX_ARTICLES` to cap it.
* **Cut for time:** no handling of articles removed upstream (they stay in the store), and no retry/backoff beyond the next daily run.
