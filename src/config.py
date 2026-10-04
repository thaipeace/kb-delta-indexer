"""Configuration and environment variables management."""
import os
from pathlib import Path
from dotenv import load_dotenv

# Base directory paths
BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
ARTICLES_DIR = DATA_DIR / "articles"
STATE_FILE = DATA_DIR / "sync_state.json"
DOCS_DIR = BASE_DIR / "docs"

# Load .env if present
load_dotenv(BASE_DIR / ".env")

# Provider Selection: "gemini" (default, 100% free via AI Studio) or "openai"
AI_PROVIDER = os.getenv("AI_PROVIDER", "gemini").lower()

# Google Gemini Settings (Primary / Recommended free tier)
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.5-flash-lite")

# OpenAI Settings (Optional alternative)
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

ASSISTANT_NAME = os.getenv("ASSISTANT_NAME", "OptiBot")

# Vector store / knowledge base naming.
# Gemini: File Search Store (persistent, server-side chunking + embeddings).
# OpenAI: Vector Store. If an existing store name/ID is supplied it is reused,
# otherwise one is created on first run and persisted in sync_state.json.
VECTOR_STORE_DISPLAY_NAME = os.getenv("VECTOR_STORE_DISPLAY_NAME", "kb-delta-indexer-store")
GEMINI_FILE_SEARCH_STORE = os.getenv("GEMINI_FILE_SEARCH_STORE", "")
OPENAI_VECTOR_STORE_ID = os.getenv("OPENAI_VECTOR_STORE_ID", "")

# Zendesk & Scraper Settings
ZENDESK_API_URL = os.getenv(
    "ZENDESK_API_URL", 
    "https://support.optisigns.com/api/v2/help_center/en-us/articles.json"
)
# Sanity threshold: warn if fewer articles than this are ingested (brief asks for >= 30).
MIN_ARTICLES_COUNT = int(os.getenv("MIN_ARTICLES_COUNT", "30"))
# Upper bound on ingested articles. 0 = ingest the entire Help Center.
MAX_ARTICLES = int(os.getenv("MAX_ARTICLES", "0"))

# Chunking Strategy Configuration (Gemini File Search caps chunks at 512 tokens)
CHUNK_SIZE_TOKENS = int(os.getenv("CHUNK_SIZE_TOKENS", "512"))
CHUNK_OVERLAP_TOKENS = int(os.getenv("CHUNK_OVERLAP_TOKENS", "64"))

# Verbatim System Prompt (Mandatory requirement from OptiSigns brief)
SYSTEM_PROMPT = """You are OptiBot, the customer-support bot for OptiSigns.com.
• Tone: helpful, factual, concise.
• Only answer using the uploaded docs.
• Max 5 bullet points; else link to the doc.
• Cite up to 3 "Article URL:" lines per reply."""
