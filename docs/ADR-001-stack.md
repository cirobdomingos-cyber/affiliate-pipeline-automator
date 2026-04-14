# ADR-001 — Stack & Module Boundaries

Status: Accepted
Date: 2026-04-13

## Context

We are building a system that automates the full Brazilian affiliate marketing
pipeline across 7 stages: product discovery, link management, traffic planning,
funnel building, email nurture, tracking, and scale. The system must be
incremental (ship an MVP slice first), deployable to Railway, and built on a
Windows 11 dev machine without Docker.

## Decisions

### 1. Backend — FastAPI + Python 3.12

**Why:** every other module downstream (LLM orchestration, scrapers, scoring)
is Python-native. FastAPI gives us async HTTP clients for scraping, pydantic
for typed domain models, and an OpenAPI surface we can hand to a Next.js
frontend in V1 without rewriting anything.

**Alternative considered:** Node/Next.js API routes. Rejected — scraping and
ML-adjacent code lives better in Python, and we want one language end-to-end.

**Interview angle:** "I picked FastAPI because the hot path is I/O-bound
scraping and LLM calls — async matters more than framework features. Pydantic
as the contract layer means the same models validate HTTP, DB rows, and LLM
tool inputs."

### 2. Frontend — Streamlit for MVP, migrate to Next.js in V1

**Why (MVP):** this is an internal operator tool for a single user (me). The
MVP is about iterating on the data pipeline, not UX polish. Streamlit lets me
ship a working UI in tens of lines and change it as the data model evolves.

**Why migrate later:** once the 7 stages stabilize, a Next.js frontend lets us
build proper dashboards, auth, and a polished demo for the portfolio.

**Interview angle:** "I don't pick Next.js by default — I pick it when the
problem is customer-facing UI. For an internal analytics tool, Streamlit is
the right tool for weeks 1–4, and migrating later is cheap because FastAPI is
the contract."

### 3. Storage — DuckDB (local), Postgres-ready via SQLAlchemy

**Why:** DuckDB is columnar, zero-config, handles analytical queries at
millions of rows on a laptop, and reads Parquet natively. It is a better fit
for "score and rank thousands of scraped products" than SQLite. It also
matches my stated learning goal (analytics engineering, DuckDB).

We model tables via SQLAlchemy so the same schema can target Postgres on
Railway without a rewrite.

**Alternative considered:** SQLite. Rejected — rank/window queries over
product catalogs are a core workload, and DuckDB wins on those by a wide
margin.

**Interview angle:** "DuckDB is OLAP, SQLite is OLTP. Affiliate product
scoring is OLAP — aggregations, window functions, joining scraped snapshots
over time. The workload dictates the engine."

### 4. LLM — Anthropic SDK, Claude Sonnet + Haiku, prompt caching on by default

**Why:** Sonnet for reasoning-heavy work (niche fit analysis, email sequence
generation, ad copy). Haiku for bulk classification (scoring hints, tagging
thousands of products). Prompt caching is mandatory — the product taxonomy
and niche definitions are reused across every call and should be cached.

**Interview angle:** "Two-tier model routing is how you keep LLM costs sane
at scale. You write one system prompt with the taxonomy, cache it, and then
fan out thousands of Haiku classifications against the cached prefix."

### 5. Scraping — httpx + selectolax, Playwright only when forced

**Why:** httpx gives us async HTTP and connection pooling. selectolax is a
fast CSS parser (5–10x BeautifulSoup). Playwright is the fallback for
JS-rendered pages — expensive and fragile, so we reach for it last.

### 6. Scheduling — none in MVP, APScheduler in V1

**Why:** MVP runs discovery on-demand from the UI. V1 adds a daily cron to
re-scrape catalogs. We defer Celery/Redis until there is a real reason.

## Module boundaries

```
backend/app/
  scrapers/     # One module per platform. All implement ScraperProtocol.
  scoring.py    # Pure functions. No I/O. Fully unit-tested.
  services/     # Orchestration — calls scrapers, persists, invokes LLM.
  api/          # FastAPI routers. Thin — delegates to services.
  models.py     # Pydantic domain models. The contract.
  db.py         # DuckDB connection + schema.
  llm.py        # Anthropic client with prompt caching wired in.
```

**Rule:** `scoring.py` and `models.py` have zero I/O dependencies. They are
pure and trivially testable. Everything that touches the network lives in
`scrapers/` or `services/`.

**Portfolio signal:** this separation is what interviewers call "hexagonal
architecture" or "ports and adapters" — the core domain (scoring) doesn't
know or care where products came from. Swapping Hotmart for Amazon is a new
adapter, not a rewrite.

## Data model (MVP)

```python
Product:
  id: str                    # Stable hash of (platform, external_id)
  platform: Platform         # Enum: HOTMART, MONETIZZE, EDUZZ, AMAZON, ...
  external_id: str           # Platform-native ID
  name: str
  url: str
  category: str | None
  price_brl: float | None
  commission_pct: float | None    # 0.0 – 100.0
  commission_brl: float | None    # Derived if pct + price known
  popularity: float | None        # Platform-native metric (Hotmart gravity, etc.)
  producer_name: str | None
  producer_reputation: float | None  # 0.0 – 1.0
  sales_page_signals: float | None   # 0.0 – 1.0, set by LLM in V1
  scraped_at: datetime
  raw: dict                  # Full scraper payload for audit

ProductScore:
  product_id: str
  score: float               # 0.0 – 100.0
  components: dict[str, float]  # Breakdown for explainability
  computed_at: datetime
```
