from __future__ import annotations

import asyncio
import ipaddress
import logging
import random
import socket
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING
from urllib.parse import urlparse

import httpx
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

if TYPE_CHECKING:
    from src.models import Event

logger = logging.getLogger(__name__)

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:133.0) Gecko/20100101 Firefox/133.0",
]

# Shared semaphore: max 3 concurrent requests across all scrapers
_semaphore = asyncio.Semaphore(3)

DEFAULT_TIMEOUT = 30.0
DEFAULT_DELAY_RANGE = (1.0, 2.5)

# Maximum response body size: 10 MB.  Prevents memory exhaustion from
# malicious or misconfigured servers returning unbounded data.
MAX_RESPONSE_BYTES = 10 * 1024 * 1024

# Maximum number of redirects httpx will follow per request.
MAX_REDIRECTS = 5

# Domains the scraper is allowed to contact.  Requests to any other
# host are rejected before the connection is made.
ALLOWED_DOMAINS: frozenset[str] = frozenset({
    "devpost.com",
    "www.eventbrite.com",
    "api.lu.ma",
    "www.mlh.com",
    "mlh.com",
    "lu.ma",
    "luma.com",
    "eventbrite.com",
    "www.google.com",
    "www.reddit.com",
    "old.reddit.com",
    "nitter.privacydev.net",
    "nitter.poast.org",
})


# ── URL / network safety helpers ────────────────────────────────────


def _is_private_ip(host: str) -> bool:
    """Return True if *host* resolves to a loopback or private IP."""
    try:
        addr = ipaddress.ip_address(host)
        return addr.is_private or addr.is_loopback or addr.is_reserved or addr.is_link_local
    except ValueError:
        pass
    # host is a hostname, resolve it
    try:
        infos = socket.getaddrinfo(host, None, socket.AF_UNSPEC, socket.SOCK_STREAM)
        for _family, _type, _proto, _canonname, sockaddr in infos:
            ip = sockaddr[0]
            addr = ipaddress.ip_address(ip)
            if addr.is_private or addr.is_loopback or addr.is_reserved or addr.is_link_local:
                return True
    except socket.gaierror:
        # DNS resolution failed -- reject to be safe
        return True
    return False


def validate_url(url: str) -> None:
    """Raise ValueError if *url* is not a safe, allowed HTTPS target.

    Checks performed:
    1. Scheme must be https.
    2. Host must be in ALLOWED_DOMAINS (or a subdomain thereof).
    3. Host must not resolve to a private / loopback IP (SSRF protection).
    """
    parsed = urlparse(url)
    if parsed.scheme != "https":
        raise ValueError(f"Only HTTPS URLs are allowed, got scheme={parsed.scheme!r}")

    host = (parsed.hostname or "").lower()
    if not host:
        raise ValueError("URL has no hostname")

    # Allow exact match or subdomain match (e.g. foo.devpost.com)
    domain_ok = any(
        host == allowed or host.endswith(f".{allowed}")
        for allowed in ALLOWED_DOMAINS
    )
    if not domain_ok:
        raise ValueError(f"Host {host!r} is not in the allow-list")

    if _is_private_ip(host):
        raise ValueError(f"Host {host!r} resolves to a private/loopback address (SSRF blocked)")


# ── Client factory ──────────────────────────────────────────────────


def make_client(timeout: float = DEFAULT_TIMEOUT) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        timeout=timeout,
        headers={"User-Agent": random.choice(USER_AGENTS)},
        follow_redirects=True,
        max_redirects=MAX_REDIRECTS,
    )


def _is_retryable(exc: BaseException) -> bool:
    """Only retry on transient errors. Don't retry 4xx (permanent rejections)."""
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        # Retry on 429 (rate limit) and 5xx (server errors)
        return status == 429 or status >= 500
    # Retry on connection/timeout errors
    if isinstance(exc, (httpx.ConnectError, httpx.TimeoutException, httpx.ReadError)):
        return True
    return False


# ── Base class ──────────────────────────────────────────────────────


class BaseScraper(ABC):
    """Abstract base for all event scrapers."""

    name: str

    @abstractmethod
    async def scrape(self) -> list[Event]:
        """Fetch and return events from this source."""
        ...

    @staticmethod
    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception(_is_retryable),
    )
    async def fetch(client: httpx.AsyncClient, url: str, **kwargs) -> httpx.Response:
        """Rate-limited, retrying HTTP GET with safety checks.

        Before issuing the request this method:
        - Validates the URL scheme (HTTPS only) and domain allow-list.
        - Blocks requests to private / loopback IPs (SSRF defence).

        After receiving the response:
        - Enforces a maximum body size to prevent memory exhaustion.
        """
        validate_url(url)

        async with _semaphore:
            await asyncio.sleep(random.uniform(*DEFAULT_DELAY_RANGE))
            response = await client.get(url, **kwargs)
            response.raise_for_status()

            # Enforce response-size limit
            content_length = response.headers.get("content-length")
            if content_length is not None and int(content_length) > MAX_RESPONSE_BYTES:
                raise ValueError(
                    f"Response from {url} exceeds size limit "
                    f"({content_length} > {MAX_RESPONSE_BYTES} bytes)"
                )
            if len(response.content) > MAX_RESPONSE_BYTES:
                raise ValueError(
                    f"Response body from {url} exceeds size limit "
                    f"({len(response.content)} > {MAX_RESPONSE_BYTES} bytes)"
                )

            return response
