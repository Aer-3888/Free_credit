"""Tests for the Reddit hackathon scraper (RSS-based).

Written FIRST (TDD RED phase) -- implementation follows.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch, AsyncMock

import httpx
import pytest
import respx

from src.models import Event
from src.scrapers.reddit import RedditScraper, _parse_rss_feed

FIXTURES = Path(__file__).parent / "fixtures"

REDDIT_BASE = "https://www.reddit.com"


def _load_fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


@pytest.fixture()
def rss_xml() -> str:
    return _load_fixture("reddit_rss.xml")


@pytest.fixture(autouse=True)
def _patch_url_validation():
    """Bypass URL validation in tests (avoid DNS resolution)."""
    with patch("src.scrapers.base.validate_url"):
        yield


@pytest.fixture(autouse=True)
def _patch_sleep():
    """Eliminate async sleep delays in tests."""
    with patch("src.scrapers.base.asyncio.sleep", new_callable=AsyncMock):
        yield


# ---------------------------------------------------------------------------
# Unit tests: RSS parsing
# ---------------------------------------------------------------------------

def test_parse_rss_returns_correct_count(rss_xml: str):
    events = _parse_rss_feed(rss_xml)
    assert len(events) == 4


def test_parse_rss_event_fields(rss_xml: str):
    events = _parse_rss_feed(rss_xml)
    for event in events:
        assert isinstance(event, Event)
        assert event.source == "reddit"
        assert event.title
        assert event.url
        assert event.id.startswith("reddit:")


def test_parse_rss_specific_values(rss_xml: str):
    events = _parse_rss_feed(rss_xml)
    by_id = {e.id: e for e in events}

    aws = by_id["reddit:abc123"]
    assert "AWS Hackathon" in aws.title
    assert "Bedrock" in aws.title
    assert aws.organizer == "/u/clouddev42"
    assert "hackathons/comments/abc123" in aws.url

    azure = by_id["reddit:def456"]
    assert "Azure" in azure.title
    assert azure.organizer == "/u/msdev"


def test_parse_rss_empty_feed():
    empty = '<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom"></feed>'
    events = _parse_rss_feed(empty)
    assert events == []


# ---------------------------------------------------------------------------
# Integration tests: full scraper
# ---------------------------------------------------------------------------

@respx.mock
async def test_scrape_returns_events(rss_xml: str):
    respx.get(url__startswith=REDDIT_BASE).mock(
        return_value=httpx.Response(200, text=rss_xml),
    )
    scraper = RedditScraper()
    events = await scraper.scrape()
    assert len(events) == 4


@respx.mock
async def test_scrape_deduplicates(rss_xml: str):
    """Same posts from multiple endpoints are deduplicated."""
    respx.get(url__startswith=REDDIT_BASE).mock(
        return_value=httpx.Response(200, text=rss_xml),
    )
    scraper = RedditScraper()
    events = await scraper.scrape()
    ids = [e.id for e in events]
    assert len(ids) == len(set(ids))


@respx.mock
async def test_scrape_network_error_returns_empty():
    respx.get(url__startswith=REDDIT_BASE).mock(
        side_effect=httpx.ConnectError("refused"),
    )
    scraper = RedditScraper()
    events = await scraper.scrape()
    assert events == []


@respx.mock
async def test_scrape_searches_multiple_subreddits():
    called_urls: list[str] = []

    def _record(request: httpx.Request) -> httpx.Response:
        called_urls.append(str(request.url))
        return httpx.Response(200, text='<feed xmlns="http://www.w3.org/2005/Atom"></feed>')

    respx.get(url__startswith=REDDIT_BASE).mock(side_effect=_record)

    scraper = RedditScraper()
    await scraper.scrape()

    all_urls = " ".join(called_urls)
    assert "/r/hackathons/" in all_urls
    assert "/r/aws/" in all_urls
    assert "/r/MachineLearning/" in all_urls
    assert "/r/artificial/" in all_urls


def test_scraper_name():
    assert RedditScraper().name == "reddit"
