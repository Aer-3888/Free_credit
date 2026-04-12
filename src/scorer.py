"""Credit scoring engine for free-credit event discovery.

Two-tier keyword system detects cloud/LLM providers and scores events
based on the strength of credit-related signals found in their text.
"""

from __future__ import annotations

from src.models import Event

# ── Provider keyword lists ───────────────────────────────────────────

PROVIDERS: dict[str, list[str]] = {
    "aws": ["aws", "amazon web services", "bedrock", "amazon bedrock", "aws activate"],
    "azure": [
        "azure",
        "microsoft azure",
        "azure openai",
        "founders hub",
        "microsoft for startups",
    ],
    "gcp": ["google cloud", "gcp", "vertex ai", "google for startups"],
    "anthropic": ["anthropic", "claude api", "claude"],
    "huggingface": ["hugging face", "huggingface", "zerogpu"],
    "fireworks": ["fireworks ai", "fireworks"],
    "openai": ["openai", "gpt-4", "chatgpt"],
}

# ── Signal tiers (weight per match) ─────────────────────────────────

HIGH_SIGNALS: list[str] = [
    "aws activate",
    "founders hub",
    "cloud credits provided",
    "api credits included",
    "free api access",
    "bedrock credits",
    "azure openai access",
    "vertex ai access",
    "zerogpu",
    "free credits",
    "unlimited credits",
]

MEDIUM_SIGNALS: list[str] = [
    "sponsored by",
    "powered by",
    "in partnership with",
    "credits",
    "free access",
    "workshop credits",
    "hands-on lab",
    "compute credits",
    "api access",
    # Provider names in event context are signals on their own
    "aws",
    "amazon",
    "azure",
    "google cloud",
    "microsoft",
    "anthropic",
    "bedrock",
    "openai",
]

LOW_SIGNALS: list[str] = [
    "hackathon",
    "workshop",
    "build-a-thon",
    "jam",
]

_HIGH_WEIGHT = 0.4
_MEDIUM_WEIGHT = 0.2
_LOW_WEIGHT = 0.15
_PROXIMITY_BONUS = 0.15
_PROXIMITY_WINDOW = 200


# ── Internal helpers ─────────────────────────────────────────────────


def _build_text_blob(event: Event) -> str:
    """Combine all searchable fields into one lowercase string."""
    parts = [
        event.title or "",
        event.description or "",
        event.organizer or "",
        " ".join(event.sponsors),
        event.prizes or "",
    ]
    return " ".join(parts).lower()


def _detect_providers(blob: str) -> tuple[str, ...]:
    """Return tuple of provider keys whose keywords appear in *blob*."""
    detected: list[str] = []
    for provider, keywords in PROVIDERS.items():
        for kw in keywords:
            if kw in blob:
                detected.append(provider)
                break
    return tuple(sorted(set(detected)))


def _score_signals(blob: str) -> tuple[float, list[str]]:
    """Sum weights for matched signal keywords. Return (raw_score, matched)."""
    score = 0.0
    matched: list[str] = []

    for signal in HIGH_SIGNALS:
        if signal in blob:
            score += _HIGH_WEIGHT
            matched.append(signal)

    for signal in MEDIUM_SIGNALS:
        if signal in blob:
            score += _MEDIUM_WEIGHT
            matched.append(signal)

    for signal in LOW_SIGNALS:
        if signal in blob:
            score += _LOW_WEIGHT
            matched.append(signal)

    return score, matched


def _proximity_bonus(blob: str, providers: tuple[str, ...], matched_signals: list[str]) -> float:
    """Add a bonus when a provider keyword sits within *_PROXIMITY_WINDOW* chars of a signal."""
    bonus = 0.0
    bonus_pairs_seen: set[tuple[str, str]] = set()

    for provider in providers:
        for kw in PROVIDERS[provider]:
            kw_start = blob.find(kw)
            if kw_start == -1:
                continue
            for signal in matched_signals:
                pair = (provider, signal)
                if pair in bonus_pairs_seen:
                    continue
                sig_start = blob.find(signal)
                if sig_start == -1:
                    continue
                # Distance between the closest edges of the two substrings
                kw_end = kw_start + len(kw)
                sig_end = sig_start + len(signal)
                distance = max(0, max(kw_start, sig_start) - min(kw_end, sig_end))
                if distance <= _PROXIMITY_WINDOW:
                    bonus += _PROXIMITY_BONUS
                    bonus_pairs_seen.add(pair)
    return bonus


def _clamp(value: float, lo: float = 0.0, hi: float = 1.0) -> float:
    """Clamp *value* to [*lo*, *hi*]."""
    return max(lo, min(hi, value))


# ── Public API ───────────────────────────────────────────────────────


def score_event(event: Event) -> Event:
    """Score a single event and return a new Event with scoring fields set."""
    blob = _build_text_blob(event)
    providers = _detect_providers(blob)
    raw_score, matched_signals = _score_signals(blob)
    bonus = _proximity_bonus(blob, providers, matched_signals)
    final_score = _clamp(raw_score + bonus)

    return event.with_score(
        credit_score=final_score,
        credit_signals=tuple(matched_signals),
        providers_detected=providers,
    )


def filter_events(events: list[Event], threshold: float = 0.3) -> list[Event]:
    """Score every event and return only those at or above *threshold*."""
    scored = [score_event(e) for e in events]
    return [e for e in scored if e.credit_score >= threshold]
