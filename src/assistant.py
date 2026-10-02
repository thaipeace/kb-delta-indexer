"""Module for AI Assistant integration, vector store / knowledge base synchronization."""
import abc
import logging
import math
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


def estimate_chunks(text: str, chunk_size: int = CHUNK_SIZE_TOKENS, overlap: int = CHUNK_OVERLAP_TOKENS) -> int:
    """Estimate number of token chunks for a given text based on 4 chars per token rule of thumb."""
    if not text.strip():
        return 0
    approx_tokens = len(text) / 4.0
    effective_step = max(1, chunk_size - overlap)
    return max(1, math.ceil(approx_tokens / effective_step))


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
    """Google Gemini Knowledge Base provider using official google-genai SDK."""

    def __init__(self, api_key: str | None = None, model_name: str | None = None):
        key = api_key if api_key is not None else GEMINI_API_KEY
        if not key:
            raise ValueError("GEMINI_API_KEY is not set. Please add it to your .env file.")
        from google import genai
        self.client = genai.Client(api_key=key)
        self.model_name = model_name or GEMINI_MODEL

    def upload_file(self, file_path: Path, display_name: str) -> str:
        """Upload a markdown file to Gemini File API."""
        from google.genai import types

        logger.info("Uploading %s to Gemini File API...", file_path.name)
        config = types.UploadFileConfig(
            mime_type="text/plain",
            display_name=display_name,
        )
        uploaded = self.client.files.upload(file=file_path, config=config)
        logger.info("Uploaded %s -> Remote ID: %s", file_path.name, uploaded.name)
        return uploaded.name

    def delete_file(self, remote_file_id: str) -> bool:
        """Delete an obsolete file from Gemini File API."""
        try:
            logger.info("Deleting obsolete Gemini file: %s", remote_file_id)
            self.client.files.delete(name=remote_file_id)
            return True
        except Exception as exc:
            logger.warning("Failed to delete remote file %s: %s", remote_file_id, exc)
            return False

    def sync_delta(self, delta_summary: DeltaSummary, state: dict[str, Any]) -> SyncResult:
        """Sync delta articles with Gemini File API and update sync state."""
        state["provider"] = "gemini"
        result = SyncResult(
            skipped_count=delta_summary.skipped_count,
        )

        # 1. Handle UPDATED articles (delete obsolete remote file, then re-upload)
        for item in delta_summary.updated:
            if item.old_remote_file_id:
                self.delete_file(item.old_remote_file_id)

            remote_id = self.upload_file(item.file_path, display_name=item.slug)
            update_state_entry(state, item, remote_file_id=remote_id)
            result.updated_count += 1
            time.sleep(0.3)  # Gentle throttle for rate limits

        # 2. Handle ADDED articles (upload new)
        for item in delta_summary.added:
            remote_id = self.upload_file(item.file_path, display_name=item.slug)
            update_state_entry(state, item, remote_file_id=remote_id)
            result.added_count += 1
            time.sleep(0.3)  # Gentle throttle for rate limits

        # 3. Calculate total chunks and active files
        active_articles = state.get("articles", {})
        result.total_remote_files = sum(1 for a in active_articles.values() if a.get("remote_file_id"))

        # Compute total chunks for all indexed articles
        total_chunks = 0
        for item in delta_summary.added + delta_summary.updated + delta_summary.skipped:
            total_chunks += estimate_chunks(item.content)
        result.total_chunks = total_chunks

        logger.info(result.to_log_string())
        return result

    def ask_question(self, query: str, state: dict[str, Any]) -> str:
        """Ask a question grounded with the uploaded files using Gemini."""
        from google.genai import types

        # Find YouTube setup or relevant article files from state
        contents: list[Any] = []

        # Find the YouTube article specifically or pick relevant grounded files
        # Prioritize matching file for the specific query
        articles_map = state.get("articles", {})
        selected_file_ids: list[str] = []

        # Find YouTube setup or relevant article files from state
        query_lower = query.lower()
        for art_id, art_info in articles_map.items():
            slug = art_info.get("slug", "").lower()
            remote_id = art_info.get("remote_file_id")
            if not remote_id:
                continue
            if "youtube" in query_lower and "how-to-use-youtube-with-optisigns" in slug:
                selected_file_ids.insert(0, remote_id)
            elif any(k in slug for k in ["youtube", "getting-started", "screens", "playlist"]):
                selected_file_ids.append(remote_id)

        # Fallback if no matching file
        if not selected_file_ids:
            for art_info in articles_map.values():
                remote_id = art_info.get("remote_file_id")
                if remote_id:
                    selected_file_ids.append(remote_id)
                if len(selected_file_ids) >= 3:
                    break

        # Limit to top 3 grounded docs to ensure high precision
        selected_file_ids = selected_file_ids[:3]
        logger.info("Querying Gemini with %d grounded files...", len(selected_file_ids))

        # Retrieve file references from Gemini
        for fid in selected_file_ids:
            try:
                remote_file = self.client.files.get(name=fid)
                contents.append(remote_file)
            except Exception as exc:
                logger.warning("Could not retrieve file %s: %s", fid, exc)

        contents.append(query)

        config = types.GenerateContentConfig(
            system_instruction=SYSTEM_PROMPT,
            temperature=0.2,
        )

        candidate_models = [self.model_name, "gemini-3.5-flash-lite", "gemini-3.5-flash", "gemini-2.5-flash"]
        # Deduplicate while preserving order
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

        # Get or create vector store
        vector_store_id = state.get("vector_store_id")
        if not vector_store_id:
            vs = self.client.beta.vector_stores.create(name="OptiSigns-Knowledge-Base")
            vector_store_id = vs.id
            state["vector_store_id"] = vector_store_id

        # Handle UPDATED (delete old file from vector store and storage)
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
                vector_store_id=vector_store_id,
                file_id=uploaded.id,
            )
            update_state_entry(state, item, remote_file_id=uploaded.id)
            result.updated_count += 1

        # Handle ADDED
        for item in delta_summary.added:
            with open(item.file_path, "rb") as f:
                uploaded = self.client.files.create(file=f, purpose="assistants")
            self.client.beta.vector_stores.files.create(
                vector_store_id=vector_store_id,
                file_id=uploaded.id,
            )
            update_state_entry(state, item, remote_file_id=uploaded.id)
            result.added_count += 1

        active_articles = state.get("articles", {})
        result.total_remote_files = sum(1 for a in active_articles.values() if a.get("remote_file_id"))

        total_chunks = 0
        for item in delta_summary.added + delta_summary.updated + delta_summary.skipped:
            total_chunks += estimate_chunks(item.content)
        result.total_chunks = total_chunks

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
