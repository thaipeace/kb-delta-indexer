"""Script to perform sanity check and query OptiBot grounded on the knowledge base."""
import sys
import logging
from pathlib import Path

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.assistant import get_assistant_provider
from src.delta import load_sync_state
from src.config import STATE_FILE, DOCS_DIR

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s]: %(message)s")
logger = logging.getLogger("test_bot")

DEFAULT_QUESTION = "How do I add a YouTube video?"


def run_sanity_check(question: str = DEFAULT_QUESTION) -> str:
    """Query the assistant and verify answer with citations."""
    print("=" * 70)
    print(f"QUERY: {question}")
    print("=" * 70)

    state = load_sync_state(STATE_FILE)
    if not state.get("articles"):
        logger.error("No articles found in state. Please run main.py first.")
        sys.exit(1)

    provider = get_assistant_provider()
    logger.info("Sending query to Assistant...")
    answer = provider.ask_question(question, state)

    print("\n" + "=" * 70)
    print("OPTIBOT RESPONSE:")
    print("=" * 70)
    print(answer)
    print("=" * 70 + "\n")

    # Save output to docs directory for record and evidence
    DOCS_DIR.mkdir(parents=True, exist_ok=True)
    output_file = DOCS_DIR / "sanity_test_output.txt"
    output_content = (
        f"TEST QUESTION: {question}\n"
        f"{'=' * 60}\n"
        f"{answer}\n"
    )
    output_file.write_text(output_content, encoding="utf-8")
    logger.info("Saved sanity check evidence to %s", output_file)

    return answer


if __name__ == "__main__":
    query = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_QUESTION
    run_sanity_check(query)
