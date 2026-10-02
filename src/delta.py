"""Module for calculating content hash and detecting incremental delta changes."""
import hashlib
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.config import STATE_FILE, ARTICLES_DIR
from src.scraper import format_article_markdown, generate_slug

logger = logging.getLogger(__name__)


@dataclass
class ArticleDelta:
    """Represents a single evaluated article delta item."""
    article_id: int
    action: str  # "ADDED" | "UPDATED" | "SKIPPED"
    slug: str
    title: str
    url: str
    updated_at: str
    content_hash: str
    file_path: Path
    content: str
    remote_file_id: str | None = None
    old_remote_file_id: str | None = None
    chunk_file_ids: list[str] = field(default_factory=list)
    old_chunk_file_ids: list[str] = field(default_factory=list)

    # Backward compatibility aliases
    @property
    def old_openai_file_id(self) -> str | None:
        return self.old_remote_file_id

    @property
    def openai_file_id(self) -> str | None:
        return self.remote_file_id



@dataclass
class DeltaSummary:
    """Summary of all evaluated delta articles."""
    total_scraped: int
    added: list[ArticleDelta] = field(default_factory=list)
    updated: list[ArticleDelta] = field(default_factory=list)
    skipped: list[ArticleDelta] = field(default_factory=list)

    @property
    def added_count(self) -> int:
        return len(self.added)

    @property
    def updated_count(self) -> int:
        return len(self.updated)

    @property
    def skipped_count(self) -> int:
        return len(self.skipped)

    def to_log_string(self) -> str:
        return (
            f"[DELTA SUMMARY] Scraped: {self.total_scraped} | "
            f"Added: {self.added_count} | "
            f"Updated: {self.updated_count} | "
            f"Skipped: {self.skipped_count}"
        )


def compute_content_hash(text: str) -> str:
    """Compute standard SHA-256 hash of article text content."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def load_sync_state(state_file_path: Path = STATE_FILE) -> dict[str, Any]:
    """Load existing sync state from disk or return default initial schema."""
    if state_file_path.exists():
        try:
            content = state_file_path.read_text(encoding="utf-8")
            state = json.loads(content)
            if isinstance(state, dict) and "articles" in state:
                return state
        except Exception as exc:
            logger.warning("Failed to parse state file %s (%s). Recreating initial state.", state_file_path, exc)

    return {
        "version": 1,
        "last_sync": None,
        "vector_store_id": None,
        "assistant_id": None,
        "articles": {},
    }


def save_sync_state(state: dict[str, Any], state_file_path: Path = STATE_FILE) -> None:
    """Atomically write sync state to disk with formatted indentation."""
    state_file_path.parent.mkdir(parents=True, exist_ok=True)
    temp_file = state_file_path.with_suffix(".tmp")
    state["last_sync"] = datetime.now(timezone.utc).isoformat()

    temp_file.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")
    temp_file.replace(state_file_path)


def evaluate_delta(
    articles: list[dict[str, Any]],
    state: dict[str, Any],
    output_dir: Path = ARTICLES_DIR,
) -> DeltaSummary:
    """
    Evaluate list of raw scraped articles against the previous sync state.
    Categorizes each article into ADDED, UPDATED, or SKIPPED.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    stored_articles: dict[str, Any] = state.get("articles", {})
    summary = DeltaSummary(total_scraped=len(articles))

    for article in articles:
        raw_id = article.get("id")
        if not raw_id:
            continue

        article_id = int(raw_id)
        id_str = str(article_id)
        title = article.get("name") or article.get("title") or "Untitled Article"
        url = article.get("html_url", "")
        updated_at = article.get("updated_at", "")

        slug = generate_slug(title, article_id)
        file_path = output_dir / f"{slug}.md"

        # Generate standard markdown and hash
        content = format_article_markdown(article)
        content_hash = compute_content_hash(content)

        if id_str not in stored_articles:
            # Case 1: Brand new article
            delta_item = ArticleDelta(
                article_id=article_id,
                action="ADDED",
                slug=slug,
                title=title,
                url=url,
                updated_at=updated_at,
                content_hash=content_hash,
                file_path=file_path,
                content=content,
            )
            summary.added.append(delta_item)
            # Write markdown file to disk
            file_path.write_text(content, encoding="utf-8")

        else:
            prev = stored_articles[id_str]
            prev_hash = prev.get("content_hash", "")
            prev_updated_at = prev.get("updated_at", "")
            prev_file_id = prev.get("remote_file_id") or prev.get("openai_file_id")
            # Load old chunk IDs; fall back to wrapping single remote_file_id for backward compat
            prev_chunk_ids: list[str] = prev.get("chunk_file_ids") or (
                [prev_file_id] if prev_file_id else []
            )

            if prev_hash != content_hash or prev_updated_at != updated_at:
                # Case 2: Article has changed
                delta_item = ArticleDelta(
                    article_id=article_id,
                    action="UPDATED",
                    slug=slug,
                    title=title,
                    url=url,
                    updated_at=updated_at,
                    content_hash=content_hash,
                    file_path=file_path,
                    content=content,
                    old_remote_file_id=prev_file_id,
                    old_chunk_file_ids=prev_chunk_ids,
                )
                summary.updated.append(delta_item)
                # Overwrite updated file to disk
                file_path.write_text(content, encoding="utf-8")
            else:
                # Case 3: Completely unchanged
                delta_item = ArticleDelta(
                    article_id=article_id,
                    action="SKIPPED",
                    slug=slug,
                    title=title,
                    url=url,
                    updated_at=updated_at,
                    content_hash=content_hash,
                    file_path=file_path,
                    content=content,
                    old_remote_file_id=prev_file_id,
                )
                summary.skipped.append(delta_item)

    logger.info(summary.to_log_string())
    return summary


def update_state_entry(
    state: dict[str, Any],
    delta_item: ArticleDelta,
    remote_file_id: str | None = None,
    openai_file_id: str | None = None,
    chunk_file_ids: list[str] | None = None,
) -> None:
    """Update or register an article's metadata in the state dictionary."""
    state.setdefault("articles", {})

    # Resolve chunk list; fall back to single ID for OpenAI provider (backward compat)
    resolved_chunks: list[str] = chunk_file_ids or (
        [remote_file_id or openai_file_id]
        if (remote_file_id or openai_file_id)
        else []
    )
    # Primary file ID = first chunk (or legacy single ID)
    primary_id = resolved_chunks[0] if resolved_chunks else delta_item.old_remote_file_id

    state["articles"][str(delta_item.article_id)] = {
        "slug": delta_item.slug,
        "title": delta_item.title,
        "url": delta_item.url,
        "updated_at": delta_item.updated_at,
        "content_hash": delta_item.content_hash,
        "chunk_file_ids": resolved_chunks,
        "remote_file_id": primary_id,   # Backward compat & fallback
        "openai_file_id": primary_id,   # Backward compat
    }
