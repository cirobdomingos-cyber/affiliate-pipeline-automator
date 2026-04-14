# Deploying to Railway

**Two Railway services in one project, backed by a shared Railway Postgres
addon.** Both services run from the same Docker image (single Dockerfile
at repo root), but override the start command to play different roles:

| Service | Start command | Public URL serves |
|---|---|---|
| `affiliate-ui` | default (Dockerfile CMD → Streamlit) | Operator dashboard |
| `affiliate-api` | `sh -c "uvicorn backend.app.main:app --host 0.0.0.0 --port $PORT"` | `/r/{slug}`, `/bp/{slug}`, `/mailerlite/embed/*` |

Both services read/write the same Postgres database via the
`DATABASE_URL` env var, which Railway injects automatically when you
attach a Postgres addon to the project.

Locally nothing changes — `DATABASE_URL` is unset, so the app falls back
to the embedded DuckDB file at `./affiliate.duckdb`. See `db.py` for the
adapter that switches backends.

## Why Postgres

The previous attempt put both processes in one container behind nginx +
supervisord. It failed Railway health checks with no usable logs, and
the single-process Streamlit fallback lost the FastAPI public routes.
Postgres removes the "shared state" constraint that forced single-
container: each service opens its own connection to the managed
Postgres, no Volume sharing, no process co-location. It's also the
right long-term architecture for anything beyond a solo operator.

## One-time setup

### 1. Create Railway project

New Project → Deploy from GitHub repo → `affiliate-pipeline-automator`.
Railway reads `railway.toml`, builds from `Dockerfile`, and spins up the
first service. Rename it to `affiliate-ui`.

### 2. Add the Postgres addon

In the project (not the service): + New → **Database** → **Postgres**.
Railway provisions it in ~30 seconds and exposes `DATABASE_URL` as a
reference variable available to any service in the project.

### 3. Link `DATABASE_URL` to the UI service

`affiliate-ui` → Variables → + New Variable → **Add Reference** →
select the Postgres you just created → `DATABASE_URL`. Railway will
populate the value automatically on every deploy.

While you're in Variables, also set:

```
ANTHROPIC_API_KEY=sk-ant-...
REPLICATE_API_TOKEN=r8_...           # optional, creative generation
MAILERLITE_API_KEY=...               # optional, email sync
```

**Do NOT set `DATA_DIR`** — it's a DuckDB relic and ignored when
`DATABASE_URL` is present.

### 4. Expose the UI's public domain

`affiliate-ui` → Settings → Networking → Generate Domain. Copy the URL.

### 5. Create the API service

Back in the project: + New → **GitHub Repo** → same
`affiliate-pipeline-automator` repo. Rename it to `affiliate-api`.

In `affiliate-api` → Settings → **Custom Start Command**:

```
sh -c "uvicorn backend.app.main:app --host 0.0.0.0 --port $PORT"
```

In `affiliate-api` → Settings → **Healthcheck Path**: `/health`.

In `affiliate-api` → Variables, add the same reference to
`DATABASE_URL` (same Postgres) plus any API keys the API actually uses:

```
ANTHROPIC_API_KEY=sk-ant-...         # needed if you hit /traffic or LLM routes
```

### 6. Expose the API's public domain

`affiliate-api` → Settings → Networking → Generate Domain. Now you have
two URLs:

- `https://affiliate-ui-xxxx.up.railway.app` — operator dashboard
- `https://affiliate-api-xxxx.up.railway.app` — public redirects, bridge pages, embed form

### 7. Tell the UI where the API lives

Back in `affiliate-ui` → Variables, set:

```
APP_BASE_URL=https://affiliate-api-xxxx.up.railway.app
```

This is the URL the Streamlit UI builds links against — the copy-paste
`/r/{slug}` and `/bp/{slug}` URLs, the MailerLite iframe embed snippet.
If it's wrong, users who paste copied links will hit a dead domain.
Redeploy the UI after setting this.

### 8. Smoke test

```bash
# API health
curl https://affiliate-api-xxxx.up.railway.app/health
# {"status":"ok"}

# UI reachable
curl -I https://affiliate-ui-xxxx.up.railway.app/_stcore/health

# Create a managed product in the UI, create a tracked short link,
# click the copied /r/{slug} URL — it should 302-redirect.
```

## Local development

Unchanged. Don't set `DATABASE_URL`. The app uses DuckDB at the repo
root. Run `uvicorn backend.app.main:app --port 8000` and
`streamlit run ui/streamlit_app.py --server.port 8503` in separate
terminals, both talking to the same `affiliate.duckdb` file.

## Updating

Railway auto-deploys each service on every push to the branch it
tracks (default `main`). Bad deploy? Service → Deployments → pick a
previous green build → Redeploy. Postgres schema migrations are
additive only (the schema is re-applied on every connection via
`CREATE TABLE IF NOT EXISTS`), so code rollback is safe.

## What does NOT work yet

- **No auth on the UI.** Anyone with the Streamlit URL can edit your
  managed products. Add Streamlit basic auth or Railway private
  networking before sharing the URL.
- **API rate limiting.** `/r/{slug}` and `/bp/{slug}` are wide open;
  anyone hitting them counts as a click event. Add Cloudflare or
  a rate-limiter middleware before public launch.
- **No CI gate.** Every push deploys. Add GitHub Actions (`pytest` +
  `ruff`) as a merge gate before shared project stage.
