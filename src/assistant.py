"""Module for AI Assistant integration and vector store / knowledge base synchronization.

Two interchangeable providers are supported:

* Gemini (default): Google **File Search Store** — Gemini's managed vector store.
  Documents are uploaded via API, chunked + embedded server-side using our
  chunking config, and retrieved through the ``file_search`` tool at query time.
* OpenAI: **Vector Store** + Responses API ``file_search`` tool.

Both stores are persistent, so unchanged (SKIPPED) articles stay indexed across
daily runs and only the delta is (re-)uploaded.
"""
import abc
import logging
import math
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from src.config import (
    AI_PROVIDER,
    GEMINI_API_KEY,
    GEMINI_MODEL,
    GEMINI_FILE_SEARCH_STORE,
    OPENAI_API_KEY,
    OPENAI_MODEL,
    OPENAI_VECTOR_STORE_ID,
    VECTOR_STORE_DISPLAY_NAME,
    SYSTEM_PROMPT,
    CHUNK_SIZE_TOKENS,
    CHUNK_OVERLAP_TOKENS,
)
from src.delta import ArticleDelta, DeltaSummary, update_state_entry

logger = logging.getLogger(__name__)

# Max seconds to wait for server-side chunking/embedding of a single batch.
INDEXING_TIMEOUT_SEC = 600
POLL_INTERVAL_SEC = 2


def estimate_chunks(
    text: str,
    chunk_size: int = CHUNK_SIZE_TOKENS,
    overlap: int = CHUNK_OVERLAP_TOKENS,
) -> int:
    """
    Estimate how many chunks the vector store will produce for ``text``.

    Mirrors the server-side *white-space* chunker we configure on upload
    (``max_tokens_per_chunk`` / ``max_overlap_tokens``): tokens are
    whitespace-delimited words and consecutive windows advance by
    ``chunk_size - overlap`` tokens. Neither API returns per-document chunk
    counts, so this is used for the "chunks embedded" log line.
    """
    n_tokens = len(text.split())
    if n_tokens == 0:
        return 0
    if n_tokens <= chunk_size:
        return 1
    stride = max(chunk_size - overlap, 1)
    return 1 + math.ceil((n_tokens - chunk_size) / stride)


@dataclass
class SyncResult:
    """Summary of vector store / knowledge base synchronization."""
    added_count: int = 0
    updated_count: int = 0
    skipped_count: int = 0
    reindexed_count: int = 0      # previously-synced docs re-uploaded because the store was (re)created
    failed_count: int = 0
    embedded_files: int = 0       # files uploaded + embedded in THIS run
    embedded_chunks: int = 0      # chunks embedded in THIS run
    total_remote_files: int = 0   # documents currently in the store
    total_chunks: int = 0         # chunks currently in the store

    def to_log_string(self) -> str:
        return (
            f"[SYNC SUMMARY] Added: {self.added_count} | "
            f"Updated: {self.updated_count} | "
            f"Skipped: {self.skipped_count} | "
            f"Re-indexed: {self.reindexed_count} | "
            f"Failed: {self.failed_count} | "
            f"Embedded this run: {self.embedded_files} files / {self.embedded_chunks} chunks | "
            f"Store total: {self.total_remote_files} files / {self.total_chunks} chunks"
        )


class BaseAssistantProvider(abc.ABC):
    """Abstract base class for AI Knowledge Base providers."""

    @abc.abstractmethod
    def sync_delta(self, delta_summary: DeltaSummary, state: dict[str, Any]) -> SyncResult:
        """Upload delta items to the vector store and update state."""

    @abc.abstractmethod
    def ask_question(self, query: str, state: dict[str, Any]) -> str:
        """Query the assistant grounded on the vector store."""

    # ------------------------------------------------------------------
    # Shared helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _plan_uploads(
        delta_summary: DeltaSummary, in_store: Callable[[str | None], bool]
    ) -> tuple[list[tuple[ArticleDelta, str]], int]:
        """
        Decide which articles to upload.

        Returns ``(jobs, skipped_count)`` where ``jobs`` is a list of
        ``(item, action)`` with action in {"ADDED", "UPDATED", "REINDEXED"}.
        An unchanged (SKIPPED) article whose document is not in the current
        store (store recreated, or legacy state) is re-uploaded.
        """
        jobs: list[tuple[ArticleDelta, str]] = []
        jobs += [(i, "UPDATED") for i in delta_summary.updated]
        jobs += [(i, "ADDED") for i in delta_summary.added]
        missing = [i for i in delta_summary.skipped if not in_store(i.old_remote_file_id)]
        if missing:
            logger.warning("%d unchanged articles are not in the current store -> re-indexing.", len(missing))
            jobs += [(i, "REINDEXED") for i in missing]
        return jobs, delta_summary.skipped_count - len(missing)

    @staticmethod
    def _tally(result: SyncResult, action: str) -> None:
        if action == "ADDED":
            result.added_count += 1
        elif action == "UPDATED":
            result.updated_count += 1
        else:
            result.reindexed_count += 1

    @staticmethod
    def _store_totals(result: SyncResult, state: dict[str, Any], in_store: Callable[[str | None], bool]) -> None:
        live = [a for a in state.get("articles", {}).values() if in_store(a.get("remote_file_id"))]
        result.total_remote_files = len(live)
        result.total_chunks = sum(int(a.get("chunk_count", 0)) for a in live)


class GeminiAssistantProvider(BaseAssistantProvider):
    """
    Google Gemini provider backed by a **File Search Store** (managed vector store).

    * ``upload_to_file_search_store`` uploads each article's Markdown file; Gemini
      chunks it with our white-space chunking config (512 tokens / 64 overlap,
      which is the maximum allowed by Google Gemini File Search Store API;
      OpenAI provider supports 800/100 or configurable), embeds the chunks and
      indexes them. Documents persist until deleted.
    * Each document carries ``article_id`` / ``url`` custom metadata.
    * ``ask_question`` calls ``generate_content`` with the ``file_search`` tool so
      retrieval is semantic (embedding search), not keyword-based.
    """

    def __init__(self, api_key: str | None = None, model_name: str | None = None):
        key = api_key if api_key is not None else GEMINI_API_KEY
        if not key:
            raise ValueError("GEMINI_API_KEY is not set. Please add it to your .env file.")
        from google import genai
        self.client = genai.Client(api_key=key)
        self.model_name = model_name or GEMINI_MODEL

    # ------------------------------------------------------------------
    # Store management
    # ------------------------------------------------------------------

    def ensure_store(self, state: dict[str, Any]) -> bool:
        """
        Make sure a File Search Store exists and its name is recorded in state.

        Returns True if a NEW store was created (caller must re-index everything).
        """
        name = state.get("file_search_store_name") or GEMINI_FILE_SEARCH_STORE
        if name:
            try:
                self.client.file_search_stores.get(name=name)
                state["file_search_store_name"] = name
                logger.info("Using existing File Search Store: %s", name)
                return False
            except Exception as exc:
                logger.warning("File Search Store %s unavailable (%s); creating a new one.", name, exc)

        store = self.client.file_search_stores.create(
            config={"display_name": VECTOR_STORE_DISPLAY_NAME}
        )
        state["file_search_store_name"] = store.name
        logger.info("Created File Search Store: %s", store.name)
        return True

    def delete_document(self, document_name: str) -> bool:
        """Delete a document (and all its chunks) from the File Search Store."""
        try:
            self.client.file_search_stores.documents.delete(
                name=document_name, config={"force": True}
            )
            logger.info("Deleted obsolete document: %s", document_name)
            return True
        except Exception as exc:
            logger.warning("Failed to delete document %s: %s", document_name, exc)
            return False

    def _start_upload(self, item: ArticleDelta, store_name: str) -> Any:
        """Kick off an async upload+index operation for one article."""
        from google.genai import types

        # Ensure file exists on disk with content and resolve absolute path
        file_path = Path(item.file_path).resolve()
        if not file_path.is_file():
            file_path.parent.mkdir(parents=True, exist_ok=True)
            content = item.content or f"# {item.title}\n\nArticle URL: {item.url}\n"
            file_path.write_text(content, encoding="utf-8")

        config = types.UploadToFileSearchStoreConfig(
            display_name=item.slug,
            mime_type="text/markdown",
            custom_metadata=[
                types.CustomMetadata(key="article_id", numeric_value=float(item.article_id)),
                types.CustomMetadata(key="url", string_value=item.url),
            ],
            chunking_config=types.ChunkingConfig(
                white_space_config=types.WhiteSpaceConfig(
                    max_tokens_per_chunk=CHUNK_SIZE_TOKENS,
                    max_overlap_tokens=CHUNK_OVERLAP_TOKENS,
                )
            ),
        )
        return self.client.file_search_stores.upload_to_file_search_store(
            file_search_store_name=store_name,
            file=str(file_path),
            config=config,
        )

    def _wait_for(self, operations: list[Any]) -> list[Any]:
        """Poll a batch of long-running indexing operations until all are done."""
        deadline = time.monotonic() + INDEXING_TIMEOUT_SEC
        ops = list(operations)
        while True:
            pending = [i for i, op in enumerate(ops) if not op.done]
            if not pending:
                return ops
            if time.monotonic() > deadline:
                logger.error("Timed out waiting for %d indexing operations.", len(pending))
                return ops
            time.sleep(POLL_INTERVAL_SEC)
            for i in pending:
                try:
                    ops[i] = self.client.operations.get(ops[i])
                except Exception as exc:
                    logger.warning("Polling operation failed: %s", exc)

    # ------------------------------------------------------------------
    # sync_delta
    # ------------------------------------------------------------------

    def sync_delta(self, delta_summary: DeltaSummary, state: dict[str, Any]) -> SyncResult:
        """
        Upload only the delta to the File Search Store.

        * ADDED   -> upload new document.
        * UPDATED -> upload new version, then delete the old document.
        * SKIPPED -> no API calls (document already indexed and persistent).
        """
        state["provider"] = "gemini"
        self.ensure_store(state)
        store_name = state["file_search_store_name"]

        def in_store(doc: str | None) -> bool:
            return bool(doc) and doc.startswith(f"{store_name}/documents/")

        jobs, skipped = self._plan_uploads(delta_summary, in_store)
        result = SyncResult(skipped_count=skipped)

        # Fire uploads in batches, then poll — far faster than upload+wait one by one.
        batch_size = 20
        for start in range(0, len(jobs), batch_size):
            batch = jobs[start:start + batch_size]
            started: list[tuple[ArticleDelta, str, Any]] = []
            for item, action in batch:
                try:
                    started.append((item, action, self._start_upload(item, store_name)))
                except Exception as exc:
                    logger.error("Upload failed for '%s': %s", item.slug, exc)
                    result.failed_count += 1

            finished = self._wait_for([op for _, _, op in started])

            for (item, action, _), op in zip(started, finished):
                doc_name = getattr(getattr(op, "response", None), "document_name", None)
                if not op.done or op.error or not doc_name:
                    logger.error("Indexing failed for '%s': %s", item.slug, op.error or "timeout")
                    result.failed_count += 1
                    continue  # state untouched -> retried on next run

                # Old doc is removed only after the new one is safely indexed.
                if action == "UPDATED" and in_store(item.old_remote_file_id):
                    self.delete_document(item.old_remote_file_id)

                chunks = estimate_chunks(item.content)
                update_state_entry(state, item, remote_file_id=doc_name, chunk_count=chunks)
                self._tally(result, action)
                result.embedded_files += 1
                result.embedded_chunks += chunks
                logger.info("[%s] %s -> %s (%d chunks)", action, item.slug, doc_name, chunks)

        self._store_totals(result, state, in_store)
        logger.info(result.to_log_string())
        return result

    # ------------------------------------------------------------------
    # ask_question
    # ------------------------------------------------------------------

    def ask_question(self, query: str, state: dict[str, Any]) -> str:
        """Answer a query using Gemini + the file_search tool over the store."""
        from google.genai import types

        store_name = state.get("file_search_store_name")
        if not store_name:
            raise RuntimeError("No File Search Store in state. Run main.py first.")

        config = types.GenerateContentConfig(
            system_instruction=SYSTEM_PROMPT,
            temperature=0.2,
            tools=[types.Tool(file_search=types.FileSearch(file_search_store_names=[store_name]))],
        )

        candidate_models = list(dict.fromkeys(
            [self.model_name, "gemini-3.5-flash-lite", "gemini-3.5-flash", "gemini-2.5-flash"]
        ))
        last_error: Exception | None = None
        for model in candidate_models:
            try:
                logger.info("Querying %s with file_search over %s ...", model, store_name)
                response = self.client.models.generate_content(
                    model=model, contents=query, config=config,
                )
                answer = (response.text or "").strip()
                sources = self._retrieved_sources(response)
                if sources:
                    answer += "\n\n[Retrieved from File Search Store: " + ", ".join(sources) + "]"
                return answer
            except Exception as exc:
                logger.warning("Model %s failed (%s), trying fallback...", model, exc)
                last_error = exc
        raise RuntimeError(f"All candidate Gemini models failed. Last error: {last_error}")

    @staticmethod
    def _retrieved_sources(response: Any) -> list[str]:
        """Extract unique document titles from grounding metadata (proof of retrieval)."""
        titles: list[str] = []
        try:
            meta = response.candidates[0].grounding_metadata
            for chunk in (meta.grounding_chunks or []):
                ctx = chunk.retrieved_context
                title = ctx and (ctx.title or ctx.document_name)
                if title and title not in titles:
                    titles.append(title)
        except Exception:
            pass
        return titles


class OpenAIAssistantProvider(BaseAssistantProvider):
    """OpenAI provider: Vector Store + Responses API ``file_search`` tool."""

    def __init__(self, api_key: str | None = None, model_name: str | None = None):
        key = api_key if api_key is not None else OPENAI_API_KEY
        if not key:
            raise ValueError("OPENAI_API_KEY is not set. Please add it to your .env file.")
        from openai import OpenAI
        self.client = OpenAI(api_key=key)
        self.model_name = model_name or OPENAI_MODEL

    def ensure_store(self, state: dict[str, Any]) -> bool:
        vs_id = state.get("vector_store_id") or OPENAI_VECTOR_STORE_ID
        if vs_id:
            try:
                self.client.vector_stores.retrieve(vs_id)
                state["vector_store_id"] = vs_id
                return False
            except Exception as exc:
                logger.warning("Vector store %s unavailable (%s); creating a new one.", vs_id, exc)
        vs = self.client.vector_stores.create(name=VECTOR_STORE_DISPLAY_NAME)
        state["vector_store_id"] = vs.id
        logger.info("Created OpenAI Vector Store: %s", vs.id)
        return True

    def sync_delta(self, delta_summary: DeltaSummary, state: dict[str, Any]) -> SyncResult:
        state["provider"] = "openai"
        store_is_new = self.ensure_store(state)
        vs_id = state["vector_store_id"]

        # OpenAI file IDs carry no store prefix; trust state unless the store was recreated.
        def in_store(file_id: str | None) -> bool:
            return bool(file_id) and not store_is_new and str(file_id).startswith("file-")

        jobs, skipped = self._plan_uploads(delta_summary, in_store)
        result = SyncResult(skipped_count=skipped)
        chunking = {
            "type": "static",
            "static": {
                "max_chunk_size_tokens": CHUNK_SIZE_TOKENS,
                "chunk_overlap_tokens": CHUNK_OVERLAP_TOKENS,
            },
        }

        for item, action in jobs:
            try:
                with open(item.file_path, "rb") as f:
                    vs_file = self.client.vector_stores.files.upload_and_poll(
                        vector_store_id=vs_id, file=f, chunking_strategy=chunking,
                    )
                if vs_file.status != "completed":
                    raise RuntimeError(f"status={vs_file.status}")
            except Exception as exc:
                logger.error("Indexing failed for '%s': %s", item.slug, exc)
                result.failed_count += 1
                continue

            if action == "UPDATED" and in_store(item.old_remote_file_id):
                try:
                    self.client.vector_stores.files.delete(
                        vector_store_id=vs_id, file_id=item.old_remote_file_id
                    )
                    self.client.files.delete(item.old_remote_file_id)
                except Exception as exc:
                    logger.warning("Failed to delete old file %s: %s", item.old_remote_file_id, exc)

            chunks = estimate_chunks(item.content)
            update_state_entry(state, item, remote_file_id=vs_file.id, chunk_count=chunks)
            self._tally(result, action)
            result.embedded_files += 1
            result.embedded_chunks += chunks

        self._store_totals(result, state, lambda fid: bool(fid))
        logger.info(result.to_log_string())
        return result

    def ask_question(self, query: str, state: dict[str, Any]) -> str:
        vs_id = state.get("vector_store_id")
        if not vs_id:
            raise RuntimeError("No vector store found in state. Run main.py first.")
        response = self.client.responses.create(
            model=self.model_name,
            instructions=SYSTEM_PROMPT,
            input=query,
            tools=[{"type": "file_search", "vector_store_ids": [vs_id]}],
        )
        return response.output_text.strip()


def get_assistant_provider() -> BaseAssistantProvider:
    """Factory function returning the configured Assistant Provider."""
    if AI_PROVIDER.lower() == "openai":
        logger.info("Using OpenAI provider (Vector Store).")
        return OpenAIAssistantProvider()
    logger.info("Using Google Gemini provider (File Search Store).")
    return GeminiAssistantProvider()
