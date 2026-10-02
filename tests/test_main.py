"""Unit tests for the main orchestration pipeline."""
from unittest.mock import MagicMock, patch
from main import run_pipeline
from src.assistant import SyncResult
from src.delta import DeltaSummary


@patch("main.fetch_articles")
@patch("main.load_sync_state")
@patch("main.evaluate_delta")
@patch("main.get_assistant_provider")
@patch("main.save_sync_state")
def test_run_pipeline_success(
    mock_save_state,
    mock_get_provider,
    mock_eval_delta,
    mock_load_state,
    mock_fetch_articles,
):
    mock_fetch_articles.return_value = [{"id": 1, "name": "Test Art"}]
    mock_load_state.return_value = {"version": 1, "articles": {}}
    mock_eval_delta.return_value = DeltaSummary(total_scraped=1)

    mock_provider = MagicMock()
    mock_provider.sync_delta.return_value = SyncResult(
        added_count=1,
        updated_count=0,
        skipped_count=0,
        total_remote_files=1,
        total_chunks=3,
    )
    mock_get_provider.return_value = mock_provider

    exit_code = run_pipeline()
    assert exit_code == 0
    mock_save_state.assert_called_once()


@patch("main.fetch_articles")
def test_run_pipeline_no_articles_aborts(mock_fetch_articles):
    mock_fetch_articles.return_value = []
    exit_code = run_pipeline()
    assert exit_code == 1


@patch("main.fetch_articles")
def test_run_pipeline_exception_handled(mock_fetch_articles):
    mock_fetch_articles.side_effect = RuntimeError("Network timeout")
    exit_code = run_pipeline()
    assert exit_code == 1
