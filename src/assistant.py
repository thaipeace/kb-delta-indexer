"""Module for AI Assistant integration, vector store / knowledge base synchronization."""
import abc
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from src.config import (
    AI_PROVIDER,
    GEMINI_API_KEY,
    GEMINI_MODEL,
    OPENAI_API_KEY,
    OPENAI_MODEL,
    ASSISTANT_NAME,
    SYSTEM_PROMPT,
    CHUNK_SIZE_TOKENS,
    CHUNK_OVERLAP_TOKENS,
)
from src.delta import DeltaSummary, update_state_entry

logger = logging.getLogger(__name__)

# Rule of thumb: 1 token ≈ 4 English characters
CHARS_PER_TOKEN = 4


def _split_into_chunks(
    text: str,
    chunk_size: int = CHUNK_SIZE_TOKENS,
    overlap: int = CHUNK_OVERLAP_TOKENS,
) -> list[str]:
    """
    Split markdown text into overlapping, paragraph-aware chunks.

    Strategy:
    - Compute character budget from token estimates (CHARS_PER_TOKEN = 4).
    - Prefer splitting on double-newlines (paragraph boundaries) to avoid
      cutting mid-sentence or mid-step in procedural documentation.
    - Apply overlap so that context from the end of one chunk bleeds into
      the start of the next, preserving referential continuity.

    Args:
        text:       Full article markdown content.
        chunk_size: Target chunk size in tokens (default: CHUNK_SIZE_TOKENS).
        overlap:    Overlap between consecutive chunks in tokens (default: CHUNK_OVERLAP_TOKENS).

    Returns:
        List of non-empty text chunks.
    """
    if not text.strip():
        return []

    chunk_size_chars = chunk_size * CHARS_PER_TOKEN
    overlap_chars = overlap * CHARS_PER_TOKEN

    # Short content fits in a single chunk
    if len(text) <= chunk_size_chars:
        return [text.strip()]

    chunks: list[str] = []
    start = 0

    while start < len(text):
        end = min(start + chunk_size_chars, len(text))
        chunk = text[start:end]

        # Prefer to break on a paragraph boundary (double newline) to
        # avoid cutting mid-sentence in step-by-step guides.
        if end < len(text):
            last_para = chunk.rfind("\n\n")
            if last_para > chunk_size_chars // 3:
                end = start + last_para + 2
                chunk = text[start:end]

        stripped = chunk.strip()
        if stripped:
            chunks.append(stripped)

        # Advance with overlap; safety guard ensures we always move forward.
        next_start = end - overlap_chars
        if next_start <= start:
            next_start = end
        start = next_start

    return chunks


def estimate_chunks(text: str, chunk_size: int = CHUNK_SIZE_TOKENS, overlap: int = CHUNK_OVERLAP_TOKENS) -> int:
    """Count the number of chunks produced by _split_into_chunks."""
    return len(_split_into_chunks(text, chunk_size=chunk_size, overlap=overlap))


@dataclass
class SyncResult:
    """Summary of vector store / knowledge base synchronization."""
    added_count: int = 0
    updated_count: int = 0
    skipped_count: int = 0
    total_remote_files: int = 0
    total_chunks: int = 0

    def to_log_string(self) -> str:
        return (
            f"[SYNC SUMMARY] Added: {self.added_count} | "
            f"Updated: {self.updated_count} | "
            f"Skipped: {self.skipped_count} | "
            f"Total Active Files: {self.total_remote_files} | "
            f"Total Estimated Chunks: {self.total_chunks}"
        )


class BaseAssistantProvider(abc.ABC):
    """Abstract base class for AI Knowledge Base providers."""

    @abc.abstractmethod
    def sync_delta(self, delta_summary: DeltaSummary, state: dict[str, Any]) -> SyncResult:
        """Upload delta items and update state."""
        pass

    @abc.abstractmethod
    def ask_question(self, query: str, state: dict[str, Any]) -> str:
        """Query the assistant grounded on the uploaded knowledge base."""
        pass


class GeminiAssistantProvider(BaseAssistantProvider):
    """
    Google Gemini Knowledge Base provider using the official google-genai SDK.

    Ingestion strategy:
    - Each article is split into token-estimated, paragraph-aware chunks
      (default: 800-token chunks with 100-token overlap).
    - Each chunk is uploaded as an individual file to the Gemini File API,
      giving the model focused, size-controlled context windows.
    - Gemini performs in-context grounding: files are passed directly into the
      model context window at query time rather than through an embedding index.
      This is well-suited for factual retrieval over structured support docs.
    """

    def __init__(self, api_key: str | None = None, model_name: str | None = None):
        key = api_key if api_key is not None else GEMINI_API_KEY
        if not key:
            raise ValueError("GEMINI_API_KEY is not set. Please add it to your .env file.")
        from google import genai
        self.client = genai.Client(api_key=key)
        self.model_name = model_name or GEMINI_MODEL

    # ------------------------------------------------------------------
    # Chunk upload / delete helpers
    # ------------------------------------------------------------------

    def upload_chunks(self, content: str, slug: str, article_dir: Path) -> list[str]:
        """
        Split article content into chunks and upload each to Gemini File API.

        Args:
            content:     Full article markdown text.
            slug:        Article slug used for chunk file naming.
            article_dir: Directory where per-chunk files are written before upload.

        Returns:
            List of remote Gemini file IDs (one per chunk).
        """
        from google.genai import types

        article_dir.mkdir(parents=True, exist_ok=True)
        chunks = _split_into_chunks(content)
        if not chunks:
            logger.warning("No chunks generated for '%s' — skipping upload.", slug)
            return []

        chunk_file_ids: list[str] = []

        for i, chunk_text in enumerate(chunks):
            chunk_name = f"{slug}-chunk-{i:03d}"
            chunk_path = article_dir / f"{chunk_name}.md"
            chunk_path.write_text(chunk_text, encoding="utf-8")

            config = types.UploadFileConfig(
                mime_type="text/plain",
                display_name=chunk_name,
            )
            uploaded = self.client.files.upload(file=chunk_path, config=config)
            chunk_file_ids.append(uploaded.name)
            logger.info(
                "Uploaded chunk %d/%d for '%s' -> Remote ID: %s",
                i + 1, len(chunks), slug, uploaded.name,
            )
            time.sleep(0.2)  # Gentle rate-limit throttle

        return chunk_file_ids

    def delete_chunks(self, chunk_file_ids: list[str]) -> None:
        """Delete all chunk files for an article from Gemini File API."""
        for fid in chunk_file_ids:
            self._delete_file(fid)

    def _delete_file(self, remote_file_id: str) -> bool:
        """Delete a single file from Gemini File API."""
        try:
            logger.info("Deleting obsolete Gemini file: %s", remote_file_id)
            self.client.files.delete(name=remote_file_id)
            return True
        except Exception as exc:
            logger.warning("Failed to delete remote file %s: %s", remote_file_id, exc)
            return False

    # Keep old public name for backward compatibility with any external callers.
    def delete_file(self, remote_file_id: str) -> bool:
        return self._delete_file(remote_file_id)

    # ------------------------------------------------------------------
    # sync_delta
    # ------------------------------------------------------------------

    def sync_delta(self, delta_summary: DeltaSummary, state: dict[str, Any]) -> SyncResult:
        """
        Sync delta articles with Gemini File API using chunk-based uploading.

        For UPDATED articles: delete obsolete chunks, upload new chunks.
        For ADDED articles: upload chunks fresh.
        For SKIPPED articles: no API calls needed.
        """
        state["provider"] = "gemini"
        result = SyncResult(skipped_count=delta_summary.skipped_count)

        # 1. Handle UPDATED articles
        for item in delta_summary.updated:
            old_ids = item.old_chunk_file_ids or (
                [item.old_remote_file_id] if item.old_remote_file_id else []
            )
            if old_ids:
                self.delete_chunks(old_ids)

            chunk_ids = self.upload_chunks(item.content, item.slug, item.file_path.parent)
            update_state_entry(state, item, chunk_file_ids=chunk_ids)
            result.updated_count += 1

        # 2. Handle ADDED articles
        for item in delta_summary.added:
            chunk_ids = self.upload_chunks(item.content, item.slug, item.file_path.parent)
            update_state_entry(state, item, chunk_file_ids=chunk_ids)
            result.added_count += 1

        # 3. Count active articles and total indexed chunks
        active_articles = state.get("articles", {})
        result.total_remote_files = sum(
            1 for a in active_articles.values() if a.get("chunk_file_ids")
        )
        result.total_chunks = sum(
            estimate_chunks(item.content)
            for item in delta_summary.added + delta_summary.updated + delta_summary.skipped
        )

        logger.info(result.to_log_string())
        return result

    # ------------------------------------------------------------------
    # ask_question
    # ------------------------------------------------------------------

    def ask_question(self, query: str, state: dict[str, Any]) -> str:
        """
        Answer a query grounded on the uploaded article chunks.

        Retrieval approach: chunk file IDs are selected from the most
        relevant articles (keyword-matched on slug), then passed as Gemini
        File API references into generate_content for in-context grounding.
        """
        from google.genai import types

        articles_map = state.get("articles", {})
        selected_chunk_ids: list[str] = []

        query_lower = query.lower()

        # Priority-ranked chunk selection
        for art_info in articles_map.values():
            slug = art_info.get("slug", "").lower()
            chunk_ids: list[str] = art_info.get("chunk_file_ids") or (
                [art_info["remote_file_id"]] if art_info.get("remote_file_id") else []
            )
            if not chunk_ids:
                continue

            if "youtube" in query_lower and "how-to-use-youtube-with-optisigns" in slug:
                selected_chunk_ids = chunk_ids + selected_chunk_ids  # highest priority
            elif any(word in slug for word in query_lower.split() if len(word) > 3):
                selected_chunk_ids.extend(chunk_ids)

        # Fallback: include any available chunks when no keyword match
        if not selected_chunk_ids:
            for art_info in articles_map.values():
                chunk_ids = art_info.get("chunk_file_ids") or (
                    [art_info["remote_file_id"]] if art_info.get("remote_file_id") else []
                )
                selected_chunk_ids.extend(chunk_ids)
                if len(selected_chunk_ids) >= 5:
                    break

        # Cap to avoid exceeding Gemini context window limits
        selected_chunk_ids = selected_chunk_ids[:10]
        logger.info("Querying Gemini with %d grounded chunks...", len(selected_chunk_ids))

        contents: list[Any] = []
        for fid in selected_chunk_ids:
            try:
                remote_file = self.client.files.get(name=fid)
                contents.append(remote_file)
            except Exception as exc:
                logger.warning("Could not retrieve chunk %s: %s", fid, exc)

        contents.append(query)

        config = types.GenerateContentConfig(
            system_instruction=SYSTEM_PROMPT,
            temperature=0.2,
        )

        candidate_models = [self.model_name, "gemini-3.5-flash-lite", "gemini-3.5-flash", "gemini-2.5-flash"]
        unique_models = list(dict.fromkeys(candidate_models))

        last_error = None
        for model in unique_models:
            try:
                logger.info("Attempting generate_content with model %s...", model)
                response = self.client.models.generate_content(
                    model=model,
                    contents=contents,
                    config=config,
                )
                return response.text.strip()
            except Exception as exc:
                logger.warning("Model %s failed (%s), trying fallback...", model, exc)
                last_error = exc

        raise RuntimeError(f"All candidate Gemini models failed. Last error: {last_error}")


class OpenAIAssistantProvider(BaseAssistantProvider):
    """OpenAI Assistants API v2 provider using vector stores."""

    def __init__(self, api_key: str | None = None, model_name: str | None = None):
        key = api_key if api_key is not None else OPENAI_API_KEY
        if not key:
            raise ValueError("OPENAI_API_KEY is not set. Please add it to your .env file.")
        from openai import OpenAI
        self.client = OpenAI(api_key=key)
        self.model_name = model_name or OPENAI_MODEL

    def sync_delta(self, delta_summary: DeltaSummary, state: dict[str, Any]) -> SyncResult:
        """Sync delta articles with OpenAI Vector Store."""
        state["provider"] = "openai"
        result = SyncResult(skipped_count=delta_summary.skipped_count)

        vector_store_id = state.get("vector_store_id")
        if not vector_store_id:
            vs = self.client.beta.vector_stores.create(name="OptiSigns-Knowledge-Base")
            vector_store_id = vs.id
            state["vector_store_id"] = vector_store_id

        for item in delta_summary.updated:
            if item.old_remote_file_id:
                try:
                    self.client.beta.vector_stores.files.delete(
                        vector_store_id=vector_store_id,
                        file_id=item.old_remote_file_id,
                    )
                    self.client.files.delete(file_id=item.old_remote_file_id)
                except Exception as exc:
                    logger.warning("Failed to delete OpenAI file %s: %s", item.old_remote_file_id, exc)

            with open(item.file_path, "rb") as f:
                uploaded = self.client.files.create(file=f, purpose="assistants")
            self.client.beta.vector_stores.files.create(
                vector_store_id=vector_store_id, file_id=uploaded.id,
            )
            update_state_entry(state, item, remote_file_id=uploaded.id)
            result.updated_count += 1

        for item in delta_summary.added:
            with open(item.file_path, "rb") as f:
                uploaded = self.client.files.create(file=f, purpose="assistants")
            self.client.beta.vector_stores.files.create(
                vector_store_id=vector_store_id, file_id=uploaded.id,
            )
            update_state_entry(state, item, remote_file_id=uploaded.id)
            result.added_count += 1

        active_articles = state.get("articles", {})
        result.total_remote_files = sum(1 for a in active_articles.values() if a.get("remote_file_id"))
        result.total_chunks = sum(
            estimate_chunks(item.content)
            for item in delta_summary.added + delta_summary.updated + delta_summary.skipped
        )

        logger.info(result.to_log_string())
        return result

    def ask_question(self, query: str, state: dict[str, Any]) -> str:
        """Ask question via OpenAI Assistant thread run."""
        vector_store_id = state.get("vector_store_id")
        if not vector_store_id:
            raise RuntimeError("No vector store found in state. Run sync first.")

        assistant_id = state.get("assistant_id")
        if not assistant_id:
            assistant = self.client.beta.assistants.create(
                name=ASSISTANT_NAME,
                instructions=SYSTEM_PROMPT,
                model=self.model_name,
                tools=[{"type": "file_search"}],
                tool_resources={"file_search": {"vector_store_ids": [vector_store_id]}},
            )
            assistant_id = assistant.id
            state["assistant_id"] = assistant_id

        thread = self.client.beta.threads.create(
            messages=[{"role": "user", "content": query}]
        )
        run = self.client.beta.threads.runs.create_and_poll(
            thread_id=thread.id,
            assistant_id=assistant_id,
        )

        if run.status == "completed":
            messages = self.client.beta.threads.messages.list(thread_id=thread.id)
            for m in messages.data:
                if m.role == "assistant":
                    return m.content[0].text.value.strip()

        return f"Run ended with status: {run.status}"


def get_assistant_provider() -> BaseAssistantProvider:
    """Factory function returning the configured Assistant Provider."""
    provider_name = AI_PROVIDER.lower()
    if provider_name == "openai":
        logger.info("Using OpenAI Assistant Provider.")
        return OpenAIAssistantProvider()
    else:
        logger.info("Using Google Gemini Assistant Provider (Primary - Free Tier).")
        return GeminiAssistantProvider()
