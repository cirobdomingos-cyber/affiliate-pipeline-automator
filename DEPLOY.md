# Deploying to Railway

Ship the app as **two services** in a single Railway project, both pointing
at this same repo and sharing one Railway Volume for the DuckDB file.

| Service | Start command | Purpose |
|---|---|---|
| `affiliate-api` | `uvicorn backend.app.main:app --host 0.0.0.0 --port $PORT` | Public `/r/{slug}` redirects, `/bp/{slug}` bridge pages, admin API |
| `affiliate-ui` | `streamlit run ui/streamlit_app.py --server.port $PORT --server.address 0.0.0.0 --server.headless true --browser.gatherUsageStats false` | Operator dashboard (admin) |

`railway.toml` at repo root defines the `affiliate-api` defaults
(Nixpacks builder, `/health` healthcheck, the uvicorn start command). The
UI service uses the same repo but overrides the start command in the Railway
dashboard.

## Why two services

FastAPI and Streamlit are two different web servers — neither proxies the
other, and Railway exposes one public port per service. The FastAPI service
must be publicly reachable because affiliate links (`/r/{slug}`) and bridge
pages (`/bp/{slug}`) are pasted into external sites. Streamlit is operator-
only, lives at a separate URL, and talks to the same DuckDB file (not
via HTTP) so the two services never need to call each other.

## One-time setup

1. **Create Railway project** (web UI): New Project → Deploy from GitHub
   repo → pick `affiliate-pipeline-automator`. Railway creates the first
   service automatically; rename it to `affiliate-api`. It reads
   `railway.toml` and starts FastAPI.

2. **Create the UI service** in the same project: + New → GitHub Repo →
   same repo. Rename it `affiliate-ui`. In Settings → Deploy, set
   **Custom Start Command**:

   ```
   streamlit run ui/streamlit_app.py --server.port $PORT --server.address 0.0.0.0 --server.headless true --browser.gatherUsageStats false
   ```

3. **Create a Volume** in the project: + New → Volume. Name it `data`,
   size 1 GB. Mount it at `/data` on **both** services (attach under each
   service's Settings → Volumes).

4. **Set environment variables** on both services (copy from local `.env`,
   see `.env.example`). Required on both:

   ```
   ANTHROPIC_API_KEY=sk-ant-...
   DATA_DIR=/data
   APP_BASE_URL=https://<affiliate-api-public-url>.up.railway.app
   ```

   Optional (for Stage 3 creatives and Stage 5 email):

   ```
   REPLICATE_API_TOKEN=r8_...
   MAILERLITE_API_KEY=...
   ```

   `APP_BASE_URL` should be the public URL of the **api** service, not the
   UI service — that's where `/r/` and `/bp/` live.

5. **Expose public domains**: each service → Settings → Networking →
   Generate Domain. You'll get two URLs:
   - `https://affiliate-api-production-xxxx.up.railway.app` (public endpoints)
   - `https://affiliate-ui-production-xxxx.up.railway.app` (your admin dashboard)

6. **Smoke test**:

   ```bash
   curl https://affiliate-api-*.up.railway.app/health
   # {"status":"ok"}

   # Open the UI in a browser, create a managed product, create a tracked
   # short link, hit the short link URL — it should 302-redirect.
   ```

## Updating

Railway auto-deploys on push to the branch each service tracks. Default is
`main`. To stage before promoting:

- Create a `staging` branch, point one copy of `affiliate-api` + `affiliate-ui`
  services at it, point the production services at `main`. Merge `staging → main`
  when ready.

## Rollback

Railway keeps deployment history per service. From the dashboard: service →
Deployments → pick a previous green deployment → Redeploy. Because the DuckDB
schema is additive-only (no destructive migrations), rolling back code is
safe even if schema has moved forward.

## What does NOT work yet

- **No auth on Streamlit.** The UI is publicly reachable once you generate a
  domain. Either keep the URL private or add Streamlit basic auth / Railway
  private networking before sharing the URL anywhere.
- **DuckDB single-writer constraint.** Both services write to the same file.
  Streamlit writes to managed products, short links, bridge pages, etc.;
  FastAPI writes click events. These rarely collide in practice but under
  contention you may see transient `IOError` on writes. Postgres migration
  is the fix; see the Industrialization plan.
- **No CI.** Every push deploys. Add GitHub Actions (`pytest` + `ruff`) as
  a merge gate before making this a shared project.
