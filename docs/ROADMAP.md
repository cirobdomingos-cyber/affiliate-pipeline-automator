# Roadmap — MVP → V1 → V2

The 7 stages from the project brief, mapped to phases.

## MVP (this PR — weeks 1–2)

**Goal:** a single operator can discover and rank Hotmart products from a
Streamlit UI. End-to-end slice of Stage 1 only — proves the architecture.

- [x] Stage 1: Product discovery (Hotmart scraper + scoring + UI)
- [x] DuckDB persistence
- [x] Pure scoring module with unit tests
- [x] FastAPI `/products` endpoint
- [x] Streamlit browser UI

Out of scope for MVP: LLM niche analysis, other platforms, scheduling, auth.

## V1 (weeks 3–6) — discovery is solid, links and traffic unlock

- [ ] Stage 1: Add Monetizze, Eduzz, Amazon Associates scrapers
- [ ] Stage 1: LLM niche fit analysis (Claude Sonnet, cached taxonomy prompt)
- [ ] Stage 1: LLM sales-page signal extraction (Haiku, bulk)
- [ ] Stage 2: Affiliate link vault with UTM builder and approval tracking
- [ ] Stage 3: Traffic channel planner — organic content calendars and paid
      ad brief generators (LLM-powered)
- [ ] APScheduler daily re-scrape
- [ ] Next.js frontend scaffolded alongside Streamlit (parallel, not a rewrite)
- [ ] Deploy to Railway with Postgres

## V2 (weeks 7–12) — full pipeline closes the loop

- [ ] Stage 4: Bridge page generator from product metadata
- [ ] Stage 5: Email nurture sequence generator + MailerLite/Brevo integration
- [ ] Stage 6: Tracking dashboard unifying GA4, UTM, and affiliate platform data
- [ ] Stage 7: Winner detection + scale recommendations
- [ ] Multi-user auth
- [ ] Replace Streamlit with Next.js as primary UI

## Why this order

Stages 1 → 2 → 3 deliver value even without 4–7: a user with great product
picks, tracked links, and a content plan can already make money manually. The
MVP must prove we can reliably find winning products, because everything
downstream assumes that input is good. Starting anywhere else is building on
sand.
