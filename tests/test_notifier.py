"""Tests for the Discord notification system (TDD — RED phase first)."""

from __future__ import annotations

import logging
from unittest.mock import MagicMock, patch

import pytest

from src.models import Event
from src.notifier import build_embed, build_messages, send_notifications


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_event(
    *,
    title: str = "Cloud Hack 2026",
    url: str = "https://devpost.com/cloud-hack",
    source: str = "devpost",
    organizer: str = "CloudOrg",
    description: str = "A great hackathon",
    location: str = "Nantes, France",
    start_date: str = "2026-05-15T00:00:00Z",
    end_date: str = "2026-05-17T00:00:00Z",
    credit_score: float = 0.85,
    credit_signals: tuple[str, ...] = ("AWS Activate", "cloud credits provided"),
    providers_detected: tuple[str, ...] = ("AWS", "Azure"),
) -> Event:
    return Event(
        id=f"{source}:{title.lower().replace(' ', '-')}",
        source=source,
        title=title,
        url=url,
        organizer=organizer,
        description=description,
        location=location,
        start_date=start_date,
        end_date=end_date,
        credit_score=credit_score,
        credit_signals=credit_signals,
        providers_detected=providers_detected,
    )


# ---------------------------------------------------------------------------
# build_embed
# ---------------------------------------------------------------------------

class TestBuildEmbed:
    """Tests for build_embed(event) -> dict."""

    def test_returns_dict(self):
        event = _make_event()
        result = build_embed(event)
        assert isinstance(result, dict)

    def test_title_contains_event_name(self):
        event = _make_event(title="Cloud Hack 2026")
        embed = build_embed(event)
        assert "Cloud Hack 2026" in embed["title"]

    def test_title_links_to_url(self):
        event = _make_event(url="https://devpost.com/cloud-hack")
        embed = build_embed(event)
        assert embed["url"] == "https://devpost.com/cloud-hack"

    def test_high_score_green_color(self):
        """Score > 0.7 -> green (0x00ff00)."""
        event = _make_event(credit_score=0.85)
        embed = build_embed(event)
        assert embed["color"] == 0x00FF00

    def test_medium_score_yellow_color(self):
        """Score 0.3 - 0.7 -> yellow (0xffaa00)."""
        event = _make_event(credit_score=0.5)
        embed = build_embed(event)
        assert embed["color"] == 0xFFAA00

    def test_low_score_default_color(self):
        """Score < 0.3 -> grey default (0x95a5a6)."""
        event = _make_event(credit_score=0.1)
        embed = build_embed(event)
        assert embed["color"] == 0x95A5A6

    def test_boundary_high_score_at_0_7(self):
        """Exactly 0.7 is NOT high — it's medium."""
        event = _make_event(credit_score=0.7)
        embed = build_embed(event)
        assert embed["color"] == 0xFFAA00

    def test_boundary_medium_score_at_0_3(self):
        """Exactly 0.3 is medium, not low."""
        event = _make_event(credit_score=0.3)
        embed = build_embed(event)
        assert embed["color"] == 0xFFAA00

    def test_fields_present(self):
        event = _make_event()
        embed = build_embed(event)
        field_names = [f["name"] for f in embed["fields"]]
        assert "Providers" in field_names
        assert "Score" in field_names
        assert "Signals" in field_names
        assert "Dates" in field_names
        assert "Location" in field_names
        assert "Source" in field_names

    def test_providers_field_inline(self):
        event = _make_event(providers_detected=("AWS", "Azure"))
        embed = build_embed(event)
        providers_field = _find_field(embed, "Providers")
        assert providers_field["inline"] is True
        assert "AWS" in providers_field["value"]
        assert "Azure" in providers_field["value"]

    def test_score_field_inline(self):
        event = _make_event(credit_score=0.85)
        embed = build_embed(event)
        score_field = _find_field(embed, "Score")
        assert score_field["inline"] is True
        assert "0.85" in score_field["value"]

    def test_signals_field_value(self):
        event = _make_event(credit_signals=("AWS Activate", "cloud credits provided"))
        embed = build_embed(event)
        signals_field = _find_field(embed, "Signals")
        assert "AWS Activate" in signals_field["value"]
        assert "cloud credits provided" in signals_field["value"]

    def test_dates_field_formatted(self):
        event = _make_event(
            start_date="2026-05-15T00:00:00Z",
            end_date="2026-05-17T00:00:00Z",
        )
        embed = build_embed(event)
        dates_field = _find_field(embed, "Dates")
        # Format: "May 15-17, 2026" (en-dash between days)
        assert "May" in dates_field["value"]
        assert "15" in dates_field["value"]
        assert "17" in dates_field["value"]
        assert "2026" in dates_field["value"]

    def test_dates_field_none_values(self):
        event = _make_event(start_date=None, end_date=None)
        embed = build_embed(event)
        dates_field = _find_field(embed, "Dates")
        assert dates_field["value"] == "TBD"

    def test_location_field_value(self):
        event = _make_event(location="Nantes, France")
        embed = build_embed(event)
        loc_field = _find_field(embed, "Location")
        assert loc_field["value"] == "Nantes, France"

    def test_source_field_value(self):
        event = _make_event(source="devpost")
        embed = build_embed(event)
        source_field = _find_field(embed, "Source")
        assert source_field["value"] == "devpost"

    def test_empty_providers(self):
        event = _make_event(providers_detected=())
        embed = build_embed(event)
        providers_field = _find_field(embed, "Providers")
        assert providers_field["value"] == "None detected"

    def test_empty_signals(self):
        event = _make_event(credit_signals=())
        embed = build_embed(event)
        signals_field = _find_field(embed, "Signals")
        assert signals_field["value"] == "None"

    def test_dates_cross_month(self):
        """Start and end in different months of the same year."""
        event = _make_event(
            start_date="2026-05-30T00:00:00Z",
            end_date="2026-06-02T00:00:00Z",
        )
        embed = build_embed(event)
        dates_field = _find_field(embed, "Dates")
        assert "May" in dates_field["value"]
        assert "Jun" in dates_field["value"]
        assert "2026" in dates_field["value"]

    def test_dates_cross_year(self):
        """Start and end in different years."""
        event = _make_event(
            start_date="2025-12-30T00:00:00Z",
            end_date="2026-01-02T00:00:00Z",
        )
        embed = build_embed(event)
        dates_field = _find_field(embed, "Dates")
        assert "2025" in dates_field["value"]
        assert "2026" in dates_field["value"]

    def test_dates_only_start(self):
        """Only start_date provided, no end_date."""
        event = _make_event(start_date="2026-05-15T00:00:00Z", end_date=None)
        embed = build_embed(event)
        dates_field = _find_field(embed, "Dates")
        assert "May" in dates_field["value"]
        assert "15" in dates_field["value"]
        assert "2026" in dates_field["value"]


# ---------------------------------------------------------------------------
# build_messages
# ---------------------------------------------------------------------------

class TestBuildMessages:
    """Tests for build_messages(events) -> list[dict]."""

    def test_single_event_one_message(self):
        events = [_make_event()]
        messages = build_messages(events)
        assert len(messages) == 1
        assert len(messages[0]["embeds"]) == 1

    def test_empty_events_no_messages(self):
        messages = build_messages([])
        assert messages == []

    def test_ten_events_one_message(self):
        events = [_make_event(title=f"Event {i}") for i in range(10)]
        messages = build_messages(events)
        assert len(messages) == 1
        assert len(messages[0]["embeds"]) == 10

    def test_fifteen_events_two_messages(self):
        events = [_make_event(title=f"Event {i}") for i in range(15)]
        messages = build_messages(events)
        assert len(messages) == 2
        assert len(messages[0]["embeds"]) == 10
        assert len(messages[1]["embeds"]) == 5

    def test_twenty_events_two_messages(self):
        events = [_make_event(title=f"Event {i}") for i in range(20)]
        messages = build_messages(events)
        assert len(messages) == 2
        assert len(messages[0]["embeds"]) == 10
        assert len(messages[1]["embeds"]) == 10

    def test_twenty_one_events_three_messages(self):
        events = [_make_event(title=f"Event {i}") for i in range(21)]
        messages = build_messages(events)
        assert len(messages) == 3

    def test_message_contains_embed_dicts(self):
        events = [_make_event()]
        messages = build_messages(events)
        embed = messages[0]["embeds"][0]
        assert "title" in embed
        assert "color" in embed
        assert "fields" in embed


# ---------------------------------------------------------------------------
# send_notifications
# ---------------------------------------------------------------------------

class TestSendNotifications:
    """Tests for send_notifications(events, webhook_url)."""

    def test_missing_webhook_url_raises(self):
        with pytest.raises(ValueError, match="webhook_url"):
            send_notifications([_make_event()], "")

    def test_none_webhook_url_raises(self):
        with pytest.raises(ValueError, match="webhook_url"):
            send_notifications([_make_event()], None)  # type: ignore[arg-type]

    @patch("src.notifier.DiscordWebhook")
    def test_calls_webhook_execute(self, mock_webhook_cls: MagicMock):
        mock_instance = MagicMock()
        mock_webhook_cls.return_value = mock_instance

        events = [_make_event()]
        send_notifications(events, "https://discord.com/api/webhooks/test")

        mock_webhook_cls.assert_called()
        mock_instance.execute.assert_called()

    @patch("src.notifier.DiscordWebhook")
    def test_sends_correct_number_of_webhooks(self, mock_webhook_cls: MagicMock):
        mock_instance = MagicMock()
        mock_webhook_cls.return_value = mock_instance

        events = [_make_event(title=f"Event {i}") for i in range(15)]
        send_notifications(events, "https://discord.com/api/webhooks/test")

        # 15 events = 2 messages -> 2 webhook calls
        assert mock_webhook_cls.call_count == 2
        assert mock_instance.execute.call_count == 2

    @patch("src.notifier.DiscordWebhook")
    def test_webhook_failure_logs_error(
        self, mock_webhook_cls: MagicMock, caplog: pytest.LogCaptureFixture
    ):
        mock_instance = MagicMock()
        mock_instance.execute.side_effect = Exception("Connection refused")
        mock_webhook_cls.return_value = mock_instance

        with caplog.at_level(logging.ERROR):
            # Must NOT raise
            send_notifications([_make_event()], "https://discord.com/api/webhooks/test")

        assert "Connection refused" in caplog.text

    @patch("src.notifier.DiscordWebhook")
    def test_empty_events_no_webhook_call(self, mock_webhook_cls: MagicMock):
        send_notifications([], "https://discord.com/api/webhooks/test")
        mock_webhook_cls.assert_not_called()

    @patch("src.notifier.DiscordWebhook")
    def test_webhook_receives_embeds(self, mock_webhook_cls: MagicMock):
        mock_instance = MagicMock()
        mock_webhook_cls.return_value = mock_instance

        events = [_make_event()]
        send_notifications(events, "https://discord.com/api/webhooks/test")

        # Verify add_embed was called with a DiscordEmbed-compatible object
        mock_instance.add_embed.assert_called()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _find_field(embed: dict, name: str) -> dict:
    """Find a field by name in an embed dict."""
    for field in embed["fields"]:
        if field["name"] == name:
            return field
    raise AssertionError(f"Field '{name}' not found in embed. Fields: {[f['name'] for f in embed['fields']]}")
