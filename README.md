# Affiliate Pipeline Automator

**The problem:** the Brazilian affiliate marketing market is large and noisy.
A would-be affiliate has to manually discover good products across half a
dozen platforms (Hotmart, Monetizze, Eduzz, Amazon, Shopee, Magalu, plus
SaaS programs), guess which are worth promoting, build bridge pages, write
ad copy, set up email nurture, and instrument tracking — all before they
know if any of it will earn anything. Most quit somewhere in the middle.

**This project automates the full pipeline.** It takes a user from
"I want to make money as an affiliate" to "I have a running, tracked,
optimizing funnel" by collapsing seven manual stages into one tool.

The seven stages — each a module in this codebase:

1. **Niche & product discovery** (Stage 1 — shipped in MVP)
2. Affiliate link management
3. Traffic channel planner (organic + paid)
4. Conversion funnel builder (bridge pages)
5. Email list & nurture automation
6. Tracking & optimization dashboard
7. Scale engine (winner detection + auto-scaling)

See [docs/ROADMAP.md](docs/ROADMAP.md) for what lands in MVP, V1, and V2.

## What the MVP does today

Stage 1 is fully wired:

- Scrape Hotmart's marketplace (with `MockScraper` fallback so the demo always works)
- Score every product on five dimensions: commission %, ticket size,
  producer reputation, popularity, and sales-page signals
- Compute an EPC heuristic (expected earnings per visit) so two products with
  the same score can still be compared on absolute earning potential
- Persist everything to DuckDB
- Browse top picks in a Streamlit UI with per-component score breakdowns
- FastAPI HTTP surface (`/products/discover`, `/products/top`) for the V1 frontend migration

## Running it

```bash
py -3.12 -m venv .venv
.venv\Scripts\activate
pip install -e ".[dev]"

# Run the test suite
pytest

# Run discovery from the CLI with mock data
py -3.12 scripts/run_discovery.py --mock

# Launch the Streamlit operator UI
streamlit run ui/streamlit_app.py

# Launch the FastAPI backend (separate terminal)
uvicorn backend.app.main:app --reload
```

## Architecture in one paragraph

A pure scoring core (`backend/app/scoring.py`) and pure domain models
(`backend/app/models.py`) sit at the center. Around them are adapters:
scrapers (`backend/app/scrapers/`) implement a single `ScraperProtocol` so
adding Monetizze or Amazon is a closed change; persistence
(`backend/app/db.py`) talks to DuckDB but exposes a `ProductRepository`
interface so we can swap to Postgres on Railway without touching anything
else; the API (`backend/app/api/`) and Streamlit UI both call the same
`services/discovery.py` orchestration. This is a ports-and-adapters layout —
the kind interviewers ask about and the kind that actually pays off when V1
adds five more platforms.

Full design rationale and tradeoffs: [docs/ADR-001-stack.md](docs/ADR-001-stack.md).

## Tech stack

| Layer | Choice | Why |
|---|---|---|
| Backend | FastAPI + Python 3.12 | Async, typed, same language as scrapers and LLM code |
| Frontend (MVP) | Streamlit | Internal tool, ship in days not weeks |
| Frontend (V1) | Next.js 14 + TypeScript | Polished demo once schema stabilizes |
| Storage | DuckDB | OLAP workload (rank/window over scraped catalogs); Postgres-ready |
| Scraping | httpx + selectolax | Async + fast HTML parsing; Playwright only when forced |
| LLM | Anthropic SDK (Sonnet + Haiku) | Two-tier routing with prompt caching for cost control |
| Tests | pytest | Fast, pure-function unit tests on the scoring core |

## Why this architecture is portfolio-grade

- **Pure core, dirty edges.** Scoring and models have zero I/O. They're
  unit-tested in milliseconds. Everything that touches the network is
  isolated behind a Protocol.
- **Closed for modification, open for extension.** Adding a new platform
  means writing one class and registering it. Nothing else changes.
- **Storage migration is a config change.** DuckDB now, Postgres on Railway
  later — same `ProductRepository` interface.
- **Two-tier LLM routing.** Sonnet for reasoning, Haiku for bulk, cached
  prompts for the shared taxonomy. The pattern that keeps real production
  AI products affordable.

## Repository layout

```
affiliate-pipeline-automator/
├── docs/
│   ├── ADR-001-stack.md       # Architectural decisions + tradeoffs
│   └── ROADMAP.md             # MVP → V1 → V2 phasing
├── backend/
│   ├── app/
│   │   ├── models.py          # Pure pydantic domain models
│   │   ├── scoring.py         # Pure scoring functions (no I/O)
│   │   ├── scrapers/          # One adapter per platform
│   │   ├── services/          # Orchestration (discovery, etc.)
│   │   ├── api/               # FastAPI routers — thin
│   │   ├── db.py              # DuckDB repository
│   │   └── main.py            # FastAPI app
│   └── tests/                 # pytest, no network
├── ui/
│   └── streamlit_app.py       # Operator UI for browsing scored products
├── scripts/
│   └── run_discovery.py       # CLI entrypoint for cron / smoke tests
└── pyproject.toml
```
