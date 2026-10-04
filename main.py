"""Entrypoint for the Knowledge Base Delta Indexer daily sync batch job."""
import logging
import sys
from datetime import datetime, timezone

from src.assistant import get_assistant_provider
from src.config import MIN_ARTICLES_COUNT, STATE_FILE
from src.delta import evaluate_delta, load_sync_state, save_sync_state
from src.scraper import fetch_articles

# Configure root logger
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger("main")


def run_pipeline() -> int:
    """
    Execute the end-to-end ingestion, delta detection, and sync pipeline.
    Returns 0 on success, non-zero on failure.
    """
    start_time = datetime.now(timezone.utc)
    logger.info("=================================================================")
    logger.info("Starting Knowledge Base Delta Sync Job at %s", start_time.isoformat())
    logger.info("=================================================================")

    try:
        # Step 1: Scrape articles from Zendesk API
        logger.info("[Step 1/4] Ingesting articles from Zendesk Help Center API...")
        raw_articles = fetch_articles()
        if not raw_articles:
            logger.error("No articles could be retrieved. Aborting sync.")
            return 1
        if len(raw_articles) < MIN_ARTICLES_COUNT:
            logger.warning("Only %d articles fetched (expected >= %d).", len(raw_articles), MIN_ARTICLES_COUNT)
        logger.info("Successfully fetched %d articles from Zendesk.", len(raw_articles))

        # Step 2: Load previous state and evaluate delta
        logger.info("[Step 2/4] Evaluating delta against state file: %s", STATE_FILE)
        state = load_sync_state(STATE_FILE)
        delta_summary = evaluate_delta(raw_articles, state)
        logger.info(delta_summary.to_log_string())

        # Step 3: Synchronize delta with AI Assistant Knowledge Base
        logger.info("[Step 3/4] Synchronizing delta with AI Assistant Knowledge Base...")
        provider = get_assistant_provider()
        sync_result = provider.sync_delta(delta_summary, state)
        logger.info(sync_result.to_log_string())

        # Step 4: Persist updated state to disk
        logger.info("[Step 4/4] Persisting state to %s...", STATE_FILE)
        save_sync_state(state, STATE_FILE)

        # Print structured job completion summary
        end_time = datetime.now(timezone.utc)
        duration_sec = (end_time - start_time).total_seconds()
        logger.info("=================================================================")
        logger.info(
            "[JOB COMPLETE] Scraped: %d | Added: %d | Updated: %d | Skipped: %d | "
            "Re-indexed: %d | Failed: %d | Embedded: %d files / %d chunks | "
            "Store total: %d files / %d chunks | Duration: %.2fs",
            len(raw_articles),
            sync_result.added_count,
            sync_result.updated_count,
            sync_result.skipped_count,
            sync_result.reindexed_count,
            sync_result.failed_count,
            sync_result.embedded_files,
            sync_result.embedded_chunks,
            sync_result.total_remote_files,
            sync_result.total_chunks,
            duration_sec,
        )
        if sync_result.failed_count:
            logger.error("%d uploads failed; they will be retried next run.", sync_result.failed_count)
            logger.info("=================================================================")
            return 1
        logger.info("Job successfully completed with exit code 0.")
        logger.info("=================================================================")
        return 0

    except Exception as exc:
        logger.exception("Fatal error during sync job execution: %s", exc)
        return 1


if __name__ == "__main__":
    exit_code = run_pipeline()
    sys.exit(exit_code)
