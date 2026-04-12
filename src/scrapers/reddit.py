"""Reddit scraper -- discovers hackathon posts from subreddits.

Uses Reddit's free JSON API (append .json to any page URL).
No API key required; only a descriptive User-Agent header.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from src.models import Event
from src.scrapers.base import BaseScraper, make_client

logger = logging.getLogger(__name__)

REDDIT_BASE = "https://www.reddit.com"
USER_AGENT = "FreeCreditScraper/0.1"

SUBREDDITS: tuple[str, ...] = ("hackathons", "aws", "MachineLearning", "artificial")

SEARCH_KEYWORDS: tuple[str, ...] = (
    "cloud credits",
    "free API",
    "hackathon AWS",
    "hackathon Azure",
)

MAX_POSTS_PER_SUBREDDIT = 25


def _build_permalink_url(permalink: str) -> str:
    """Turn a Reddit permalink into a full HTTPS URL."""
    return f"{REDDIT_BASE}{permalink}"


def _parse_post(post_data: dict) -> Event | None:
    """Convert a single Reddit post JSON object into an Event.

    Returns None if the post lacks required fields.
    """
    data = post_data.get("data", {})
    post_id = data.get("id", "")
    if not post_id:
        return None

    title = data.get("title", "")
    if not title:
        return None

    selftext = data.get("selftext", "")
    author = data.get("author", "")
    permalink = data.get("permalink", "")
    is_self = data.get("is_self", True)
    external_url = data.get("url", "")

    # Use external URL for link posts; reddit permalink for self posts
    if is_self or not external_url or external_url.startswith(REDDIT_BASE):
        url = _build_permalink_url(permalink) if permalink else ""
    else:
        url = external_url

    created_utc = data.get("created_utc")
    start_date: str | None = None
    if created_utc:
        start_date = datetime.fromtimestamp(created_utc, tz=timezone.utc).isoformat()

    return Event(
        id=f"reddit:{post_id}",
        source="reddit",
        title=title,
        url=url,
        organizer=author,
        description=selftext,
        location="Online",  # Reddit posts don't have structured location
        start_date=start_date,
    )


def _parse_listing(data: dict) -> list[Event]:
    """Parse a Reddit listing JSON response into Event objects."""
    children = data.get("data", {}).get("children", [])
    events: list[Event] = []
    for child in children:
        event = _parse_post(child)
        if event is not None:
            events.append(event)
    return events


class RedditScraper(BaseScraper):
    """Scrape Reddit subreddits for hackathon and free-credit posts."""

    name = "reddit"

    async def scrape(self) -> list[Event]:
        seen: dict[str, Event] = {}

        try:
            async with make_client() as client:
                # Override User-Agent for Reddit-friendly identification
                client.headers["User-Agent"] = USER_AGENT

                for subreddit in SUBREDDITS:
                    # 1. Fetch latest posts from r/hackathons (only for hackathons)
                    if subreddit == "hackathons":
                        await self._fetch_listing(
                            client,
                            f"{REDDIT_BASE}/r/{subreddit}/new.json",
                            params={"limit": str(MAX_POSTS_PER_SUBREDDIT)},
                            seen=seen,
                        )

                    # 2. Search each subreddit with credit-related keywords
                    for keyword in SEARCH_KEYWORDS:
                        await self._fetch_listing(
                            client,
                            f"{REDDIT_BASE}/r/{subreddit}/search.json",
                            params={
                                "q": keyword,
                                "restrict_sr": "on",
                                "sort": "new",
                                "limit": str(MAX_POSTS_PER_SUBREDDIT),
                            },
                            seen=seen,
                        )

        except Exception:
            logger.exception("Reddit scraper failed at top level")
            return []

        return list(seen.values())

    async def _fetch_listing(
        self,
        client,
        url: str,
        *,
        params: dict[str, str],
        seen: dict[str, Event],
    ) -> None:
        """Fetch a single Reddit listing endpoint and merge results into *seen*."""
        try:
            response = await self.fetch(client, url, params=params)
            data = response.json()
            for event in _parse_listing(data):
                if event.id not in seen:
                    seen[event.id] = event
        except Exception:
            logger.warning("Reddit fetch failed for %s", url, exc_info=True)
