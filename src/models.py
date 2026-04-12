from __future__ import annotations

import json
from dataclasses import dataclass, asdict, field
from datetime import datetime, timezone


@dataclass(frozen=True)
class Event:
    """Immutable representation of a discovered event."""

    id: str                               # "devpost:hackathon-slug"
    source: str                           # "devpost" | "luma" | "mlh" | "eventbrite" | "aws"
    title: str
    url: str
    organizer: str
    description: str
    location: str                         # City name or "Online"
    start_date: str | None = None         # ISO 8601
    end_date: str | None = None           # ISO 8601
    registration_deadline: str | None = None
    sponsors: tuple[str, ...] = ()
    prizes: str | None = None
    credit_score: float = 0.0             # 0.0–1.0
    credit_signals: tuple[str, ...] = ()  # Keywords that matched
    providers_detected: tuple[str, ...] = ()  # ["AWS", "Azure", ...]
    scraped_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def with_score(
        self,
        credit_score: float,
        credit_signals: tuple[str, ...],
        providers_detected: tuple[str, ...],
    ) -> Event:
        """Return a new Event with updated scoring fields."""
        return Event(
            id=self.id,
            source=self.source,
            title=self.title,
            url=self.url,
            organizer=self.organizer,
            description=self.description,
            location=self.location,
            start_date=self.start_date,
            end_date=self.end_date,
            registration_deadline=self.registration_deadline,
            sponsors=self.sponsors,
            prizes=self.prizes,
            credit_score=credit_score,
            credit_signals=credit_signals,
            providers_detected=providers_detected,
            scraped_at=self.scraped_at,
        )

    def to_dict(self) -> dict:
        d = asdict(self)
        d["sponsors"] = list(d["sponsors"])
        d["credit_signals"] = list(d["credit_signals"])
        d["providers_detected"] = list(d["providers_detected"])
        return d

    @classmethod
    def from_dict(cls, d: dict) -> Event:
        d = dict(d)
        d["sponsors"] = tuple(d.get("sponsors", []))
        d["credit_signals"] = tuple(d.get("credit_signals", []))
        d["providers_detected"] = tuple(d.get("providers_detected", []))
        return cls(**d)


def events_to_json(events: list[Event]) -> str:
    return json.dumps([e.to_dict() for e in events], indent=2, ensure_ascii=False)


def events_from_json(raw: str) -> list[Event]:
    data = json.loads(raw)
    return [Event.from_dict(d) for d in data]
