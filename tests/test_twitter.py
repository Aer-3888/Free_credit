"""Tests for the Twitter/Nitter scraper.

Written FIRST (TDD RED phase) — the implementation does not yet exist.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch, AsyncMock

import httpx
import pytest
import respx

from src.models import Event
from src.scrapers.twitter import TwitterScraper, NITTER_INSTANCES, QUERIES, _parse_tweets

FIXTURES = Path(__file__).parent / "fixtures"


def _load_fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def _search_url(instance: str, query: str) -> str:
    """Build the expected Nitter search URL."""
    encoded = query.replace(" ", "+")
    return f"{instance}/search?f=tweets&q={encoded}"


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------

@pytest.fixture()
def nitter_html() -> str:
    return _load_fixture("nitter_search.html")


@pytest.fixture(autouse=True)
def _patch_sleep():
    """Eliminate async sleep delays in tests."""
    with patch("src.scrapers.twitter.asyncio.sleep", new_callable=AsyncMock):
        yield


# ---------------------------------------------------------------------------
# Test: parse fixture HTML → correct number of tweets
# ---------------------------------------------------------------------------

def test_parse_tweets_count(nitter_html: str):
    """Parsing the fixture HTML extracts all 4 tweets."""
    events = _parse_tweets(nitter_html, NITTER_INSTANCES[0])
    assert len(events) == 4


# ---------------------------------------------------------------------------
# Test: parsed events have correct fields
# ---------------------------------------------------------------------------

def test_parse_tweets_fields(nitter_html: str):
    """Each parsed event has correct id, source, title, url, organizer, description."""
    events = _parse_tweets(nitter_html, NITTER_INSTANCES[0])

    # First tweet — AWS hackathon
    aws = events[0]
    assert aws.source == "twitter"
    assert aws.id == "twitter:1234567890"
    assert aws.url == f"{NITTER_INSTANCES[0]}/awscloud/status/1234567890"
    assert aws.organizer == "@awscloud"
    assert "AWS Cloud Hackathon 2026" in aws.description
    # Title is first 100 chars of tweet text
    assert len(aws.title) <= 100
    assert aws.title == aws.description[:100].strip()

    # Second tweet — Azure workshop
    azure = events[1]
    assert azure.id == "twitter:9876543210"
    assert azure.organizer == "@MSFTAzure"
    assert "Azure OpenAI credits" in azure.description

    # Third tweet — Bedrock
    bedrock = events[2]
    assert bedrock.id == "twitter:1111222233"
    assert bedrock.organizer == "@bedaborin"
    assert "Bedrock" in bedrock.description

    # Fourth tweet — unrelated
    unrelated = events[3]
    assert unrelated.id == "twitter:5555666677"
    assert "morning coffee" in unrelated.description


def test_parse_tweets_event_location(nitter_html: str):
    """All twitter events default to 'Online' location."""
    events = _parse_tweets(nitter_html, NITTER_INSTANCES[0])
    for event in events:
        assert event.location == "Online"


def test_parse_tweets_empty_html():
    """Parsing empty/minimal HTML returns an empty list."""
    events = _parse_tweets("<html><body></body></html>", NITTER_INSTANCES[0])
    assert events == []


# ---------------------------------------------------------------------------
# Test: full scrape with mocked Nitter — primary instance succeeds
# ---------------------------------------------------------------------------

@respx.mock
async def test_scrape_primary_instance_success(nitter_html: str):
    """Scraper uses primary Nitter instance and returns events."""
    primary = NITTER_INSTANCES[0]

    # Mock all queries on the primary instance
    for query in QUERIES:
        url = _search_url(primary, query)
        respx.get(url).mock(return_value=httpx.Response(200, text=nitter_html))

    scraper = TwitterScraper()
    events = await scraper.scrape()

    assert len(events) > 0
    assert all(isinstance(e, Event) for e in events)
    assert all(e.source == "twitter" for e in events)


# ---------------------------------------------------------------------------
# Test: primary fails → fallback to secondary instance
# ---------------------------------------------------------------------------

@respx.mock
async def test_scrape_fallback_on_primary_failure(nitter_html: str):
    """When primary Nitter instance fails, scraper tries the fallback."""
    primary = NITTER_INSTANCES[0]
    fallback = NITTER_INSTANCES[1]

    # Primary returns 500 for all queries
    for query in QUERIES:
        respx.get(_search_url(primary, query)).mock(
            return_value=httpx.Response(500),
        )

    # Fallback succeeds
    for query in QUERIES:
        respx.get(_search_url(fallback, query)).mock(
            return_value=httpx.Response(200, text=nitter_html),
        )

    scraper = TwitterScraper()
    events = await scraper.scrape()

    assert len(events) > 0
    assert all(e.source == "twitter" for e in events)


# ---------------------------------------------------------------------------
# Test: all instances fail → empty list (no crash)
# ---------------------------------------------------------------------------

@respx.mock
async def test_scrape_all_instances_fail():
    """When all Nitter instances fail, returns empty list gracefully."""
    for instance in NITTER_INSTANCES:
        for query in QUERIES:
            respx.get(_search_url(instance, query)).mock(
                return_value=httpx.Response(500),
            )

    scraper = TwitterScraper()
    events = await scraper.scrape()

    assert isinstance(events, list)
    assert len(events) == 0


# ---------------------------------------------------------------------------
# Test: deduplication by tweet ID
# ---------------------------------------------------------------------------

@respx.mock
async def test_scrape_deduplicates_by_tweet_id(nitter_html: str):
    """Same tweets returned by multiple queries are deduplicated."""
    primary = NITTER_INSTANCES[0]

    # All queries return the same fixture (same tweet IDs)
    for query in QUERIES:
        respx.get(_search_url(primary, query)).mock(
            return_value=httpx.Response(200, text=nitter_html),
        )

    scraper = TwitterScraper()
    events = await scraper.scrape()

    # Should have at most 4 unique tweets (the fixture has 4)
    tweet_ids = [e.id for e in events]
    assert len(tweet_ids) == len(set(tweet_ids))
    assert len(events) == 4


# ---------------------------------------------------------------------------
# Test: search queries are well-formed
# ---------------------------------------------------------------------------

def test_search_queries_are_nonempty():
    """All search queries are non-empty strings."""
    assert len(QUERIES) > 0
    for q in QUERIES:
        assert isinstance(q, str)
        assert len(q.strip()) > 0


# ---------------------------------------------------------------------------
# Test: scraper name attribute
# ---------------------------------------------------------------------------

def test_scraper_name():
    """TwitterScraper.name is 'twitter'."""
    scraper = TwitterScraper()
    assert scraper.name == "twitter"


# ---------------------------------------------------------------------------
# Test: connection error (not just HTTP error) → try fallback
# ---------------------------------------------------------------------------

@respx.mock
async def test_scrape_connection_error_tries_fallback(nitter_html: str):
    """A connection error on primary triggers fallback usage."""
    primary = NITTER_INSTANCES[0]
    fallback = NITTER_INSTANCES[1]

    # Primary raises connection error
    for query in QUERIES:
        respx.get(_search_url(primary, query)).mock(
            side_effect=httpx.ConnectError("Connection refused"),
        )

    # Fallback succeeds
    for query in QUERIES:
        respx.get(_search_url(fallback, query)).mock(
            return_value=httpx.Response(200, text=nitter_html),
        )

    scraper = TwitterScraper()
    events = await scraper.scrape()

    assert len(events) > 0


# ---------------------------------------------------------------------------
# Test: max 20 tweets per query cap
# ---------------------------------------------------------------------------

def test_parse_tweets_respects_max_cap():
    """_parse_tweets returns at most 20 events even with more in HTML."""
    # Build HTML with 25 timeline items
    items = []
    for i in range(25):
        items.append(f"""
        <div class="timeline-item">
            <div class="tweet-header">
                <a class="username" href="/user{i}">@user{i}</a>
                <span class="tweet-date">
                    <a href="/user{i}/status/{7000000000 + i}">Apr 1, 2026</a>
                </span>
            </div>
            <a class="tweet-link" href="/user{i}/status/{7000000000 + i}"></a>
            <div class="tweet-content">Tweet number {i} about some event.</div>
        </div>
        """)
    html = f"<html><body><div class='timeline'>{''.join(items)}</div></body></html>"

    events = _parse_tweets(html, NITTER_INSTANCES[0])
    assert len(events) == 20
