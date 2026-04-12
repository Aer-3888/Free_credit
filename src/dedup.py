"""Deduplication engine for discovered events.

Provides functions to:
- Find new events not yet in the existing set
- Merge new events with existing ones (no duplicates)
- Prune expired events by age
- Deduplicate across sources (same title, different source)
- Load / save event lists from/to JSON files
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path

from src.models import Event, events_to_json, events_from_json

logger = logging.getLogger(__name__)


def find_new_events(
    incoming: list[Event],
    existing: list[Event],
) -> list[Event]:
    """Return events from *incoming* whose id is not in *existing*."""
    existing_ids = frozenset(e.id for e in existing)
    return [e for e in incoming if e.id not in existing_ids]


def merge_events(
    new_events: list[Event],
    existing: list[Event],
) -> list[Event]:
    """Combine *new_events* and *existing*, keeping existing on id collision."""
    seen_ids: set[str] = set()
    merged: list[Event] = []

    # Existing events take priority
    for event in existing:
        if event.id not in seen_ids:
            seen_ids.add(event.id)
            merged.append(event)

    for event in new_events:
        if event.id not in seen_ids:
            seen_ids.add(event.id)
            merged.append(event)

    return merged


def _parse_iso(datestr: str) -> datetime:
    """Parse an ISO 8601 datetime string, tolerant of common variants."""
    return datetime.fromisoformat(datestr)


def prune_expired(
    events: list[Event],
    max_age_days: int = 90,
) -> list[Event]:
    """Remove events older than *max_age_days*.

    The reference date is:
    - ``end_date`` if set (the event has already ended)
    - ``scraped_at`` otherwise (no end date known, use scrape time)

    An event is pruned when its reference date is more than *max_age_days* before now.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=max_age_days)
    kept: list[Event] = []

    for event in events:
        ref_str = event.end_date if event.end_date else event.scraped_at
        try:
            ref_dt = _parse_iso(ref_str)
            # Ensure timezone-aware comparison
            if ref_dt.tzinfo is None:
                ref_dt = ref_dt.replace(tzinfo=timezone.utc)
            if ref_dt >= cutoff:
                kept.append(event)
        except (ValueError, TypeError):
            # Can't parse the date — keep the event to be safe
            logger.warning("Could not parse date %r for event %s; keeping it", ref_str, event.id)
            kept.append(event)

    return kept


def _normalise_title(title: str) -> str:
    """Lower-case, stripped title for cross-source comparison."""
    return title.strip().lower()


def deduplicate_cross_source(events: list[Event]) -> list[Event]:
    """Remove cross-source duplicates (same title, different source).

    For each group of events sharing a normalised title from **different** sources,
    keep only the one with the highest ``credit_score`` (first encountered wins ties).
    Events from the **same** source with the same title are never deduped here.
    """
    if not events:
        return []

    # Group by normalised title
    title_groups: dict[str, list[Event]] = {}
    for event in events:
        key = _normalise_title(event.title)
        title_groups.setdefault(key, []).append(event)

    result: list[Event] = []
    for _title, group in title_groups.items():
        sources = {e.source for e in group}
        if len(sources) <= 1:
            # All same source — no cross-source dedup, keep all
            result.extend(group)
        else:
            # Multiple sources — keep the one with highest credit_score
            best = max(group, key=lambda e: (e.credit_score, -group.index(e)))
            # Correction: on equal score, first encountered wins.
            # max() with tuple key: higher score wins; on tie, lower index wins
            # -index makes lower index "larger" so max picks it.
            result.append(best)

    return result


# Default project root for path validation (resolved once at import time)
_PROJECT_ROOT: Path = Path(__file__).resolve().parent.parent


def _validate_data_path(path: str, *, base_dir: Path | None = None) -> Path:
    """Resolve the data path and verify it stays within an allowed directory.

    Args:
        path: Filesystem path to validate.
        base_dir: Root directory the path must reside under.
                  Defaults to the project root.

    Raises:
        ValueError: If the resolved path escapes *base_dir* or has a
                    non-``.json`` suffix.
    """
    root = (base_dir or _PROJECT_ROOT).resolve()
    file_path = Path(path).resolve()

    # Ensure the resolved path is under the allowed root
    try:
        file_path.relative_to(root)
    except ValueError:
        raise ValueError(
            f"DATA_PATH resolves outside allowed root: {file_path} "
            f"is not under {root}"
        )

    # Ensure it ends with .json
    if file_path.suffix != ".json":
        raise ValueError(f"DATA_PATH must end with .json, got: {file_path.suffix}")

    return file_path


def load_events(path: str, *, _base_dir: Path | None = None) -> list[Event]:
    """Read events from a JSON file.

    Returns an empty list if the file is missing or contains invalid JSON.

    Raises ValueError if *path* resolves outside the project directory
    (or *_base_dir* when provided for testing).
    """
    file_path = _validate_data_path(path, base_dir=_base_dir)
    if not file_path.exists():
        return []
    try:
        raw = file_path.read_text(encoding="utf-8")
        return events_from_json(raw)
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        logger.warning("Failed to load events from %s: %s", path, exc)
        return []


def save_events(events: list[Event], path: str, *, _base_dir: Path | None = None) -> None:
    """Write events to a JSON file with pretty formatting.

    Creates parent directories if they don't exist.

    Raises ValueError if *path* resolves outside the project directory
    (or *_base_dir* when provided for testing).
    """
    file_path = _validate_data_path(path, base_dir=_base_dir)
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text(events_to_json(events), encoding="utf-8")
