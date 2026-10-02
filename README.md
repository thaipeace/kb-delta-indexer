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

# Run test suite (20 unit tests)
pytest tests/ -v
```

---

## 2. Docker Execution

The container runs as an ephemeral single-execution batch job and exits with code `0`:

```bash
# Build Docker image
docker build -t kb-delta-indexer .

# Run container with environment variable
docker run --rm -e GEMINI_API_KEY="your_api_key_here" kb-delta-indexer
```

---

## 3. Chunking Strategy & Rationale

* **Chunk Size:** `800 tokens` (~3,200 characters). **Overlap:** `100 tokens` (~400 characters).
* **Algorithm:** Paragraph-aware recursive splitting (`_split_into_chunks` in `src/assistant.py`).  
  Each article is split on `\n\n` (paragraph boundaries) first; if a paragraph still exceeds the budget, a hard character split is applied. This keeps step-by-step procedural instructions intact within a single chunk.
* **Per-chunk upload:** Each chunk is written as an individual `.md` file and uploaded separately to the **Gemini File API**, so the model receives focused, size-controlled context windows rather than entire articles.
* **Retrieval model:** Gemini performs **in-context grounding** — chunk files are passed directly as references into `generate_content` at query time. This differs from dense vector embedding (no index is built), making it reliable for factual Q&A over structured support documentation where exact wording matters.
* **Rationale for 800 / 100:** Technical troubleshooting guides are step-oriented (`Step 1 → Step 2 → Step 3`). At 800 tokens, an entire multi-step procedure stays cohesive under its `##` header. A 100-token overlap preserves referential continuity across chunk boundaries without fragmenting actionable guidance.


---

## 4. Daily Job Deployment & Public Logs

The sync job runs automatically every day at **02:00 UTC** via GitHub Actions Scheduled Cron.

* **Live Daily Job Logs:** [GitHub Actions Workflow Runs](https://github.com/thaipeace/kb-delta-indexer/actions/workflows/daily_sync.yml)

---

## 5. Sanity Test Verification

**Question:** *"How do I add a YouTube video?"*  
**Prompt Enforced:** Max 5 bullet points, grounded strictly in uploaded documents, citing source URLs.

![Sanity Test Result](docs/sanity_test_result.png)
