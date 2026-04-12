"""Orchestrator: run all scrapers → score → dedup → notify."""

from __future__ import annotations

import asyncio
import logging
import os
import sys

from src.dedup import (
    deduplicate_cross_source,
    find_new_events,
    load_events,
    merge_events,
    prune_expired,
    save_events,
)
from src.models import Event
from src.notifier import send_notifications
from src.scorer import filter_events
from src.scrapers.devpost import DevpostScraper
from src.scrapers.eventbrite import EventbriteScraper
from src.scrapers.google_search import GoogleSearchScraper
from src.scrapers.luma import LumaScraper
from src.scrapers.mlh import MLHScraper
from src.scrapers.reddit import RedditScraper
from src.scrapers.twitter import TwitterScraper

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

DATA_PATH = os.environ.get("DATA_PATH", "data/events.json")
SCORE_THRESHOLD = float(os.environ.get("SCORE_THRESHOLD", "0.3"))


async def run_scraper(scraper) -> list[Event]:
    """Run a single scraper with error isolation."""
    try:
        logger.info("Running scraper: %s", scraper.name)
        events = await scraper.scrape()
        logger.info("Scraper %s found %d events", scraper.name, len(events))
        return events
    except Exception:
        logger.exception("Scraper %s failed", scraper.name)
        return []


async def main() -> int:
    """Main pipeline: scrape → score → dedup → notify. Returns count of new events."""
    # Read webhook URL at call time, not module import time, to avoid
    # keeping the secret in a module-level global for the process lifetime.
    discord_webhook_url = os.environ.get("DISCORD_WEBHOOK_URL", "")

    scrapers = [
        DevpostScraper(),
        MLHScraper(),
        LumaScraper(),
        EventbriteScraper(),
        RedditScraper(),
        GoogleSearchScraper(),
        TwitterScraper(),
    ]

    # Run all scrapers concurrently
    results = await asyncio.gather(*(run_scraper(s) for s in scrapers))
    all_events = [event for batch in results for event in batch]
    logger.info("Total raw events scraped: %d", len(all_events))

    if not all_events:
        logger.info("No events found, exiting")
        return 0

    # Score and filter
    scored = filter_events(all_events, threshold=SCORE_THRESHOLD)
    logger.info("Events above score threshold (%.2f): %d", SCORE_THRESHOLD, len(scored))

    # Cross-source dedup
    deduped = deduplicate_cross_source(scored)
    logger.info("Events after cross-source dedup: %d", len(deduped))

    # Compare with existing data
    existing = load_events(DATA_PATH)
    new_events = find_new_events(deduped, existing)
    logger.info("New events not previously seen: %d", len(new_events))

    if new_events:
        # Notify
        if discord_webhook_url:
            try:
                send_notifications(new_events, discord_webhook_url)
                logger.info("Discord notifications sent for %d events", len(new_events))
            except Exception:
                logger.exception("Failed to send Discord notifications")
        else:
            logger.warning("DISCORD_WEBHOOK_URL not set, skipping notifications")

        # Persist
        merged = merge_events(new_events, existing)
        pruned = prune_expired(merged)
        save_events(pruned, DATA_PATH)
        logger.info("Saved %d total events to %s", len(pruned), DATA_PATH)

    return len(new_events)


def cli():
    """CLI entry point."""
    count = asyncio.run(main())
    logger.info("Done. %d new events discovered.", count)
    sys.exit(0)


if __name__ == "__main__":
    cli()
