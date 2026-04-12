"""Discord notification system for free-credit event alerts."""

from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import TYPE_CHECKING

from discord_webhook import DiscordEmbed, DiscordWebhook

if TYPE_CHECKING:
    from src.models import Event

logger = logging.getLogger(__name__)

# Discord embed color codes
_COLOR_GREEN = 0x00FF00   # high score (> 0.7)
_COLOR_YELLOW = 0xFFAA00  # medium score (0.3 - 0.7)
_COLOR_GREY = 0x95A5A6    # low score (< 0.3)

_MAX_EMBEDS_PER_MESSAGE = 10

# Maximum length for any single text field sent to Discord (prevents abuse)
_MAX_FIELD_LENGTH = 256

# Pattern matching Discord mentions: @everyone, @here, <@userid>, <@!userid>,
# <@&roleid>, <#channelid>
_DISCORD_MENTION_RE = re.compile(
    r"@(everyone|here)|<@[!&]?\d+>|<#\d+>",
)


def _sanitize_discord_text(text: str) -> str:
    """Strip Discord mention patterns and control characters from untrusted text.

    This prevents scraped content from pinging @everyone/@here, mentioning
    users/roles, or injecting control characters into Discord messages.
    """
    # Replace Discord mentions with safe placeholder
    sanitized = _DISCORD_MENTION_RE.sub("[mention removed]", text)
    # Strip ASCII control characters (0x00-0x1F except \n and \t) that could
    # corrupt log output or terminal displays
    sanitized = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", sanitized)
    # Truncate to prevent excessively long payloads
    if len(sanitized) > _MAX_FIELD_LENGTH:
        sanitized = sanitized[:_MAX_FIELD_LENGTH - 3] + "..."
    return sanitized


def _score_color(score: float) -> int:
    """Return a Discord color int based on credit score."""
    if score > 0.7:
        return _COLOR_GREEN
    if score >= 0.3:
        return _COLOR_YELLOW
    return _COLOR_GREY


def _format_dates(start_date: str | None, end_date: str | None) -> str:
    """Format ISO dates into a human-readable range like 'May 15-17, 2026'."""
    if start_date is None and end_date is None:
        return "TBD"

    def _parse(iso: str) -> datetime:
        return datetime.fromisoformat(iso.replace("Z", "+00:00"))

    if start_date and end_date:
        start = _parse(start_date)
        end = _parse(end_date)
        if start.month == end.month and start.year == end.year:
            return f"{start.strftime('%b')} {start.day}\u2013{end.day}, {start.year}"
        if start.year == end.year:
            return f"{start.strftime('%b')} {start.day} \u2013 {end.strftime('%b')} {end.day}, {start.year}"
        return f"{start.strftime('%b')} {start.day}, {start.year} \u2013 {end.strftime('%b')} {end.day}, {end.year}"

    single = _parse(start_date or end_date)  # type: ignore[arg-type]
    return f"{single.strftime('%b')} {single.day}, {single.year}"


def build_embed(event: Event) -> dict:
    """Create a Discord-embed-compatible dict from an Event.

    All user-controlled text fields are sanitized to prevent Discord
    mention injection (@everyone, @here, user/role pings) and control
    character injection from malicious scraped content.
    """
    providers_value = (
        ", ".join(_sanitize_discord_text(p) for p in event.providers_detected)
        if event.providers_detected
        else "None detected"
    )
    signals_value = (
        ", ".join(_sanitize_discord_text(s) for s in event.credit_signals)
        if event.credit_signals
        else "None"
    )

    fields = [
        {"name": "Providers", "value": providers_value, "inline": True},
        {"name": "Score", "value": str(event.credit_score), "inline": True},
        {"name": "Signals", "value": signals_value, "inline": False},
        {"name": "Dates", "value": _format_dates(event.start_date, event.end_date), "inline": False},
        {"name": "Location", "value": _sanitize_discord_text(event.location), "inline": False},
        {"name": "Source", "value": _sanitize_discord_text(event.source), "inline": False},
    ]

    return {
        "title": _sanitize_discord_text(event.title),
        "url": event.url,
        "color": _score_color(event.credit_score),
        "fields": fields,
    }


def build_messages(events: list[Event]) -> list[dict]:
    """Batch events into message payloads, each with at most 10 embeds."""
    if not events:
        return []

    embeds = [build_embed(e) for e in events]
    messages: list[dict] = []

    for i in range(0, len(embeds), _MAX_EMBEDS_PER_MESSAGE):
        batch = embeds[i : i + _MAX_EMBEDS_PER_MESSAGE]
        messages.append({"embeds": batch})

    return messages


def send_notifications(events: list[Event], webhook_url: str) -> None:
    """Send event notifications to a Discord webhook.

    Raises:
        ValueError: If *webhook_url* is empty or None.
    """
    if not webhook_url:
        raise ValueError("webhook_url must be a non-empty string")

    messages = build_messages(events)
    if not messages:
        return

    for message in messages:
        try:
            webhook = DiscordWebhook(url=webhook_url)
            for embed_dict in message["embeds"]:
                embed = DiscordEmbed(
                    title=embed_dict["title"],
                    url=embed_dict.get("url"),
                    color=embed_dict["color"],
                )
                for field in embed_dict["fields"]:
                    embed.add_embed_field(
                        name=field["name"],
                        value=field["value"],
                        inline=field.get("inline", False),
                    )
                webhook.add_embed(embed)
            webhook.execute()
        except Exception:
            logger.error(
                "Failed to send Discord notification: %s",
                message.get("embeds", [{}])[0].get("title", "unknown"),
                exc_info=True,
            )
