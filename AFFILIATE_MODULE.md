# Affiliate Module

End-to-end 7-stage affiliate marketing pipeline built on top of the existing
affiliate-pipeline-automator scaffold. FastAPI backend, Streamlit operator UI,
DuckDB persistence. Single-operator (no auth), ready to port to Postgres when
deployed.

## Stages at a glance

| # | Stage | UI tab | Key endpoints |
|---|---|---|---|
| 1 | Onboarding + manual product catalog | Onboarding, My Products | `/onboarding`, `/managed-products` |
| 2 | Tracked short links with click logging | My Products → Tracked short links | `/short-links`, `/r/{slug}` |
| 3 | Per-product channel config + stale alerts | My Products → Traffic channels | `/managed-products/{id}/channels`, `/alerts/stale-channels` |
| 4 | Bridge page builder + public renderer | Bridge Pages | `/bridge-pages`, `/bp/{slug}`, `/bp/{slug}/click` |
| 5 | MailerLite opt-in form + nurture sequences | Email | `/email-sequences`, `/mailerlite/embed/{id}`, `/mailerlite/subscribe`, `/mailerlite/sync-counts` |
| 6 | KPI dashboard + 30-day time series | KPIs | `/dashboard/kpis`, `/dashboard/timeseries` |
| 7 | Scale readiness checklist | Scale | `/managed-products/{id}/scale-readiness` |

## New tables (DuckDB)

Added to `backend/app/db.py::_SCHEMA`. All share the same `affiliate.duckdb`
file with the existing tables.

| Table | Purpose | Written by |
|---|---|---|
| `managed_products` | Hand-curated catalog (separate from scraped `products`) | Stage 1 |
| `operator_profile` | Single-row onboarding preference | Stage 1 |
| `short_links` | `/r/{slug}` redirects with pre-composed UTM URLs | Stage 2 |
| `click_events` | Append-only click log. `target_type ∈ {short_link, bridge_view, bridge_cta}` | Stages 2, 4 |
| `channel_configs` | One row per (product, channel) — status, budget, goal | Stage 3 |
| `bridge_pages` | Landing pages served at `/bp/{slug}` | Stage 4 |
| `email_sequences` / `email_sequence_steps` | Nurture sequence header + steps | Stage 5 |
| `subscriber_counts` | Snapshot of MailerLite subscribers per product | Stage 5 |

### Design notes

- **`managed_products` vs `products`.** The scraped catalog (`products`) is
  overwritten on every discovery run. The manual catalog lives in a separate
  table so operator-authored rows are never clobbered. The scrape pipeline
  stays idempotent and the operator's working set stays stable.
- **`click_events` is append-only.** Every aggregation — KPI counts, stale
  alerts, time series — is a `SELECT COUNT(*) GROUP BY` over this single
  table. DuckDB's columnar storage handles this comfortably for any volume a
  single operator produces. No derived tables, no snapshotting, no
  cache-invalidation bugs.
- **Click anonymization.** Only the first 3 octets of the IPv4 address are
  stored (`ip_prefix`), and the user-agent is collapsed to one of four
  coarse buckets (`mobile-ios`, `mobile-android`, `desktop`, `bot`). LGPD
  friendly and keeps the table small.

## Routes

### Public (no auth)

| Method | Path | Description |
|---|---|---|
| GET | `/r/{slug}` | Log click, 302 redirect to the UTM-composed affiliate URL |
| GET | `/bp/{slug}` | Log `bridge_view`, render bridge page HTML |
| GET | `/bp/{slug}/click` | Log `bridge_cta`, 302 redirect to `cta_url` |
| GET | `/mailerlite/embed/{managed_product_id}` | Self-contained opt-in form (meant to be iframed) |
| POST | `/mailerlite/subscribe` | Form submission handler — pushes to MailerLite |

### Internal (operator)

| Method | Path | Description |
|---|---|---|
| GET, POST | `/onboarding` | Read/save operator's primary niche |
| GET, POST, PATCH, DELETE | `/managed-products` | CRUD |
| POST, GET, DELETE | `/short-links` | Create tracked link, list, delete |
| GET | `/short-links/{slug}/stats` | Total, unique, by utm_source |
| GET, PUT, DELETE | `/managed-products/{id}/channels` | Channel config per product |
| GET | `/alerts/stale-channels` | Paid active channels with 0 clicks in 24h |
| POST, GET, PATCH, DELETE | `/bridge-pages` | CRUD |
| POST, GET, PATCH, DELETE | `/email-sequences` | CRUD |
| POST | `/mailerlite/sync-counts` | Refresh subscriber snapshot for all products |
| GET | `/dashboard/kpis` | Per-product metrics, EPC-ranked |
| GET | `/dashboard/timeseries` | Daily click counts for one product, last N days |
| GET | `/managed-products/{id}/scale-readiness` | Checklist + suggestions |

## Environment variables

| Variable | Required | Purpose |
|---|---|---|
| `ANTHROPIC_API_KEY` | yes | LLM analyzer, traffic planner (pre-existing) |
| `MAILERLITE_API_KEY` | if using Stage 5 | Bearer token for MailerLite v2 API |
| `APP_BASE_URL` | no (default `http://localhost:8000`) | Absolute URL used when generating `/r/{slug}` and `/bp/{slug}` links in the UI |

All three are read lazily (at request time, not import time), so missing keys
don't break imports or unrelated tabs.

## Running it

```bash
# backend
uvicorn backend.app.main:app --host 127.0.0.1 --port 8000 --reload

# UI
streamlit run ui/streamlit_app.py --server.port 8503
```

The UI has direct DuckDB access for its own tabs and also generates
`{APP_BASE_URL}/r/{slug}` and `/bp/{slug}` URLs that resolve against the
running FastAPI instance. Run both.

## File map

```
backend/app/
  api/
    managed_products.py    # Stage 1
    shortener.py           # Stage 2 (+ public /r)
    channels.py            # Stage 3 (+ /alerts)
    bridge_pages.py        # Stage 4 (+ public /bp)
    email.py               # Stage 5 (+ /mailerlite, /email-sequences)
    dashboard.py           # Stage 6
    scale.py               # Stage 7
  services/
    shortener.py           # slug gen, UTM compose, IP/UA anon
    mailerlite.py          # thin httpx client, lazy key
  models.py                # all new pydantic models
  db.py                    # all new DuckDB tables + repositories
  scoring.py               # score_managed_product (0.4 comm + 0.3 ticket + 0.3 trust)
ui/streamlit_app.py        # 6 new tabs: Onboarding, My Products, Bridge Pages, Email, KPIs, Scale
```

## Extending

- **Scheduler for email sequences**: the step table (`email_sequence_steps`) is
  ready to be read by a dispatcher. Add APScheduler / Celery; no schema
  changes needed.
- **Postgres migration**: swap `backend/app/db.py` for a SQLAlchemy layer.
  Every model is pure pydantic and every repo has a single public interface
  — the UI and API never touch raw DuckDB.
