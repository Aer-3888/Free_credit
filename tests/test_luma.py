"""Tests for Luma HTML scraper — written FIRST (TDD red phase).

The old API-based scraper is replaced with a free HTML scraper of
Luma's public discovery page.  No API key required.
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
import respx

from src.models import Event
from src.scrapers.luma import LumaScraper

FIXTURES = Path(__file__).parent / "fixtures"

DISCOVER_URL = "https://lu.ma/discover"


def _load_fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


@pytest.fixture()
def discovery_html() -> str:
    return _load_fixture("luma_discovery.html")


@pytest.fixture()
def scraper() -> LumaScraper:
    return LumaScraper()


# ── Parsing ──────────────────────────────────────────────────────


@respx.mock
async def test_parse_returns_correct_number_of_events(
    scraper: LumaScraper, discovery_html: str
):
    """Fixture HTML has 3 event cards; scraper returns 3 events."""
    respx.get(DISCOVER_URL).mock(
        return_value=httpx.Response(200, text=discovery_html),
    )
    events = await scraper.scrape()
    assert len(events) == 3


@respx.mock
async def test_event_fields_extracted_correctly(
    scraper: LumaScraper, discovery_html: str
):
    """Verify title, url, dates, location for each parsed event."""
    respx.get(DISCOVER_URL).mock(
        return_value=httpx.Response(200, text=discovery_html),
    )
    events = await scraper.scrape()
    by_id = {e.id: e for e in events}

    # First event — full date range
    first = by_id["luma:ai-hackathon-sf-2026"]
    assert first.title == "AI Hackathon SF 2026"
    assert first.url == "https://lu.ma/ai-hackathon-sf-2026"
    assert first.start_date == "2026-02-15T09:00:00-08:00"
    assert first.end_date == "2026-02-16T18:00:00-08:00"
    assert first.location == "San Francisco, CA"
    assert first.organizer == "TechCrunch Events"

    # Second event — single date, online
    second = by_id["luma:cloud-credits-workshop"]
    assert second.title == "Cloud Credits Workshop"
    assert second.url == "https://lu.ma/cloud-credits-workshop"
    assert second.start_date == "2026-03-01T10:00:00-05:00"
    assert second.end_date is None
    assert second.location == "Online"
    assert second.organizer == "Dev Community"

    # Third event
    third = by_id["luma:oss-hack-night"]
    assert third.title == "Open Source Hack Night"
    assert third.url == "https://lu.ma/oss-hack-night"
    assert third.location == "Austin, TX"
    assert third.organizer == "Indie Hackers Austin"


@respx.mock
async def test_source_and_id_format(
    scraper: LumaScraper, discovery_html: str
):
    """source='luma', id='luma:<event-slug>'."""
    respx.get(DISCOVER_URL).mock(
        return_value=httpx.Response(200, text=discovery_html),
    )
    events = await scraper.scrape()
    for event in events:
        assert event.source == "luma"
        assert event.id.startswith("luma:")
        slug = event.id.split(":", 1)[1]
        assert slug  # non-empty


# ── Edge cases ───────────────────────────────────────────────────


@respx.mock
async def test_empty_page_returns_empty_list(scraper: LumaScraper):
    """A page with no event cards returns an empty list."""
    empty_html = (
        '<!DOCTYPE html><html><body><div id="__next">'
        "<main><h1>Discover Events</h1></main>"
        "</div></body></html>"
    )
    respx.get(DISCOVER_URL).mock(
        return_value=httpx.Response(200, text=empty_html),
    )
    events = await scraper.scrape()
    assert events == []


# ── Error handling ───────────────────────────────────────────────


@respx.mock
async def test_network_error_returns_empty_list(scraper: LumaScraper):
    """Network failure should yield empty list, not crash."""
    respx.get(DISCOVER_URL).mock(
        side_effect=httpx.ConnectError("Connection refused"),
    )
    events = await scraper.scrape()
    assert events == []


@respx.mock
async def test_server_error_returns_empty_list(scraper: LumaScraper):
    """HTTP 500 should yield empty list, not crash."""
    respx.get(DISCOVER_URL).mock(
        return_value=httpx.Response(500, text="Internal Server Error"),
    )
    events = await scraper.scrape()
    assert events == []


# ── No API key needed ────────────────────────────────────────────


@respx.mock
async def test_no_api_key_needed(discovery_html: str):
    """Scraper works without any LUMA_API_KEY env var."""
    respx.get(DISCOVER_URL).mock(
        return_value=httpx.Response(200, text=discovery_html),
    )
    # Construct without any env manipulation — no key needed
    scraper = LumaScraper()
    events = await scraper.scrape()
    assert len(events) == 3
