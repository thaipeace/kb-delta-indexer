"""Unit tests for incremental delta engine and state tracking."""
from pathlib import Path
from src.delta import (
    compute_content_hash,
    load_sync_state,
    save_sync_state,
    evaluate_delta,
    update_state_entry,
)


def test_compute_content_hash_deterministic():
    text_a = "Hello world from OptiSigns"
    text_b = "Hello world from OptiSigns"
    text_c = "Different content"

    hash_a = compute_content_hash(text_a)
    hash_b = compute_content_hash(text_b)
    hash_c = compute_content_hash(text_c)

    assert hash_a == hash_b
    assert hash_a != hash_c
    assert len(hash_a) == 64  # SHA-256 hex string length


def test_load_and_save_sync_state(tmp_path: Path):
    state_file = tmp_path / "sync_state.json"

    # Initially missing file
    initial_state = load_sync_state(state_file)
    assert initial_state["version"] == 1
    assert initial_state["articles"] == {}
    assert initial_state["last_sync"] is None

    # Populate and save state
    initial_state["vector_store_id"] = "vs_test123"
    initial_state["assistant_id"] = "asst_test456"
    initial_state["articles"]["1001"] = {
        "slug": "test-article",
        "title": "Test Article",
        "url": "https://support.optisigns.com/hc/articles/1001",
        "updated_at": "2026-01-01T00:00:00Z",
        "content_hash": "dummyhash",
        "remote_file_id": "file-123",
        "openai_file_id": "file-123",
    }
    save_sync_state(initial_state, state_file)

    # Reload from disk
    reloaded = load_sync_state(state_file)
    assert reloaded["vector_store_id"] == "vs_test123"
    assert reloaded["assistant_id"] == "asst_test456"
    assert reloaded["last_sync"] is not None
    assert "1001" in reloaded["articles"]
    assert reloaded["articles"]["1001"]["remote_file_id"] == "file-123"
    assert reloaded["articles"]["1001"]["openai_file_id"] == "file-123"


def test_evaluate_delta_all_added_on_first_run(tmp_path: Path):
    output_dir = tmp_path / "articles"
    state = {"version": 1, "articles": {}}

    mock_articles = [
        {
            "id": 1,
            "name": "Article One",
            "html_url": "https://support.optisigns.com/1",
            "updated_at": "2026-01-01T00:00:00Z",
            "body": "<p>Content 1</p>",
        },
        {
            "id": 2,
            "name": "Article Two",
            "html_url": "https://support.optisigns.com/2",
            "updated_at": "2026-01-01T00:00:00Z",
            "body": "<p>Content 2</p>",
        },
    ]

    summary = evaluate_delta(mock_articles, state, output_dir=output_dir)
    assert summary.total_scraped == 2
    assert summary.added_count == 2
    assert summary.updated_count == 0
    assert summary.skipped_count == 0
    assert len(list(output_dir.glob("*.md"))) == 2


def test_evaluate_delta_skipped_on_second_run(tmp_path: Path):
    output_dir = tmp_path / "articles"
    mock_articles = [
        {
            "id": 1,
            "name": "Article One",
            "html_url": "https://support.optisigns.com/1",
            "updated_at": "2026-01-01T00:00:00Z",
            "body": "<p>Content 1</p>",
        }
    ]

    # First run
    state = {"version": 1, "articles": {}}
    summary1 = evaluate_delta(mock_articles, state, output_dir=output_dir)
    assert summary1.added_count == 1

    # Record to state with Gemini/provider file ID
    update_state_entry(state, summary1.added[0], remote_file_id="gemini-file-mock-1")

    # Second run with exact same article
    summary2 = evaluate_delta(mock_articles, state, output_dir=output_dir)
    assert summary2.added_count == 0
    assert summary2.updated_count == 0
    assert summary2.skipped_count == 1
    assert summary2.skipped[0].old_remote_file_id == "gemini-file-mock-1"
    assert summary2.skipped[0].old_openai_file_id == "gemini-file-mock-1"


def test_evaluate_delta_updated_when_content_changes(tmp_path: Path):
    output_dir = tmp_path / "articles"
    article_v1 = {
        "id": 50,
        "name": "YouTube Setup",
        "html_url": "https://support.optisigns.com/50",
        "updated_at": "2026-01-01T00:00:00Z",
        "body": "<p>Old instructions</p>",
    }

    state = {"version": 1, "articles": {}}
    summary1 = evaluate_delta([article_v1], state, output_dir=output_dir)
    update_state_entry(state, summary1.added[0], remote_file_id="gemini-file-v1")

    # Change content and updated_at
    article_v2 = {
        "id": 50,
        "name": "YouTube Setup",
        "html_url": "https://support.optisigns.com/50",
        "updated_at": "2026-02-01T00:00:00Z",
        "body": "<p>New instructions updated for 2026</p>",
    }

    summary2 = evaluate_delta([article_v2], state, output_dir=output_dir)
    assert summary2.added_count == 0
    assert summary2.updated_count == 1
    assert summary2.skipped_count == 0
    assert summary2.updated[0].old_remote_file_id == "gemini-file-v1"
    assert summary2.updated[0].old_openai_file_id == "gemini-file-v1"
    assert "New instructions updated for 2026" in (output_dir / "youtube-setup.md").read_text(encoding="utf-8")
