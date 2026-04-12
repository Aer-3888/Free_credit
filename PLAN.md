# Free Credit Event Scraper — Implementation Plan

## Overview

A Python scraper that runs daily on GitHub Actions, discovers public hackathons/workshops/events offering free cloud & LLM credits (AWS Bedrock, Azure OpenAI, GCP Vertex AI, etc.), and sends Discord notifications for new finds.

## Architecture

```
GitHub Actions (cron: 17 6 * * *)
        │
        ▼
┌──────────────────────────────┐
│   main.py (orchestrator)     │
│                              │
│  ┌─────────────────────┐     │
│  │  Scrapers (async)   │     │
│  │  ├─ devpost.py      │     │  httpx + selectolax
│  │  ├─ luma.py         │     │  (Playwright only
│  │  ├─ mlh.py          │     │   for aws_events.py)
│  │  ├─ eventbrite.py   │     │
│  │  └─ aws_events.py   │     │
│  └────────┬────────────┘     │
│           ▼                  │
│  ┌─────────────────────┐     │
│  │  scorer.py          │     │  Keyword proximity scoring
│  │  (credit detection) │     │
│  └────────┬────────────┘     │
│           ▼                  │
│  ┌─────────────────────┐     │
│  │  dedup.py           │     │  Compare with events.json
│  │  (new event filter) │     │
│  └────────┬────────────┘     │
│           ▼                  │
│  ┌─────────────────────┐     │
│  │  notifier.py        │     │  Discord webhook
│  │  (alerts)           │     │
│  └─────────────────────┘     │
└──────────────────────────────┘
        │
        ▼
  events.json (git commit if changed)
```

---

## Phase 1: Project Skeleton & Devpost Scraper

**Goal**: End-to-end pipeline working with one source.

### 1.1 — Project setup
- Init git repo, create branch `main`
- Project structure:
  ```
  free_credit/
  ├── src/
  │   ├── __init__.py
  │   ├── main.py              # Orchestrator: run scrapers → score → dedup → notify
  │   ├── models.py            # Event dataclass (immutable)
  │   ├── scrapers/
  │   │   ├── __init__.py
  │   │   ├── base.py          # Abstract scraper interface
  │   │   └── devpost.py       # Devpost API scraper
  │   ├── scorer.py            # Credit detection scoring engine
  │   ├── dedup.py             # Dedup against events.json
  │   └── notifier.py          # Discord webhook sender
  ├── data/
  │   └── events.json          # Persisted event data (git-scraped)
  ├── tests/
  │   ├── __init__.py
  │   ├── test_scorer.py
  │   ├── test_dedup.py
  │   ├── test_devpost.py
  │   └── fixtures/            # Sample API responses for testing
  │       └── devpost_response.json
  ├── .github/
  │   └── workflows/
  │       └── scrape.yml       # Daily cron workflow
  ├── pyproject.toml           # Dependencies & project config
  ├── README.md
  └── .gitignore
  ```
- Dependencies in `pyproject.toml`:
  ```
  httpx >= 0.28
  selectolax >= 0.4
  discord-webhook >= 1.4
  tenacity >= 8.0
  ```

### 1.2 — Event data model (`models.py`)
- Immutable dataclass:
  ```python
  @dataclass(frozen=True)
  class Event:
      id: str                  # Source-specific unique ID (e.g., "devpost:hackathon-slug")
      source: str              # "devpost" | "luma" | "mlh" | "eventbrite" | "aws"
      title: str
      url: str
      organizer: str
      description: str         # Summary/excerpt for scoring
      location: str            # City or "Online"
      start_date: str | None   # ISO 8601
      end_date: str | None     # ISO 8601
      registration_deadline: str | None
      sponsors: list[str]      # Extracted sponsor names
      prizes: str | None       # Prize info text
      credit_score: float      # 0.0–1.0, set by scorer
      credit_signals: list[str]  # Which keywords matched
      providers_detected: list[str]  # ["AWS", "Azure", ...]
      scraped_at: str          # ISO 8601 timestamp
  ```
- JSON serialization helpers (to_dict / from_dict)

### 1.3 — Scraper base class (`scrapers/base.py`)
- Abstract interface:
  ```python
  class BaseScraper(ABC):
      name: str
      
      @abstractmethod
      async def scrape(self) -> list[Event]: ...
  ```
- Shared httpx client factory with:
  - User-Agent rotation (3-4 realistic browser UAs)
  - Default timeout (30s)
  - Rate limiting via asyncio.Semaphore (max 3 concurrent)
  - Retry via tenacity (3 retries, exponential backoff)

### 1.4 — Devpost scraper (`scrapers/devpost.py`)
- Hit `https://devpost.com/api/hackathons?page={N}&status=open`
- Paginate until no more results (9 per page)
- Cap at ~50 pages (450 events) per run to stay respectful
- Extract: title, url, organization_name, submission_period_dates, displayed_location, themes
- For events whose title/org match provider keywords, fetch detail page HTML for full sponsor list
- Map to Event dataclass

### 1.5 — Credit scoring engine (`scorer.py`)
- Two-tier keyword system:
  ```python
  PROVIDERS = {
      "aws": ["aws", "amazon web services", "bedrock", "amazon bedrock"],
      "azure": ["azure", "microsoft azure", "azure openai"],
      "gcp": ["google cloud", "gcp", "vertex ai"],
      "anthropic": ["anthropic", "claude api", "claude"],
      "huggingface": ["hugging face", "huggingface", "zerogpu"],
      "fireworks": ["fireworks ai", "fireworks"],
  }
  
  CREDIT_SIGNALS = [
      # High confidence (0.4 each)
      "aws activate", "founders hub", "cloud credits provided",
      "api credits included", "free api access", "bedrock credits",
      "azure openai access", "vertex ai access",
      # Medium confidence (0.2 each)  
      "sponsored by", "powered by", "in partnership with",
      "credits", "free access", "workshop credits",
      "hands-on lab", "compute credits",
      # Low confidence (0.1 each)
      "hackathon", "workshop", "build-a-thon", "jam",
  ]
  ```
- Scoring algorithm:
  1. Check if any PROVIDER keyword found in title + description + sponsors → flag providers
  2. Check CREDIT_SIGNALS proximity: provider keyword within 200 chars of credit signal = bonus
  3. Normalize score to 0.0–1.0
  4. Threshold: score >= 0.3 = include in results

### 1.6 — Dedup engine (`dedup.py`)
- Load existing `data/events.json`
- Compare by `event.id` (source:slug composite key)
- Return only new events not seen before
- Merge new events into existing data
- Prune events older than 90 days
- Save updated JSON (pretty-printed for git diffs)

### 1.7 — Discord notifier (`notifier.py`)
- Send rich embed per new event:
  ```
  Title: [Event Name]
  URL: link
  Fields:
    - Providers: AWS, Azure
    - Score: 0.85
    - Signals: "AWS Activate", "cloud credits provided"
    - Dates: May 15–17, 2026
    - Location: Nantes, France
  Color: green (score > 0.7), yellow (0.3–0.7)
  ```
- Batch: max 10 embeds per message (Discord limit)
- If >10 new events, send multiple messages
- Read webhook URL from `DISCORD_WEBHOOK_URL` env var

### 1.8 — Orchestrator (`main.py`)
- Async main:
  ```python
  async def main():
      scrapers = [DevpostScraper()]
      all_events = []
      for scraper in scrapers:
          events = await scraper.scrape()
          all_events.extend(events)
      
      scored = [score_event(e) for e in all_events]
      filtered = [e for e in scored if e.credit_score >= THRESHOLD]
      new_events = dedup(filtered)
      
      if new_events:
          notify(new_events)
          save_events(new_events)
  ```

### 1.9 — GitHub Actions workflow (`.github/workflows/scrape.yml`)
- Cron: `17 6 * * *` (6:17 AM UTC daily)
- Also: `workflow_dispatch` for manual triggers
- Steps:
  1. Checkout repo
  2. Setup Python 3.12
  3. Install dependencies (`pip install -e .`)
  4. Run `python -m src.main`
  5. If `data/events.json` changed → git add, commit, push
- Secrets: `DISCORD_WEBHOOK_URL`

### 1.10 — Tests
- `test_scorer.py`: Unit tests for scoring logic with known inputs
- `test_dedup.py`: Dedup logic with fixture data
- `test_devpost.py`: Parse sample API response fixture → verify Event mapping
- No live HTTP calls in tests — use saved fixtures

---

## Phase 2: Additional Scrapers

**Goal**: Cover 3 more sources.

### 2.1 — MLH scraper (`scrapers/mlh.py`)
- GET `https://www.mlh.com/seasons/2026/events`
- Parse static HTML with selectolax
- Extract: event name, dates, location, link
- No sponsor data on listing → score based on event title/description only
- Auto-detect season year from current date

### 2.2 — Luma scraper (`scrapers/luma.py`)
- Use Luma public API (requires API key)
- Search for events with AI/tech/hackathon keywords
- Extract: title, description, date, location, host
- Store `LUMA_API_KEY` as GH Actions secret

### 2.3 — Eventbrite scraper (`scrapers/eventbrite.py`)
- Search Eventbrite for "hackathon", "AI workshop", "cloud credits"
- Parse search results HTML (no public API without OAuth)
- Extract: title, url, date, location, organizer
- Limit to first 3 search queries per run

### 2.4 — Update orchestrator
- Register all new scrapers in `main.py`
- Run all scrapers concurrently with `asyncio.gather()`
- Add per-scraper error isolation (one failing doesn't kill others)

---

## Phase 3: Robustness & Polish

**Goal**: Production-grade reliability.

### 3.1 — Playwright for AWS Events (`scrapers/aws_events.py`)
- Add `playwright` as optional dependency
- Scrape `aws.amazon.com/events` with headless Chromium
- Only install Playwright in GH Actions when needed (conditional step)
- Extract: event name, type, date, "Free" badge, registration link

### 3.2 — Keepalive workflow
- Add `gautamkrishnar/keepalive-workflow` action
- Runs every 50 days to prevent auto-disable

### 3.3 — Error reporting
- If a scraper fails, send a yellow/orange Discord embed with the error
- Don't fail the whole workflow for one broken scraper

### 3.4 — Rate limiting & respect
- Add `robots.txt` checking per domain (optional)
- Configurable delay between requests (default 1-2s)
- Max pages per source configurable via env vars

### 3.5 — Data quality
- Deduplicate across sources (same event on Devpost + Eventbrite)
  - Cross-source dedup by fuzzy title matching (simple: lowercase + strip → match)
- Add `last_seen` timestamp to track if events are still listed
- Mark events as "expired" when past end_date

---

## Phase 4: Enhancements (Future)

**Goal**: Smarter detection, broader coverage.

### 4.1 — LLM classification (optional)
- For ambiguous events (score 0.3–0.5), use Haiku to classify
- Prompt: "Does this event offer free cloud/LLM credits? Extract provider and amount."
- Only call LLM for ~10-20 borderline events per run (~$0.01/run)
- Add `ANTHROPIC_API_KEY` as GH Actions secret

### 4.2 — Google search integration
- Periodic Google search for: `"AWS credits" hackathon 2026`, `"free Bedrock" workshop`
- Parse top 10 results per query
- Feed URLs into scoring pipeline

### 4.3 — Telegram bot alternative
- Add Telegram as optional notification channel
- Configure via `TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID`

### 4.4 — Web dashboard
- Simple static site (GitHub Pages) showing current events.json
- Sortable table with filters by provider, score, date

---

## Implementation Order & Estimates

| Phase | Scope | Files |
|-------|-------|-------|
| **Phase 1** | Skeleton + Devpost + scoring + dedup + Discord + GH Actions | ~12 files |
| **Phase 2** | MLH + Luma + Eventbrite scrapers | ~4 files |
| **Phase 3** | Playwright + keepalive + error handling + data quality | ~3 files modified |
| **Phase 4** | LLM classification + Google search + Telegram + dashboard | Future |

## Key Design Decisions

1. **Immutable dataclasses** — Events are frozen; scoring creates new instances
2. **Async-first** — All scrapers are async, run concurrently via asyncio.gather
3. **Fail-safe isolation** — Each scraper wrapped in try/except; one failure doesn't block others
4. **Git-scraping pattern** — events.json committed to repo = free persistence + audit trail
5. **Scoring over binary** — Gradient scoring with threshold avoids false positive/negative cliff
6. **No framework** — httpx + selectolax is lighter and more flexible than Scrapy for 5 sources
7. **Playwright isolated** — Only installed/used for the one source that needs JS rendering
