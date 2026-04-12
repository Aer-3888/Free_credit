"""Tests for Luma scraper — written FIRST (TDD red phase)."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest
import respx

from src.models import Event
from src.scrapers.luma import LumaScraper

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def luma_data() -> dict:
    return json.loads((FIXTURES / "luma_response.json").read_text())


@pytest.fixture
def scraper() -> LumaScraper:
    """LumaScraper with a fake API key."""
    with patch.dict("os.environ", {"LUMA_API_KEY": "test-key-12345"}):
        return LumaScraper()


# ── Parsing ────────────────────────────────────────────────────


@respx.mock
async def test_parse_returns_correct_event_fields(scraper: LumaScraper, luma_data: dict):
    """Fixture JSON has 3 events; verify field extraction."""
    respx.get("https://api.lu.ma/public/v2/event/search").mock(
        return_value=httpx.Response(200, json=luma_data),
    )
    events = await scraper.scrape()
    # May get duplicates from multiple keyword searches; deduplicate by id
    unique = {e.id: e for e in events}
    assert len(unique) == 3

    first = unique["luma:evt-abc123def456"]
    assert first.title == "AI Hackathon SF 2026"
    assert first.url == "https://lu.ma/ai-hackathon-sf"
    assert first.start_date == "2026-02-15T09:00:00Z"
    assert first.end_date == "2026-02-16T18:00:00Z"
    assert first.location == "San Francisco"
    assert first.organizer == "TechCrunch Events"


@respx.mock
async def test_source_and_id_format(scraper: LumaScraper, luma_data: dict):
    """source='luma', id='luma:<event-api-id>'."""
    respx.get("https://api.lu.ma/public/v2/event/search").mock(
        return_value=httpx.Response(200, json=luma_data),
    )
    events = await scraper.scrape()
    for event in events:
        assert event.source == "luma"
        assert event.id.startswith("luma:")
        event_id = event.id.split(":", 1)[1]
        assert event_id.startswith("evt-")


@respx.mock
async def test_missing_optional_fields_handled(scraper: LumaScraper, luma_data: dict):
    """Events with null geo/end_date/hosts should not crash."""
    respx.get("https://api.lu.ma/public/v2/event/search").mock(
        return_value=httpx.Response(200, json=luma_data),
    )
    events = await scraper.scrape()
    unique = {e.id: e for e in events}

    # Second event has no geo_address_info
    ws = unique["luma:evt-xyz789ghi012"]
    assert ws.location == "Online"

    # Third event has no end_at and no hosts
    oss = unique["luma:evt-mno345pqr678"]
    assert oss.end_date is None
    assert oss.organizer == ""  # no hosts


# ── Error handling ────────────────────────────────────────────


@respx.mock
async def test_api_error_returns_empty_list(scraper: LumaScraper):
    """API returning 500 should yield empty list, not crash."""
    respx.get("https://api.lu.ma/public/v2/event/search").mock(
        return_value=httpx.Response(500, text="Internal Server Error"),
    )
    events = await scraper.scrape()
    assert events == []


@respx.mock
async def test_network_error_returns_empty_list(scraper: LumaScraper):
    """Network failure should yield empty list."""
    respx.get("https://api.lu.ma/public/v2/event/search").mock(
        side_effect=httpx.ConnectError("Connection refused"),
    )
    events = await scraper.scrape()
    assert events == []


async def test_no_api_key_returns_empty_list_with_warning(caplog):
    """Missing LUMA_API_KEY should log a warning and return empty list."""
    with patch.dict("os.environ", {}, clear=False):
        # Ensure LUMA_API_KEY is not set
        import os
        os.environ.pop("LUMA_API_KEY", None)

        scraper = LumaScraper()
        events = await scraper.scrape()
        assert events == []
        assert any("LUMA_API_KEY" in record.message for record in caplog.records)
