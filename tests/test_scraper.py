"""Unit tests for Zendesk scraper and HTML-to-Markdown cleaner."""
from pathlib import Path
import pytest
from src.scraper import (
    clean_html,
    html_to_markdown,
    generate_slug,
    format_article_markdown,
    save_article_file,
)


def test_clean_html_strips_scripts_and_styles():
    raw_html = """
    <div>
        <script>alert('malicious')</script>
        <style>body { color: red; }</style>
        <!-- This is a secret comment -->
        <p>This is legitimate content.</p>
    </div>
    """
    cleaned = clean_html(raw_html)
    assert "<script>" not in cleaned
    assert "alert('malicious')" not in cleaned
    assert "<style>" not in cleaned
    assert "secret comment" not in cleaned
    assert "This is legitimate content." in cleaned


def test_clean_html_normalizes_relative_urls():
    raw_html = """
    <p>Check out our <a href="/hc/en-us/articles/123-setup">Setup Guide</a>.</p>
    <img src="/images/banner.png" alt="Banner" />
    <a href="https://google.com">External Link</a>
    """
    cleaned = clean_html(raw_html, base_url="https://support.optisigns.com")
    assert 'href="https://support.optisigns.com/hc/en-us/articles/123-setup"' in cleaned
    assert 'src="https://support.optisigns.com/images/banner.png"' in cleaned
    assert 'href="https://google.com"' in cleaned


def test_html_to_markdown_converts_headings_and_lists():
    raw_html = """
    <h2>Adding an App</h2>
    <p>Follow these steps:</p>
    <ul>
        <li>Go to Files/Assets</li>
        <li>Click App</li>
        <li>Select YouTube</li>
    </ul>
    """
    md = html_to_markdown(raw_html)
    assert "## Adding an App" in md
    assert "Follow these steps:" in md
    assert "* Go to Files/Assets" in md or "- Go to Files/Assets" in md
    assert "* Click App" in md or "- Click App" in md


def test_html_to_markdown_preserves_code_and_tables():
    raw_html = """
    <p>Here is sample code:</p>
    <pre><code>docker run -e API_KEY=abc my-image</code></pre>
    """
    md = html_to_markdown(raw_html)
    assert "docker run -e API_KEY=abc my-image" in md


def test_generate_slug():
    slug1 = generate_slug("How to Use YouTube with OptiSigns", 360051014713)
    assert slug1 == "how-to-use-youtube-with-optisigns"

    slug_special = generate_slug("What's New in OptiSigns? (2026 Update!)", 999)
    assert "what-s-new-in-optisigns" in slug_special or "whats-new-in-optisigns" in slug_special
    assert "/" not in slug_special
    assert "?" not in slug_special


def test_format_article_markdown():
    sample_article = {
        "id": 12345,
        "name": "Getting Started with Screens",
        "html_url": "https://support.optisigns.com/hc/en-us/articles/12345",
        "updated_at": "2026-06-01T12:00:00Z",
        "body": "<h3>Welcome</h3><p>First step is to plug in the device.</p>",
    }
    md_output = format_article_markdown(sample_article)

    # Frontmatter verification
    assert md_output.startswith("---\n")
    assert "id: 12345\n" in md_output
    assert 'title: "Getting Started with Screens"\n' in md_output
    assert "url: https://support.optisigns.com/hc/en-us/articles/12345\n" in md_output
    assert "updated_at: 2026-06-01T12:00:00Z\n" in md_output
    assert "---\n\n" in md_output

    # Heading & Citation line verification
    assert "# Getting Started with Screens\n" in md_output
    assert "Article URL: https://support.optisigns.com/hc/en-us/articles/12345\n" in md_output
    assert "### Welcome" in md_output
    assert "First step is to plug in the device." in md_output


def test_save_article_file(tmp_path: Path):
    sample_article = {
        "id": 99999,
        "name": "Test Save Article",
        "html_url": "https://support.optisigns.com/hc/en-us/articles/99999",
        "updated_at": "2026-01-01T00:00:00Z",
        "body": "<p>Content to save</p>",
    }
    file_path, content = save_article_file(sample_article, output_dir=tmp_path)
    assert file_path.exists()
    assert file_path.name == "test-save-article.md"
    assert "Article URL: https://support.optisigns.com/hc/en-us/articles/99999" in file_path.read_text(encoding="utf-8")
