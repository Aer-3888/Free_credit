"""Tests for MLH scraper — written FIRST (TDD red phase)."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
import respx

from src.models import Event
from src.scrapers.mlh import MLHScraper

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def mlh_html() -> str:
    return (FIXTURES / "mlh_events.html").read_text()


@pytest.fixture
def scraper() -> MLHScraper:
    return MLHScraper()


# ── Parsing ────────────────────────────────────────────────────


@respx.mock
async def test_parse_returns_correct_number_of_events(scraper: MLHScraper, mlh_html: str):
    """Fixture HTML contains 3 events; scraper should return exactly 3."""
    respx.get("https://www.mlh.com/seasons/2026/events").mock(
        return_value=httpx.Response(200, text=mlh_html),
    )
    events = await scraper.scrape()
    assert len(events) == 3


@respx.mock
async def test_events_have_required_fields(scraper: MLHScraper, mlh_html: str):
    """Every event must have title, url, dates, and location."""
    respx.get("https://www.mlh.com/seasons/2026/events").mock(
        return_value=httpx.Response(200, text=mlh_html),
    )
    events = await scraper.scrape()
    for event in events:
        assert isinstance(event, Event)
        assert event.title
        assert event.url
        assert event.start_date  # raw date string is fine
        assert event.location


@respx.mock
async def test_first_event_fields(scraper: MLHScraper, mlh_html: str):
    """Verify concrete values for the first fixture event."""
    respx.get("https://www.mlh.com/seasons/2026/events").mock(
        return_value=httpx.Response(200, text=mlh_html),
    )
    events = await scraper.scrape()
    first = events[0]
    assert first.title == "Cloud Hack 2026"
    assert "events.mlh.io" in first.url
    assert first.location == "San Francisco, CA"


@respx.mock
async def test_source_and_id_format(scraper: MLHScraper, mlh_html: str):
    """source must be 'mlh' and id must follow 'mlh:<slug>' pattern."""
    respx.get("https://www.mlh.com/seasons/2026/events").mock(
        return_value=httpx.Response(200, text=mlh_html),
    )
    events = await scraper.scrape()
    for event in events:
        assert event.source == "mlh"
        assert event.id.startswith("mlh:")
        slug = event.id.split(":", 1)[1]
        assert slug  # non-empty


@respx.mock
async def test_event_ids_are_unique(scraper: MLHScraper, mlh_html: str):
    """No duplicate IDs."""
    respx.get("https://www.mlh.com/seasons/2026/events").mock(
        return_value=httpx.Response(200, text=mlh_html),
    )
    events = await scraper.scrape()
    ids = [e.id for e in events]
    assert len(ids) == len(set(ids))


# ── Edge cases ─────────────────────────────────────────────────


@respx.mock
async def test_empty_page_returns_empty_list(scraper: MLHScraper):
    """An HTML page with no event wrappers should yield an empty list."""
    empty_html = "<html><body><div id='search-results'></div></body></html>"
    respx.get("https://www.mlh.com/seasons/2026/events").mock(
        return_value=httpx.Response(200, text=empty_html),
    )
    events = await scraper.scrape()
    assert events == []


@respx.mock
async def test_network_error_returns_empty_list(scraper: MLHScraper):
    """A network failure must not crash — should return empty list."""
    respx.get("https://www.mlh.com/seasons/2026/events").mock(
        side_effect=httpx.ConnectError("Connection refused"),
    )
    events = await scraper.scrape()
    assert events == []


@respx.mock
async def test_server_error_returns_empty_list(scraper: MLHScraper):
    """HTTP 500 must not crash — should return empty list."""
    respx.get("https://www.mlh.com/seasons/2026/events").mock(
        return_value=httpx.Response(500, text="Internal Server Error"),
    )
    events = await scraper.scrape()
    assert events == []
