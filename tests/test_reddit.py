"""Tests for the Reddit hackathon scraper.

Written FIRST (TDD RED phase) -- implementation follows.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch, AsyncMock

import httpx
import pytest
import respx

from src.models import Event
from src.scrapers.reddit import RedditScraper

FIXTURES = Path(__file__).parent / "fixtures"


def _load_fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def _fixture_json() -> dict:
    return json.loads(_load_fixture("reddit_response.json"))


# ---------------------------------------------------------------------------
# URL helpers
# ---------------------------------------------------------------------------

REDDIT_BASE = "https://www.reddit.com"


def _new_url(subreddit: str) -> str:
    return f"{REDDIT_BASE}/r/{subreddit}/new.json"


def _search_url(subreddit: str) -> str:
    return f"{REDDIT_BASE}/r/{subreddit}/search.json"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

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


@pytest.fixture()
def reddit_json() -> dict:
    return _fixture_json()


# ---------------------------------------------------------------------------
# Test: parse fixture JSON -> correct number of events
# ---------------------------------------------------------------------------

@respx.mock
async def test_parse_fixture_returns_correct_event_count(reddit_json: dict):
    """Parsing the fixture response produces 4 events."""
    # Mock all endpoints the scraper will hit -- new + search queries per subreddit
    respx.get(url__startswith=REDDIT_BASE).mock(
        return_value=httpx.Response(200, json=reddit_json),
    )

    scraper = RedditScraper()
    events = await scraper.scrape()

    # 4 unique post IDs in the fixture; dedup keeps them to 4
    assert len(events) == 4


# ---------------------------------------------------------------------------
# Test: each event has required fields
# ---------------------------------------------------------------------------

@respx.mock
async def test_events_have_required_fields(reddit_json: dict):
    """Every returned event has title, url, description, source='reddit'."""
    respx.get(url__startswith=REDDIT_BASE).mock(
        return_value=httpx.Response(200, json=reddit_json),
    )

    scraper = RedditScraper()
    events = await scraper.scrape()

    for event in events:
        assert isinstance(event, Event)
        assert event.source == "reddit"
        assert event.title  # non-empty
        assert event.url  # non-empty
        assert isinstance(event.description, str)


# ---------------------------------------------------------------------------
# Test: event ID format is "reddit:{post_id}"
# ---------------------------------------------------------------------------

@respx.mock
async def test_event_id_format(reddit_json: dict):
    """Event IDs follow the 'reddit:{post_id}' pattern."""
    respx.get(url__startswith=REDDIT_BASE).mock(
        return_value=httpx.Response(200, json=reddit_json),
    )

    scraper = RedditScraper()
    events = await scraper.scrape()

    event_ids = {e.id for e in events}
    assert "reddit:abc123" in event_ids
    assert "reddit:def456" in event_ids
    assert "reddit:ghi789" in event_ids
    assert "reddit:jkl012" in event_ids

    for event in events:
        assert event.id.startswith("reddit:")


# ---------------------------------------------------------------------------
# Test: external URL used when available, permalink otherwise
# ---------------------------------------------------------------------------

@respx.mock
async def test_external_url_vs_permalink(reddit_json: dict):
    """Posts with external URLs use that; self posts use the reddit permalink."""
    respx.get(url__startswith=REDDIT_BASE).mock(
        return_value=httpx.Response(200, json=reddit_json),
    )

    scraper = RedditScraper()
    events = await scraper.scrape()

    by_id = {e.id: e for e in events}

    # abc123 has an external URL (is_self=false)
    aws_event = by_id["reddit:abc123"]
    assert aws_event.url == "https://devpost.com/some-aws-hackathon"

    # def456 is a self post -> permalink
    azure_event = by_id["reddit:def456"]
    assert azure_event.url == "https://www.reddit.com/r/hackathons/comments/def456/azure_ai_hackathon_free_openai_api_credits/"


# ---------------------------------------------------------------------------
# Test: selftext mapped to description, author to organizer
# ---------------------------------------------------------------------------

@respx.mock
async def test_description_and_organizer_mapping(reddit_json: dict):
    """selftext -> description, author -> organizer."""
    respx.get(url__startswith=REDDIT_BASE).mock(
        return_value=httpx.Response(200, json=reddit_json),
    )

    scraper = RedditScraper()
    events = await scraper.scrape()

    by_id = {e.id: e for e in events}

    aws_event = by_id["reddit:abc123"]
    assert "10K in AWS credits" in aws_event.description
    assert aws_event.organizer == "aws_evangelist"

    azure_event = by_id["reddit:def456"]
    assert "Azure" in azure_event.description
    assert azure_event.organizer == "ms_developer_rel"


# ---------------------------------------------------------------------------
# Test: network error -> empty list (no crash)
# ---------------------------------------------------------------------------

@respx.mock
async def test_network_error_returns_empty_list():
    """Network errors are handled gracefully and produce an empty list."""
    respx.get(url__startswith=REDDIT_BASE).mock(
        side_effect=httpx.ConnectError("Connection refused"),
    )

    scraper = RedditScraper()
    events = await scraper.scrape()

    assert isinstance(events, list)
    assert len(events) == 0


# ---------------------------------------------------------------------------
# Test: empty response -> empty list
# ---------------------------------------------------------------------------

@respx.mock
async def test_empty_response_returns_empty_list():
    """An empty Reddit listing produces an empty list."""
    empty_response = {"kind": "Listing", "data": {"children": [], "after": None, "dist": 0}}
    respx.get(url__startswith=REDDIT_BASE).mock(
        return_value=httpx.Response(200, json=empty_response),
    )

    scraper = RedditScraper()
    events = await scraper.scrape()

    assert isinstance(events, list)
    assert len(events) == 0


# ---------------------------------------------------------------------------
# Test: searches multiple subreddits
# ---------------------------------------------------------------------------

@respx.mock
async def test_searches_multiple_subreddits():
    """The scraper queries r/hackathons, r/aws, r/MachineLearning, r/artificial."""
    called_urls: list[str] = []

    def _record_and_respond(request: httpx.Request) -> httpx.Response:
        called_urls.append(str(request.url))
        empty = {"kind": "Listing", "data": {"children": [], "after": None, "dist": 0}}
        return httpx.Response(200, json=empty)

    respx.get(url__startswith=REDDIT_BASE).mock(side_effect=_record_and_respond)

    scraper = RedditScraper()
    await scraper.scrape()

    # Verify each expected subreddit was contacted
    all_urls = " ".join(called_urls)
    assert "/r/hackathons/" in all_urls
    assert "/r/aws/" in all_urls
    assert "/r/MachineLearning/" in all_urls
    assert "/r/artificial/" in all_urls


# ---------------------------------------------------------------------------
# Test: deduplication across subreddits
# ---------------------------------------------------------------------------

@respx.mock
async def test_deduplication_across_subreddits(reddit_json: dict):
    """Same post seen in multiple subreddits is only returned once."""
    # All endpoints return the same fixture (same post IDs)
    respx.get(url__startswith=REDDIT_BASE).mock(
        return_value=httpx.Response(200, json=reddit_json),
    )

    scraper = RedditScraper()
    events = await scraper.scrape()

    # Should still be 4 unique events despite being returned by every endpoint
    ids = [e.id for e in events]
    assert len(ids) == len(set(ids))
    assert len(events) == 4


# ---------------------------------------------------------------------------
# Test: malformed JSON -> empty list (no crash)
# ---------------------------------------------------------------------------

@respx.mock
async def test_malformed_json_returns_empty_list():
    """Invalid JSON is handled gracefully."""
    respx.get(url__startswith=REDDIT_BASE).mock(
        return_value=httpx.Response(200, text="this is not json{{{"),
    )

    scraper = RedditScraper()
    events = await scraper.scrape()

    assert isinstance(events, list)
    assert len(events) == 0


# ---------------------------------------------------------------------------
# Test: scraper name attribute
# ---------------------------------------------------------------------------

def test_scraper_name():
    """RedditScraper.name is 'reddit'."""
    scraper = RedditScraper()
    assert scraper.name == "reddit"
