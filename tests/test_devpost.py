"""Tests for the Devpost hackathon scraper.

Written FIRST (TDD RED phase) — the implementation does not yet exist.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
import respx

from src.models import Event
from src.scrapers.devpost import DevpostScraper

FIXTURES = Path(__file__).parent / "fixtures"


def _load_fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def _api_url(page: int = 1) -> str:
    return f"https://devpost.com/api/hackathons?page={page}&status=open"


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------

@pytest.fixture()
def api_json() -> dict:
    return json.loads(_load_fixture("devpost_response.json"))


@pytest.fixture()
def detail_html() -> str:
    return _load_fixture("devpost_detail.html")


# ---------------------------------------------------------------------------
# Test: parse a single hackathon → correct Event fields
# ---------------------------------------------------------------------------

@respx.mock
async def test_parse_single_hackathon(api_json: dict):
    """A single-page response with two hackathons produces correct Events."""
    # Return the fixture for the first page, empty for the second
    respx.get(_api_url(1)).mock(
        return_value=httpx.Response(200, json=api_json),
    )
    respx.get(_api_url(2)).mock(
        return_value=httpx.Response(200, json={"hackathons": [], "meta": {"current_page": 2, "per_page": 25, "total_count": 2}}),
    )
    # Mock detail page for AWS hackathon (provider keyword match triggers detail fetch)
    respx.get("https://aws-cloud-hack-2026.devpost.com").mock(
        return_value=httpx.Response(200, text=_load_fixture("devpost_detail.html")),
    )

    scraper = DevpostScraper()
    events = await scraper.scrape()

    assert len(events) == 2

    aws_event = events[0]
    assert aws_event.id == "devpost:aws-cloud-hack-2026"
    assert aws_event.source == "devpost"
    assert aws_event.title == "AWS Cloud Hack 2026"
    assert aws_event.url == "https://aws-cloud-hack-2026.devpost.com"
    assert aws_event.organizer == "Amazon Web Services"
    assert aws_event.location == "Online"
    assert aws_event.prizes == "$50,000 in prizes"
    # Sponsors fetched from detail page
    assert "Amazon Web Services" in aws_event.sponsors
    assert "MongoDB" in aws_event.sponsors
    assert "GitHub" in aws_event.sponsors

    green_event = events[1]
    assert green_event.id == "devpost:greentech-jam"
    assert green_event.title == "GreenTech Sustainability Jam"
    assert green_event.organizer == "EcoTech Foundation"
    assert green_event.location == "San Francisco, CA"


# ---------------------------------------------------------------------------
# Test: pagination across multiple pages
# ---------------------------------------------------------------------------

@respx.mock
async def test_pagination_multiple_pages(api_json: dict):
    """Scraper follows pagination until an empty page is returned."""
    page1 = api_json  # 2 hackathons
    page2_data = {
        "hackathons": [
            {
                "id": 99999,
                "title": "Page Two Hack",
                "url": "https://page-two-hack.devpost.com",
                "submission_period_dates": "Jun 01 - Jun 30, 2026",
                "displayed_location": {"icon": "globe", "location": "Online"},
                "organization_name": "Indie Dev Co",
                "open_state": "open",
                "thumbnail_url": "",
                "analytics_identifier": "page-two-hack",
                "prize_amount": "$5,000 in prizes",
                "registrations_count": 100,
                "themes": [],
                "time_left_to_submission": "80 days left",
            }
        ],
        "meta": {"current_page": 2, "per_page": 25, "total_count": 3},
    }
    page3_empty = {
        "hackathons": [],
        "meta": {"current_page": 3, "per_page": 25, "total_count": 3},
    }

    respx.get(_api_url(1)).mock(return_value=httpx.Response(200, json=page1))
    respx.get(_api_url(2)).mock(return_value=httpx.Response(200, json=page2_data))
    respx.get(_api_url(3)).mock(return_value=httpx.Response(200, json=page3_empty))

    # Mock detail page for AWS hackathon (provider keyword in title/org)
    respx.get("https://aws-cloud-hack-2026.devpost.com").mock(
        return_value=httpx.Response(200, text=_load_fixture("devpost_detail.html")),
    )

    scraper = DevpostScraper()
    events = await scraper.scrape()

    assert len(events) == 3
    titles = [e.title for e in events]
    assert "AWS Cloud Hack 2026" in titles
    assert "GreenTech Sustainability Jam" in titles
    assert "Page Two Hack" in titles


# ---------------------------------------------------------------------------
# Test: empty response → empty list
# ---------------------------------------------------------------------------

@respx.mock
async def test_empty_response():
    """An empty first page returns an empty list (no crash)."""
    respx.get(_api_url(1)).mock(
        return_value=httpx.Response(
            200,
            json={"hackathons": [], "meta": {"current_page": 1, "per_page": 25, "total_count": 0}},
        ),
    )

    scraper = DevpostScraper()
    events = await scraper.scrape()

    assert events == []


# ---------------------------------------------------------------------------
# Test: API error (500) → empty list, no crash
# ---------------------------------------------------------------------------

@respx.mock
async def test_api_error_returns_empty_list():
    """A server error on the first page returns an empty list gracefully."""
    respx.get(_api_url(1)).mock(return_value=httpx.Response(500))

    scraper = DevpostScraper()
    events = await scraper.scrape()

    assert isinstance(events, list)
    assert len(events) == 0


# ---------------------------------------------------------------------------
# Test: malformed JSON → empty list, no crash
# ---------------------------------------------------------------------------

@respx.mock
async def test_malformed_json_returns_empty_list():
    """Invalid JSON in the response is handled gracefully."""
    respx.get(_api_url(1)).mock(
        return_value=httpx.Response(200, text="this is not json{{{"),
    )

    scraper = DevpostScraper()
    events = await scraper.scrape()

    assert isinstance(events, list)
    assert len(events) == 0


# ---------------------------------------------------------------------------
# Test: extract sponsors from detail page HTML
# ---------------------------------------------------------------------------

async def test_extract_sponsors_from_detail_html(detail_html: str):
    """Sponsor names are correctly extracted from the detail page HTML."""
    scraper = DevpostScraper()
    sponsors = scraper._extract_sponsors(detail_html)

    assert isinstance(sponsors, tuple)
    assert "Amazon Web Services" in sponsors
    assert "MongoDB" in sponsors
    assert "GitHub" in sponsors
    # Ensure no empty strings sneak in
    assert all(s.strip() for s in sponsors)


async def test_extract_sponsors_empty_html():
    """An HTML page with no sponsor section returns an empty tuple."""
    scraper = DevpostScraper()
    sponsors = scraper._extract_sponsors("<html><body>No sponsors here</body></html>")

    assert sponsors == ()


# ---------------------------------------------------------------------------
# Test: _parse_hackathon produces correct Event
# ---------------------------------------------------------------------------

async def test_parse_hackathon_mapping(api_json: dict):
    """_parse_hackathon correctly maps a single JSON object to an Event."""
    scraper = DevpostScraper()
    hackathon_data = api_json["hackathons"][0]
    event = scraper._parse_hackathon(hackathon_data)

    assert isinstance(event, Event)
    assert event.id == "devpost:aws-cloud-hack-2026"
    assert event.source == "devpost"
    assert event.title == "AWS Cloud Hack 2026"
    assert event.url == "https://aws-cloud-hack-2026.devpost.com"
    assert event.organizer == "Amazon Web Services"
    assert event.location == "Online"
    assert event.prizes == "$50,000 in prizes"
    assert event.description == ""  # No description in API list response
