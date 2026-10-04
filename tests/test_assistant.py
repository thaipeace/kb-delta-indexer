"""Unit tests for AI Assistant Provider and Knowledge Base synchronization."""
from pathlib import Path
from unittest.mock import MagicMock, patch
from src.assistant import (
    estimate_chunks,
    SyncResult,
    GeminiAssistantProvider,
    get_assistant_provider,
)
from src.config import CHUNK_SIZE_TOKENS, CHUNK_OVERLAP_TOKENS
from src.delta import ArticleDelta, DeltaSummary


def test_estimate_chunks_empty_and_short():
    assert estimate_chunks("") == 0
    assert estimate_chunks("   ") == 0
    # Short string (under 512 whitespace tokens) -> 1 chunk
    assert estimate_chunks("Short text about OptiSigns") == 1


def test_estimate_chunks_long():
    # 2,000 tokens, window 800, stride 700 -> 1 + ceil(1200/700) = 3 chunks
    long_text = "token " * 2000
    assert estimate_chunks(long_text, chunk_size=800, overlap=100) == 3


def test_sync_result_log_formatting():
    result = SyncResult(
        added_count=5,
        updated_count=2,
        skipped_count=30,
        embedded_files=7,
        embedded_chunks=12,
        total_remote_files=37,
        total_chunks=115,
    )
    log_str = result.to_log_string()
    assert "Added: 5" in log_str
    assert "Updated: 2" in log_str
    assert "Skipped: 30" in log_str
    assert "Embedded this run: 7 files / 12 chunks" in log_str
    assert "Store total: 37 files / 115 chunks" in log_str


def _item(tmp_path: Path, article_id: int, action: str, old_id: str | None = None) -> ArticleDelta:
    f = tmp_path / f"art{article_id}.md"
    f.write_text(f"# Article {article_id}", encoding="utf-8")
    return ArticleDelta(
        article_id=article_id,
        action=action,
        slug=f"art{article_id}",
        title=f"Art {article_id}",
        url=f"https://support.optisigns.com/{article_id}",
        updated_at="2026-01-01T00:00:00Z",
        content_hash=f"h{article_id}",
        file_path=f,
        content=f"# Article {article_id}",
        old_remote_file_id=old_id,
    )


def _done_op(doc_name: str) -> MagicMock:
    op = MagicMock()
    op.done = True
    op.error = None
    op.response.document_name = doc_name
    return op


STORE = "fileSearchStores/kb-store"


@patch("google.genai.Client")
def test_gemini_sync_delta_uses_file_search_store(mock_client_cls, tmp_path: Path):
    mock_client = MagicMock()
    mock_client_cls.return_value = mock_client
    mock_client.file_search_stores.upload_to_file_search_store.side_effect = [
        _done_op(f"{STORE}/documents/new-102"),
        _done_op(f"{STORE}/documents/new-101"),
    ]
    provider = GeminiAssistantProvider(api_key="mock-key", model_name="gemini-2.5-flash")

    summary = DeltaSummary(
        total_scraped=3,
        added=[_item(tmp_path, 101, "ADDED")],
        updated=[_item(tmp_path, 102, "UPDATED", old_id=f"{STORE}/documents/old-102")],
        skipped=[_item(tmp_path, 103, "SKIPPED", old_id=f"{STORE}/documents/doc-103")],
    )
    state = {"version": 1, "file_search_store_name": STORE, "articles": {}}
    result = provider.sync_delta(summary, state)

    # Existing store reused, not recreated
    mock_client.file_search_stores.get.assert_called_once_with(name=STORE)
    mock_client.file_search_stores.create.assert_not_called()

    # Only the delta (1 added + 1 updated) was uploaded; skipped untouched
    upload = mock_client.file_search_stores.upload_to_file_search_store
    assert upload.call_count == 2
    cfg = upload.call_args.kwargs["config"]
    assert cfg.chunking_config.white_space_config.max_tokens_per_chunk == CHUNK_SIZE_TOKENS
    assert cfg.chunking_config.white_space_config.max_overlap_tokens == CHUNK_OVERLAP_TOKENS

    # Old version of the updated doc removed after new upload succeeded
    mock_client.file_search_stores.documents.delete.assert_called_once_with(
        name=f"{STORE}/documents/old-102", config={"force": True}
    )

    assert (result.added_count, result.updated_count, result.skipped_count) == (1, 1, 1)
    assert result.embedded_files == 2
    assert result.embedded_chunks == 2
    assert state["articles"]["101"]["remote_file_id"] == f"{STORE}/documents/new-101"
    assert state["articles"]["102"]["remote_file_id"] == f"{STORE}/documents/new-102"
    assert state["articles"]["101"]["chunk_count"] == 1


@patch("google.genai.Client")
def test_gemini_sync_reindexes_skipped_when_store_recreated(mock_client_cls, tmp_path: Path):
    mock_client = MagicMock()
    mock_client_cls.return_value = mock_client
    mock_client.file_search_stores.get.side_effect = Exception("404 not found")
    mock_client.file_search_stores.create.return_value = MagicMock(name="store")
    mock_client.file_search_stores.create.return_value.name = "fileSearchStores/fresh"
    mock_client.file_search_stores.upload_to_file_search_store.return_value = _done_op(
        "fileSearchStores/fresh/documents/d1"
    )
    provider = GeminiAssistantProvider(api_key="mock-key")

    summary = DeltaSummary(total_scraped=1, skipped=[_item(tmp_path, 7, "SKIPPED", old_id="files/legacy")])
    state = {"version": 1, "file_search_store_name": "fileSearchStores/gone", "articles": {}}
    result = provider.sync_delta(summary, state)

    assert state["file_search_store_name"] == "fileSearchStores/fresh"
    assert result.reindexed_count == 1
    assert result.skipped_count == 0
    mock_client.file_search_stores.documents.delete.assert_not_called()


@patch("google.genai.Client")
def test_gemini_sync_failed_upload_leaves_state_untouched(mock_client_cls, tmp_path: Path):
    mock_client = MagicMock()
    mock_client_cls.return_value = mock_client
    failed = MagicMock(done=True, error={"message": "boom"})
    mock_client.file_search_stores.upload_to_file_search_store.return_value = failed
    provider = GeminiAssistantProvider(api_key="mock-key")

    summary = DeltaSummary(total_scraped=1, added=[_item(tmp_path, 9, "ADDED")])
    state = {"version": 1, "file_search_store_name": STORE, "articles": {}}
    result = provider.sync_delta(summary, state)

    assert result.failed_count == 1
    assert result.added_count == 0
    assert "9" not in state["articles"]


@patch("google.genai.Client")
def test_gemini_ask_question_uses_file_search_tool(mock_client_cls):
    mock_client = MagicMock()
    mock_client_cls.return_value = mock_client
    mock_client.models.generate_content.return_value.text = "Answer\nArticle URL: https://x"
    provider = GeminiAssistantProvider(api_key="mock-key", model_name="gemini-2.5-flash")

    answer = provider.ask_question("How do I add a YouTube video?", {"file_search_store_name": STORE})

    assert answer.startswith("Answer")
    cfg = mock_client.models.generate_content.call_args.kwargs["config"]
    assert cfg.tools[0].file_search.file_search_store_names == [STORE]


def test_get_assistant_provider_returns_gemini():
    with patch("src.assistant.GEMINI_API_KEY", "dummy-key"):
        with patch("google.genai.Client"):
            provider = get_assistant_provider()
            assert isinstance(provider, GeminiAssistantProvider)
