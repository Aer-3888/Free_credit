"""Integration tests for the orchestrator."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch, MagicMock

import pytest

from src.main import main, run_scraper
from src.models import Event


def _make_event(id: str, title: str = "Test Event", **kwargs) -> Event:
    defaults = dict(
        source="test",
        title=title,
        url="https://example.com",
        organizer="Test Org",
        description="AWS Activate cloud credits provided for participants",
        location="Online",
    )
    defaults.update(kwargs)
    return Event(id=id, **defaults)


class TestRunScraper:
    @pytest.mark.asyncio
    async def test_returns_events_on_success(self):
        scraper = MagicMock()
        scraper.name = "test"
        scraper.scrape = AsyncMock(return_value=[_make_event("t:1")])
        result = await run_scraper(scraper)
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_returns_empty_on_failure(self):
        scraper = MagicMock()
        scraper.name = "test"
        scraper.scrape = AsyncMock(side_effect=RuntimeError("boom"))
        result = await run_scraper(scraper)
        assert result == []


class TestMainPipeline:
    @pytest.mark.asyncio
    async def test_full_pipeline_with_new_events(self, tmp_path):
        data_file = tmp_path / "events.json"
        data_file.write_text("[]")

        events = [_make_event("devpost:aws-hack", title="AWS Hackathon with free credits")]

        mock_scraper = MagicMock()
        mock_scraper.name = "devpost"
        mock_scraper.scrape = AsyncMock(return_value=events)

        with (
            patch("src.main.DATA_PATH", str(data_file)),
            patch("src.dedup._PROJECT_ROOT", tmp_path),
            patch.dict("os.environ", {"DISCORD_WEBHOOK_URL": ""}, clear=False),
            patch("src.main.DevpostScraper", return_value=mock_scraper),
            patch("src.main.MLHScraper", return_value=MagicMock(name="mlh", scrape=AsyncMock(return_value=[]))),
            patch("src.main.LumaScraper", return_value=MagicMock(name="luma", scrape=AsyncMock(return_value=[]))),
            patch("src.main.EventbriteScraper", return_value=MagicMock(name="eventbrite", scrape=AsyncMock(return_value=[]))),
        ):
            count = await main()

        assert count >= 1
        saved = json.loads(data_file.read_text())
        assert len(saved) >= 1

    @pytest.mark.asyncio
    async def test_no_events_returns_zero(self):
        mock_scraper = MagicMock()
        mock_scraper.name = "test"
        mock_scraper.scrape = AsyncMock(return_value=[])

        with (
            patch("src.main.DevpostScraper", return_value=mock_scraper),
            patch("src.main.MLHScraper", return_value=MagicMock(name="mlh", scrape=AsyncMock(return_value=[]))),
            patch("src.main.LumaScraper", return_value=MagicMock(name="luma", scrape=AsyncMock(return_value=[]))),
            patch("src.main.EventbriteScraper", return_value=MagicMock(name="eventbrite", scrape=AsyncMock(return_value=[]))),
        ):
            count = await main()

        assert count == 0

    @pytest.mark.asyncio
    async def test_sends_discord_notification_when_configured(self, tmp_path):
        data_file = tmp_path / "events.json"
        data_file.write_text("[]")

        events = [_make_event("devpost:aws-hack", title="AWS Activate Hackathon with free credits")]

        mock_scraper = MagicMock()
        mock_scraper.name = "devpost"
        mock_scraper.scrape = AsyncMock(return_value=events)

        with (
            patch("src.main.DATA_PATH", str(data_file)),
            patch("src.dedup._PROJECT_ROOT", tmp_path),
            patch.dict("os.environ", {"DISCORD_WEBHOOK_URL": "https://discord.com/api/webhooks/test/test"}, clear=False),
            patch("src.main.send_notifications") as mock_notify,
            patch("src.main.DevpostScraper", return_value=mock_scraper),
            patch("src.main.MLHScraper", return_value=MagicMock(name="mlh", scrape=AsyncMock(return_value=[]))),
            patch("src.main.LumaScraper", return_value=MagicMock(name="luma", scrape=AsyncMock(return_value=[]))),
            patch("src.main.EventbriteScraper", return_value=MagicMock(name="eventbrite", scrape=AsyncMock(return_value=[]))),
        ):
            count = await main()

        assert count >= 1
        mock_notify.assert_called_once()

    @pytest.mark.asyncio
    async def test_scraper_failure_doesnt_crash_pipeline(self, tmp_path):
        data_file = tmp_path / "events.json"
        data_file.write_text("[]")

        good_events = [_make_event("devpost:good", title="AWS credits hackathon")]
        good_scraper = MagicMock()
        good_scraper.name = "devpost"
        good_scraper.scrape = AsyncMock(return_value=good_events)

        bad_scraper = MagicMock()
        bad_scraper.name = "mlh"
        bad_scraper.scrape = AsyncMock(side_effect=RuntimeError("network down"))

        with (
            patch("src.main.DATA_PATH", str(data_file)),
            patch("src.dedup._PROJECT_ROOT", tmp_path),
            patch.dict("os.environ", {"DISCORD_WEBHOOK_URL": ""}, clear=False),
            patch("src.main.DevpostScraper", return_value=good_scraper),
            patch("src.main.MLHScraper", return_value=bad_scraper),
            patch("src.main.LumaScraper", return_value=MagicMock(name="luma", scrape=AsyncMock(return_value=[]))),
            patch("src.main.EventbriteScraper", return_value=MagicMock(name="eventbrite", scrape=AsyncMock(return_value=[]))),
        ):
            count = await main()

        # Pipeline should still process the good scraper's events
        assert count >= 1
