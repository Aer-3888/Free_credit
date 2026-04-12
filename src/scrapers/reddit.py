"""Reddit scraper -- discovers hackathon posts from subreddits.

Uses Reddit's public RSS feeds (.rss suffix on any subreddit/search URL).
No API key or authentication required. RSS feeds are less likely to be
blocked than the JSON API.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from selectolax.parser import HTMLParser

from src.models import Event
from src.scrapers.base import BaseScraper, make_client

logger = logging.getLogger(__name__)

REDDIT_BASE = "https://www.reddit.com"

SUBREDDITS: tuple[str, ...] = ("hackathons", "aws", "MachineLearning", "artificial")

SEARCH_KEYWORDS: tuple[str, ...] = (
    "cloud credits",
    "free API credits",
    "hackathon AWS",
    "hackathon Azure",
)

MAX_POSTS_PER_SUBREDDIT = 25


def _parse_rss_feed(xml_text: str) -> list[Event]:
    """Parse a Reddit RSS/Atom feed into Event objects.

    Reddit RSS feeds use Atom format with <entry> elements containing:
    - <title> — post title
    - <link href="..."/> — post URL
    - <id> — unique identifier (contains the post ID)
    - <content> — post body HTML
    - <author><name> — author username
    - <updated> — timestamp
    """
    parser = HTMLParser(xml_text)
    events: list[Event] = []

    for entry in parser.css("entry"):
        title_node = entry.css_first("title")
        title = title_node.text(strip=True) if title_node else ""
        if not title:
            continue

        # Extract link href
        link_node = entry.css_first("link")
        url = link_node.attributes.get("href", "") if link_node else ""

        # Extract post ID from the <id> tag (format: t3_xxxxx)
        id_node = entry.css_first("id")
        raw_id = id_node.text(strip=True) if id_node else ""
        # Reddit atom IDs look like: "t3_abc123" or full URLs
        post_id = raw_id.split("t3_")[-1].split("/")[-1] if raw_id else ""

        # Extract content/description
        content_node = entry.css_first("content")
        description = ""
        if content_node:
            # Content is HTML-encoded, parse the inner HTML for text
            inner = HTMLParser(content_node.text())
            description = inner.body.text(strip=True) if inner.body else ""

        # Extract author
        author_node = entry.css_first("author name")
        author = author_node.text(strip=True) if author_node else ""

        # Extract date
        updated_node = entry.css_first("updated")
        start_date = updated_node.text(strip=True) if updated_node else None

        if post_id and url:
            events.append(Event(
                id=f"reddit:{post_id}",
                source="reddit",
                title=title,
                url=url,
                organizer=author,
                description=description[:2000],
                location="Online",
                start_date=start_date,
            ))

    return events


class RedditScraper(BaseScraper):
    """Scrape Reddit subreddits for hackathon and free-credit posts."""

    name = "reddit"

    async def scrape(self) -> list[Event]:
        seen: dict[str, Event] = {}

        try:
            async with make_client() as client:
                for subreddit in SUBREDDITS:
                    # Fetch latest posts from r/hackathons
                    if subreddit == "hackathons":
                        await self._fetch_rss(
                            client,
                            f"{REDDIT_BASE}/r/{subreddit}/new/.rss?limit={MAX_POSTS_PER_SUBREDDIT}",
                            seen=seen,
                        )

                    # Search each subreddit with credit-related keywords
                    for keyword in SEARCH_KEYWORDS:
                        query = keyword.replace(" ", "+")
                        await self._fetch_rss(
                            client,
                            f"{REDDIT_BASE}/r/{subreddit}/search/.rss?q={query}&restrict_sr=on&sort=new&limit={MAX_POSTS_PER_SUBREDDIT}",
                            seen=seen,
                        )

        except Exception:
            logger.exception("Reddit scraper failed at top level")
            return []

        return list(seen.values())

    async def _fetch_rss(
        self,
        client,
        url: str,
        *,
        seen: dict[str, Event],
    ) -> None:
        """Fetch a single Reddit RSS feed and merge results into *seen*."""
        try:
            response = await self.fetch(client, url)
            for event in _parse_rss_feed(response.text):
                if event.id not in seen:
                    seen[event.id] = event
        except Exception:
            logger.warning("Reddit RSS fetch failed for %s", url.split("?")[0], exc_info=True)
