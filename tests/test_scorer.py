"""Tests for the credit scoring engine — written FIRST (TDD RED phase)."""

from __future__ import annotations

import pytest

from src.models import Event
from src.scorer import score_event, filter_events


def _make_event(
    *,
    title: str = "Test Event",
    description: str = "",
    organizer: str = "",
    sponsors: tuple[str, ...] = (),
    prizes: str | None = None,
) -> Event:
    """Factory helper that builds an Event with sensible defaults."""
    return Event(
        id="test:1",
        source="test",
        title=title,
        url="https://example.com",
        organizer=organizer,
        description=description,
        location="Online",
        sponsors=sponsors,
        prizes=prizes,
    )


# ── Provider detection ──────────────────────────────────────────────


class TestProviderDetection:
    def test_aws_activate_in_description(self) -> None:
        event = _make_event(description="Participants get AWS Activate credits to build.")
        scored = score_event(event)
        assert scored.credit_score >= 0.7
        assert "aws" in scored.providers_detected

    def test_google_cloud_sponsored_with_credits(self) -> None:
        event = _make_event(
            description="Sponsored by Google Cloud. Cloud credits provided for every team.",
        )
        scored = score_event(event)
        assert scored.credit_score >= 0.7
        assert "gcp" in scored.providers_detected

    def test_azure_openai_in_prizes(self) -> None:
        event = _make_event(prizes="Winners receive Azure OpenAI access and mentorship.")
        scored = score_event(event)
        assert scored.credit_score >= 0.7
        assert "azure" in scored.providers_detected

    def test_bedrock_credits_detects_aws(self) -> None:
        event = _make_event(description="Teams will receive Bedrock credits.")
        scored = score_event(event)
        assert "aws" in scored.providers_detected

    def test_founders_hub_detects_azure(self) -> None:
        event = _make_event(description="Access via Founders Hub program.")
        scored = score_event(event)
        assert "azure" in scored.providers_detected

    def test_multiple_providers_all_detected(self) -> None:
        event = _make_event(
            description=(
                "Use AWS Activate credits and Azure OpenAI access. "
                "Google Cloud also provides free credits."
            ),
        )
        scored = score_event(event)
        assert "aws" in scored.providers_detected
        assert "azure" in scored.providers_detected
        assert "gcp" in scored.providers_detected


# ── Score values ─────────────────────────────────────────────────────


class TestScoreValues:
    def test_no_credit_keywords_scores_zero(self) -> None:
        event = _make_event(
            title="Community Meetup",
            description="Join us for an evening of networking and food.",
        )
        scored = score_event(event)
        assert scored.credit_score == 0.0

    def test_generic_hackathon_low_score(self) -> None:
        event = _make_event(
            title="hackathon",
            description="A fun weekend hackathon to build cool projects.",
        )
        scored = score_event(event)
        assert scored.credit_score < 0.3

    def test_score_clamped_to_max_1(self) -> None:
        """Pile on every signal possible; score must not exceed 1.0."""
        event = _make_event(
            title="AWS Activate Hackathon Workshop",
            description=(
                "Free credits, unlimited credits, cloud credits provided, "
                "api credits included, free api access, bedrock credits, "
                "azure openai access, vertex ai access, zerogpu, "
                "sponsored by, powered by, in partnership with, "
                "credits, free access, workshop credits, hands-on lab, "
                "compute credits, api access, build-a-thon, jam"
            ),
            organizer="Google Cloud",
            sponsors=("AWS", "Microsoft Azure", "Anthropic"),
            prizes="Founders Hub membership and AWS Activate package",
        )
        scored = score_event(event)
        assert scored.credit_score <= 1.0

    def test_score_clamped_to_min_0(self) -> None:
        event = _make_event(description="Nothing relevant here at all.")
        scored = score_event(event)
        assert scored.credit_score >= 0.0


# ── Proximity bonus ─────────────────────────────────────────────────


class TestProximityBonus:
    def test_provider_near_credit_signal_gets_bonus(self) -> None:
        """Provider keyword within 200 chars of a credit signal should score higher."""
        close_text = "Google Cloud offers cloud credits provided for participants."
        far_text = (
            "Google Cloud hosts the event. "
            + ("x" * 300)
            + " Cloud credits provided for all."
        )

        close_event = _make_event(description=close_text)
        far_event = _make_event(description=far_text)

        close_scored = score_event(close_event)
        far_scored = score_event(far_event)

        assert close_scored.credit_score > far_scored.credit_score


# ── Signal tracking ──────────────────────────────────────────────────


class TestSignalTracking:
    def test_matched_signals_recorded(self) -> None:
        event = _make_event(description="Teams get aws activate credits and free api access.")
        scored = score_event(event)
        assert "aws activate" in scored.credit_signals
        assert "free api access" in scored.credit_signals

    def test_no_signals_for_empty_event(self) -> None:
        event = _make_event(description="Nothing here.")
        scored = score_event(event)
        assert scored.credit_signals == ()


# ── filter_events ────────────────────────────────────────────────────


class TestFilterEvents:
    def test_filters_below_threshold(self) -> None:
        high = _make_event(description="Free credits from AWS Activate package.")
        low = _make_event(description="Community meetup with snacks.")
        result = filter_events([high, low], threshold=0.3)
        assert len(result) == 1
        assert result[0].credit_score >= 0.3

    def test_default_threshold_is_0_3(self) -> None:
        high = _make_event(description="Free credits and bedrock credits for all.")
        low = _make_event(description="Come hang out.")
        result = filter_events([high, low])
        assert all(e.credit_score >= 0.3 for e in result)

    def test_empty_list_returns_empty(self) -> None:
        assert filter_events([]) == []


# ── Immutability ─────────────────────────────────────────────────────


class TestImmutability:
    def test_score_event_returns_new_event(self) -> None:
        original = _make_event(description="AWS Activate credits provided.")
        scored = score_event(original)
        assert scored is not original
        assert original.credit_score == 0.0
        assert scored.credit_score > 0.0
