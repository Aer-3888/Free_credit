"""Tests for the deduplication engine (src/dedup.py).

Written FIRST (TDD red phase) — implementation follows.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone, timedelta

import pytest

from src.models import Event
from src.dedup import (
    find_new_events,
    merge_events,
    prune_expired,
    deduplicate_cross_source,
    load_events,
    save_events,
)


# ── helpers ──────────────────────────────────────────────────────────


def _make_event(
    *,
    id: str = "devpost:test-event",
    source: str = "devpost",
    title: str = "Test Event",
    url: str = "https://example.com",
    organizer: str = "Org",
    description: str = "desc",
    location: str = "Online",
    start_date: str | None = None,
    end_date: str | None = None,
    credit_score: float = 0.5,
    scraped_at: str | None = None,
) -> Event:
    return Event(
        id=id,
        source=source,
        title=title,
        url=url,
        organizer=organizer,
        description=description,
        location=location,
        start_date=start_date,
        end_date=end_date,
        credit_score=credit_score,
        scraped_at=scraped_at or datetime.now(timezone.utc).isoformat(),
    )


# ── find_new_events ─────────────────────────────────────────────────


class TestFindNewEvents:
    def test_empty_existing_returns_all_incoming(self):
        incoming = [_make_event(id="a:1"), _make_event(id="a:2")]
        result = find_new_events(incoming, existing=[])
        assert result == incoming

    def test_all_incoming_already_exist_returns_empty(self):
        events = [_make_event(id="a:1"), _make_event(id="a:2")]
        result = find_new_events(incoming=events, existing=events)
        assert result == []

    def test_partial_overlap_returns_only_new(self):
        existing = [_make_event(id="a:1")]
        incoming = [_make_event(id="a:1"), _make_event(id="a:2")]
        result = find_new_events(incoming, existing)
        assert len(result) == 1
        assert result[0].id == "a:2"

    def test_empty_incoming_returns_empty(self):
        existing = [_make_event(id="a:1")]
        result = find_new_events(incoming=[], existing=existing)
        assert result == []


# ── merge_events ─────────────────────────────────────────────────────


class TestMergeEvents:
    def test_merge_no_overlap(self):
        new = [_make_event(id="a:1")]
        existing = [_make_event(id="a:2")]
        result = merge_events(new, existing)
        ids = {e.id for e in result}
        assert ids == {"a:1", "a:2"}

    def test_merge_with_duplicates(self):
        shared = _make_event(id="a:1")
        new = [shared, _make_event(id="a:2")]
        existing = [shared, _make_event(id="a:3")]
        result = merge_events(new, existing)
        ids = {e.id for e in result}
        assert ids == {"a:1", "a:2", "a:3"}
        # No duplicate ids
        assert len(result) == 3

    def test_merge_preserves_existing_when_duplicate(self):
        """When same id appears in both, existing version is kept."""
        existing_event = _make_event(id="a:1", title="Original")
        new_event = _make_event(id="a:1", title="Updated")
        result = merge_events([new_event], [existing_event])
        matched = [e for e in result if e.id == "a:1"]
        assert len(matched) == 1
        assert matched[0].title == "Original"


# ── prune_expired ────────────────────────────────────────────────────


class TestPruneExpired:
    def test_removes_old_events(self):
        old_date = (datetime.now(timezone.utc) - timedelta(days=100)).isoformat()
        recent_date = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
        old_event = _make_event(id="a:old", scraped_at=old_date, end_date=old_date)
        recent_event = _make_event(id="a:recent", scraped_at=recent_date)
        result = prune_expired([old_event, recent_event], max_age_days=90)
        assert len(result) == 1
        assert result[0].id == "a:recent"

    def test_keeps_recent_events_without_end_date(self):
        recent_date = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
        event = _make_event(id="a:1", scraped_at=recent_date, end_date=None)
        result = prune_expired([event], max_age_days=90)
        assert len(result) == 1

    def test_custom_max_age(self):
        date_50_days_ago = (datetime.now(timezone.utc) - timedelta(days=50)).isoformat()
        event = _make_event(
            id="a:1",
            scraped_at=date_50_days_ago,
            end_date=date_50_days_ago,
        )
        # Default 90 days: should keep
        assert len(prune_expired([event], max_age_days=90)) == 1
        # Stricter 30 days: should prune
        assert len(prune_expired([event], max_age_days=30)) == 0

    def test_empty_list(self):
        assert prune_expired([], max_age_days=90) == []

    def test_prune_uses_end_date_when_available(self):
        """If end_date is set and past max_age, event is pruned even if scraped_at is recent."""
        old_end = (datetime.now(timezone.utc) - timedelta(days=100)).isoformat()
        recent_scrape = datetime.now(timezone.utc).isoformat()
        event = _make_event(id="a:1", scraped_at=recent_scrape, end_date=old_end)
        result = prune_expired([event], max_age_days=90)
        assert len(result) == 0


# ── load_events / save_events ────────────────────────────────────────


class TestLoadEvents:
    def test_load_from_valid_json(self, tmp_path):
        event = _make_event(id="a:1")
        path = tmp_path / "events.json"
        path.write_text(json.dumps([event.to_dict()], indent=2))
        result = load_events(str(path), _base_dir=tmp_path)
        assert len(result) == 1
        assert result[0].id == "a:1"

    def test_load_missing_file_returns_empty(self, tmp_path):
        path = tmp_path / "does_not_exist.json"
        result = load_events(str(path), _base_dir=tmp_path)
        assert result == []

    def test_load_corrupted_json_returns_empty(self, tmp_path):
        path = tmp_path / "bad.json"
        path.write_text("{{{not valid json!!!")
        result = load_events(str(path), _base_dir=tmp_path)
        assert result == []

    def test_load_empty_array(self, tmp_path):
        path = tmp_path / "empty.json"
        path.write_text("[]")
        result = load_events(str(path), _base_dir=tmp_path)
        assert result == []


class TestSaveEvents:
    def test_save_creates_pretty_json(self, tmp_path):
        events = [_make_event(id="a:1"), _make_event(id="a:2")]
        path = tmp_path / "out.json"
        save_events(events, str(path), _base_dir=tmp_path)
        raw = path.read_text()
        data = json.loads(raw)
        assert len(data) == 2
        # Pretty-printed: should contain newlines/indentation
        assert "\n" in raw
        assert "  " in raw

    def test_roundtrip(self, tmp_path):
        events = [
            _make_event(id="a:1", title="Alpha"),
            _make_event(id="b:2", title="Beta"),
        ]
        path = tmp_path / "roundtrip.json"
        save_events(events, str(path), _base_dir=tmp_path)
        loaded = load_events(str(path), _base_dir=tmp_path)
        assert len(loaded) == 2
        assert loaded[0].title == "Alpha"
        assert loaded[1].title == "Beta"

    def test_save_creates_parent_directories(self, tmp_path):
        path = tmp_path / "subdir" / "deep" / "events.json"
        events = [_make_event(id="a:1")]
        save_events(events, str(path), _base_dir=tmp_path)
        assert path.exists()
        loaded = load_events(str(path), _base_dir=tmp_path)
        assert len(loaded) == 1


# ── deduplicate_cross_source ─────────────────────────────────────────


class TestDeduplicateCrossSource:
    def test_same_title_different_sources_keeps_higher_score(self):
        low = _make_event(
            id="devpost:hackathon",
            source="devpost",
            title="AI Hackathon 2026",
            credit_score=0.3,
        )
        high = _make_event(
            id="luma:hackathon",
            source="luma",
            title="AI Hackathon 2026",
            credit_score=0.9,
        )
        result = deduplicate_cross_source([low, high])
        assert len(result) == 1
        assert result[0].id == "luma:hackathon"

    def test_case_insensitive_title_matching(self):
        e1 = _make_event(
            id="devpost:hack",
            source="devpost",
            title="  AI Hackathon  ",
            credit_score=0.2,
        )
        e2 = _make_event(
            id="luma:hack",
            source="luma",
            title="ai hackathon",
            credit_score=0.8,
        )
        result = deduplicate_cross_source([e1, e2])
        assert len(result) == 1
        assert result[0].credit_score == 0.8

    def test_different_titles_no_dedup(self):
        e1 = _make_event(id="devpost:a", source="devpost", title="Event A")
        e2 = _make_event(id="luma:b", source="luma", title="Event B")
        result = deduplicate_cross_source([e1, e2])
        assert len(result) == 2

    def test_same_source_same_title_not_deduped(self):
        """Cross-source dedup only applies across different sources."""
        e1 = _make_event(id="devpost:a", source="devpost", title="Hackathon", credit_score=0.3)
        e2 = _make_event(id="devpost:b", source="devpost", title="Hackathon", credit_score=0.7)
        result = deduplicate_cross_source([e1, e2])
        assert len(result) == 2

    def test_three_sources_same_title_keeps_best(self):
        events = [
            _make_event(id="devpost:h", source="devpost", title="Hack", credit_score=0.3),
            _make_event(id="luma:h", source="luma", title="Hack", credit_score=0.9),
            _make_event(id="mlh:h", source="mlh", title="Hack", credit_score=0.6),
        ]
        result = deduplicate_cross_source(events)
        assert len(result) == 1
        assert result[0].credit_score == 0.9

    def test_empty_list(self):
        assert deduplicate_cross_source([]) == []

    def test_equal_scores_keeps_first_encountered(self):
        e1 = _make_event(id="devpost:h", source="devpost", title="Hack", credit_score=0.5)
        e2 = _make_event(id="luma:h", source="luma", title="Hack", credit_score=0.5)
        result = deduplicate_cross_source([e1, e2])
        assert len(result) == 1
        # First encountered wins on tie
        assert result[0].id == "devpost:h"
