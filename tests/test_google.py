"""Tests for the Google Search scraper.

Written FIRST (TDD RED phase).
"""

from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
import pytest
import respx

from src.models import Event
from src.scrapers.google_search import GoogleSearchScraper, QUERIES

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(autouse=True)
def _fast_tests(monkeypatch):
    """Eliminate asyncio.sleep delays and DNS checks so tests run instantly."""
    monkeypatch.setattr(asyncio, "sleep", AsyncMock())
    # Skip DNS-based SSRF checks (no real network in tests)
    monkeypatch.setattr("src.scrapers.base._is_private_ip", lambda _host: False)


def _load_fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def _google_url(query: str) -> str:
    """Build the Google search URL for a query (must match scraper logic)."""
    from urllib.parse import quote_plus

    return f"https://www.google.com/search?q={quote_plus(query)}&num=10"


# ---------------------------------------------------------------------------
# Test: parse fixture HTML -> correct number of unique events
# ---------------------------------------------------------------------------


@respx.mock
async def test_parse_results_correct_count():
    """Fixture has 4 results (1 duplicate URL) -> 3 unique events."""
    html = _load_fixture("google_search.html")
    scraper = GoogleSearchScraper()

    # Mock first 3 queries (the max per run) to all return the same fixture
    for query in QUERIES[:3]:
        respx.get(_google_url(query)).mock(
            return_value=httpx.Response(200, text=html),
        )

    events = await scraper.scrape()

    # The fixture has 3 unique URLs across all queries (deduplication across queries)
    assert len(events) == 3


# ---------------------------------------------------------------------------
# Test: each event has required fields
# ---------------------------------------------------------------------------


@respx.mock
async def test_event_fields():
    """Each Event has title, url, description, source, and correct ID format."""
    html = _load_fixture("google_search.html")
    scraper = GoogleSearchScraper()

    for query in QUERIES[:3]:
        respx.get(_google_url(query)).mock(
            return_value=httpx.Response(200, text=html),
        )

    events = await scraper.scrape()

    for event in events:
        assert isinstance(event, Event)
        assert event.source == "google"
        assert event.title != ""
        assert event.url.startswith("http")
        assert event.description != ""
        assert event.organizer == "(via Google)"
        # ID format: google:{md5 of url}
        expected_id = "google:" + hashlib.md5(event.url.encode()).hexdigest()
        assert event.id == expected_id


# ---------------------------------------------------------------------------
# Test: specific event values from fixture
# ---------------------------------------------------------------------------


@respx.mock
async def test_specific_event_values():
    """Verify specific titles and URLs from the fixture."""
    html = _load_fixture("google_search.html")
    scraper = GoogleSearchScraper()

    for query in QUERIES[:3]:
        respx.get(_google_url(query)).mock(
            return_value=httpx.Response(200, text=html),
        )

    events = await scraper.scrape()
    titles = {e.title for e in events}
    urls = {e.url for e in events}

    assert "AWS Hackathon 2026 - Free Bedrock Credits for All Participants" in titles
    assert "Azure AI Workshop - Free Credits for Participants" in titles
    assert "GCP Cloud Hack 2026 - Free Google Cloud Credits" in titles
    assert "https://awshackathon2026.example.com/register" in urls
    assert "https://azure-ai-workshop.example.com/" in urls
    assert "https://gcphack.example.com/2026" in urls


# ---------------------------------------------------------------------------
# Test: correct search queries are used
# ---------------------------------------------------------------------------


def test_query_list():
    """QUERIES contains the expected search terms."""
    assert len(QUERIES) >= 3
    # Check some expected patterns
    query_text = " ".join(QUERIES)
    assert "hackathon" in query_text.lower()
    assert "credits" in query_text.lower()
    assert "workshop" in query_text.lower()


# ---------------------------------------------------------------------------
# Test: network error -> empty list, no crash
# ---------------------------------------------------------------------------


@respx.mock
async def test_network_error_returns_empty_list():
    """A network/transport error returns an empty list gracefully."""
    scraper = GoogleSearchScraper()

    for query in QUERIES[:3]:
        respx.get(_google_url(query)).mock(side_effect=httpx.ConnectError("Connection refused"))

    events = await scraper.scrape()

    assert isinstance(events, list)
    assert len(events) == 0


# ---------------------------------------------------------------------------
# Test: HTTP error (429, 503) -> empty list, no crash
# ---------------------------------------------------------------------------


@respx.mock
async def test_http_error_returns_empty_list():
    """An HTTP error status returns an empty list gracefully."""
    scraper = GoogleSearchScraper()

    for query in QUERIES[:3]:
        respx.get(_google_url(query)).mock(
            return_value=httpx.Response(429, text="Too Many Requests"),
        )

    events = await scraper.scrape()

    assert isinstance(events, list)
    assert len(events) == 0


# ---------------------------------------------------------------------------
# Test: empty results HTML -> empty list
# ---------------------------------------------------------------------------


@respx.mock
async def test_empty_results_returns_empty_list():
    """A Google results page with no results returns an empty list."""
    html = _load_fixture("google_empty.html")
    scraper = GoogleSearchScraper()

    for query in QUERIES[:3]:
        respx.get(_google_url(query)).mock(
            return_value=httpx.Response(200, text=html),
        )

    events = await scraper.scrape()

    assert isinstance(events, list)
    assert len(events) == 0


# ---------------------------------------------------------------------------
# Test: rate limiting — max 3 queries per run
# ---------------------------------------------------------------------------


@respx.mock
async def test_max_three_queries_per_run():
    """Only 3 queries are executed per scrape() call, not all 5."""
    html = _load_fixture("google_search.html")
    scraper = GoogleSearchScraper()

    # Mock ALL queries, but only 3 should be called
    routes = {}
    for query in QUERIES:
        route = respx.get(_google_url(query)).mock(
            return_value=httpx.Response(200, text=html),
        )
        routes[query] = route

    await scraper.scrape()

    call_count = sum(1 for route in routes.values() if route.called)
    assert call_count == 3


# ---------------------------------------------------------------------------
# Test: query rotation across runs
# ---------------------------------------------------------------------------


async def test_query_rotation():
    """Second scrape() call uses different queries (rotates through list)."""
    html = _load_fixture("google_search.html")
    scraper = GoogleSearchScraper()

    called_urls: list[str] = []

    async def tracking_fetch(client, url):
        called_urls.append(url)
        return httpx.Response(200, text=html)

    with patch.object(GoogleSearchScraper, "_google_fetch", staticmethod(tracking_fetch)):
        await scraper.scrape()
        first_run_urls = set(called_urls)
        assert len(first_run_urls) == 3

        called_urls.clear()
        await scraper.scrape()
        second_run_urls = set(called_urls)
        assert len(second_run_urls) == 3

    # With 5 queries and 3 per run, at least 1 query must differ
    assert first_run_urls != second_run_urls


# ---------------------------------------------------------------------------
# Test: deduplication by URL
# ---------------------------------------------------------------------------


@respx.mock
async def test_deduplication_by_url():
    """Duplicate URLs (same URL from different queries) are deduplicated."""
    html = _load_fixture("google_search.html")
    scraper = GoogleSearchScraper()

    # All 3 queries return the same HTML with the same results
    for query in QUERIES[:3]:
        respx.get(_google_url(query)).mock(
            return_value=httpx.Response(200, text=html),
        )

    events = await scraper.scrape()

    # Should be 3 unique events, not 3*4=12 (or 3*3 without intra-page dedup)
    urls = [e.url for e in events]
    assert len(urls) == len(set(urls))
    assert len(events) == 3
