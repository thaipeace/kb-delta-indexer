"""Unit tests for AI Assistant Provider and Knowledge Base synchronization."""
from pathlib import Path
from unittest.mock import MagicMock, patch
from src.assistant import (
    estimate_chunks,
    SyncResult,
    GeminiAssistantProvider,
    get_assistant_provider,
)
from src.delta import ArticleDelta, DeltaSummary


def test_estimate_chunks_empty_and_short():
    assert estimate_chunks("") == 0
    assert estimate_chunks("   ") == 0
    # Short string (under 800 tokens = ~3200 chars) -> 1 chunk
    assert estimate_chunks("Short text about OptiSigns") == 1


def test_estimate_chunks_long():
    # 7000 chars is ~1750 tokens -> with step 700 tokens (800 - 100) -> 3 chunks
    long_text = "OptiSigns digital signage platform rules! " * 200
    chunks = estimate_chunks(long_text, chunk_size=800, overlap=100)
    assert chunks >= 2


def test_sync_result_log_formatting():
    result = SyncResult(
        added_count=5,
        updated_count=2,
        skipped_count=30,
        total_remote_files=37,
        total_chunks=115,
    )
    log_str = result.to_log_string()
    assert "Added: 5" in log_str
    assert "Updated: 2" in log_str
    assert "Skipped: 30" in log_str
    assert "Total Active Files: 37" in log_str
    assert "Total Estimated Chunks: 115" in log_str


@patch("google.genai.Client")
def test_gemini_assistant_provider_sync_delta(mock_client_cls, tmp_path: Path):
    mock_client = MagicMock()
    mock_client_cls.return_value = mock_client

    # Mock file upload return
    mock_uploaded = MagicMock()
    mock_uploaded.name = "files/mock123"
    mock_client.files.upload.return_value = mock_uploaded

    provider = GeminiAssistantProvider(api_key="mock-key", model_name="gemini-2.5-flash")

    f1 = tmp_path / "art1.md"
    f1.write_text("# Article 1", encoding="utf-8")
    f2 = tmp_path / "art2.md"
    f2.write_text("# Article 2", encoding="utf-8")

    added_item = ArticleDelta(
        article_id=101,
        action="ADDED",
        slug="art1",
        title="Art 1",
        url="https://support.optisigns.com/101",
        updated_at="2026-01-01T00:00:00Z",
        content_hash="h1",
        file_path=f1,
        content="# Article 1",
    )
    updated_item = ArticleDelta(
        article_id=102,
        action="UPDATED",
        slug="art2",
        title="Art 2",
        url="https://support.optisigns.com/102",
        updated_at="2026-02-01T00:00:00Z",
        content_hash="h2",
        file_path=f2,
        content="# Article 2",
        old_remote_file_id="files/old456",
    )

    summary = DeltaSummary(
        total_scraped=2,
        added=[added_item],
        updated=[updated_item],
        skipped=[],
    )

    state = {"version": 1, "articles": {}}
    result = provider.sync_delta(summary, state)

    assert result.added_count == 1
    assert result.updated_count == 1
    assert result.skipped_count == 0

    # Verify delete was called for old file
    mock_client.files.delete.assert_called_once_with(name="files/old456")

    # Verify upload was called twice (1 chunk each for tiny test content)
    assert mock_client.files.upload.call_count == 2

    # Verify state was updated with chunk_file_ids list
    assert state["articles"]["101"]["chunk_file_ids"] == ["files/mock123"]
    assert state["articles"]["102"]["chunk_file_ids"] == ["files/mock123"]
    # remote_file_id should point to first chunk for backward compat
    assert state["articles"]["101"]["remote_file_id"] == "files/mock123"
    assert state["articles"]["102"]["remote_file_id"] == "files/mock123"


def test_get_assistant_provider_returns_gemini():
    with patch("src.assistant.GEMINI_API_KEY", "dummy-key"):
        with patch("google.genai.Client"):
            provider = get_assistant_provider()
            assert isinstance(provider, GeminiAssistantProvider)
