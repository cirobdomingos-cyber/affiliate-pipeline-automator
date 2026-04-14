# Deploying to Railway

One Railway service, one Dockerfile, one Volume. Both FastAPI and Streamlit
run inside the same container managed by supervisord; nginx terminates
on `$PORT` and reverse-proxies to each based on the request path.

## Why single-service

Railway's free and hobby tiers don't allow sharing a single Volume between
two services, and the DuckDB file is the shared-state anchor between the
FastAPI backend and the Streamlit admin UI. Single-container co-location is
the cheapest way around that: one volume, one filesystem, one public URL
that serves everything.

If you ever upgrade to a tier that supports cross-service volume sharing
(or you migrate to Railway Postgres), you can split this into two services
without touching application code — the container routing logic is all in
`deploy/nginx.conf.template` and unrelated to the Python app.

## How traffic is routed inside the container

| Path | Proxied to | Why |
|---|---|---|
| `/r/{slug}` | uvicorn `:8000` | Public redirect, tracked |
| `/bp/{slug}`, `/bp/{slug}/click` | uvicorn `:8000` | Public bridge pages |
| `/health` | uvicorn `:8000` | Railway healthcheck |
| `/docs`, `/openapi.json` | uvicorn `:8000` | API docs |
| `/mailerlite/*` | uvicorn `:8000` | Embed form + opt-in |
| everything else | streamlit `:8501` | Operator UI (includes WebSockets) |

nginx config is in [deploy/nginx.conf.template](deploy/nginx.conf.template).
supervisord config is in [deploy/supervisord.conf](deploy/supervisord.conf).

## One-time setup

1. **Create Railway project** (web UI): New Project → Deploy from GitHub
   repo → pick `affiliate-pipeline-automator`. Railway detects
   `railway.toml`, reads `Dockerfile`, and starts building. First build
   takes 3–5 minutes.

2. **Create a Volume**: + New → Volume. Name it `data`, size 1 GB.
   Attach it to the service with mount path `/data`. This is where
   `affiliate.duckdb` lives across deploys.

3. **Set environment variables** (Variables tab on the service). Required:

   ```
   ANTHROPIC_API_KEY=sk-ant-...
   DATA_DIR=/data
   APP_BASE_URL=https://<your-service-public-url>.up.railway.app
   ```

   `APP_BASE_URL` must match the public domain of *this* service. It's
   used in the Streamlit UI to build copy-paste `/r/{slug}` and
   `/bp/{slug}` URLs; if it's wrong, the copied links won't resolve.

   Optional:

   ```
   REPLICATE_API_TOKEN=r8_...          # Stage 3 creative generation
   CREATIVE_PROVIDER=replicate         # force provider when both are set
   REPLICATE_IMAGE_MODEL=...           # override default model
   REPLICATE_VIDEO_MODEL=...
   MAILERLITE_API_KEY=...              # Stage 5 email
   FAL_API_KEY=...                     # alternative creative provider
   ```

4. **Expose a public domain**: Settings → Networking → Generate Domain.
   You'll get `https://<something>.up.railway.app`. Update
   `APP_BASE_URL` in step 3 to match, then redeploy.

5. **Smoke test**:

   ```bash
   curl https://<your-url>.up.railway.app/health
   # {"status":"ok"}

   # Open the root URL in a browser — should render the Streamlit UI.
   # Create a managed product, create a tracked short link, hit the
   # copied /r/{slug} URL — it should 302-redirect to the affiliate URL.
   ```

## Local development parity

The local dev loop is unchanged — you keep running `uvicorn` and
`streamlit` separately on ports 8000 and 8503. The Dockerfile and
`deploy/` directory are only used on Railway. `DATA_DIR` defaults to the
repo root locally, so `affiliate.duckdb` stays where you're used to.

To test the production container locally:

```bash
docker build -t affiliate-pipeline .
docker run --rm -p 8080:8080 \
  -e PORT=8080 \
  -e DATA_DIR=/tmp/data \
  -e ANTHROPIC_API_KEY=sk-ant-... \
  -v $PWD/.tmp-data:/tmp/data \
  affiliate-pipeline
```

Then hit `http://localhost:8080/` for the UI and
`http://localhost:8080/health` for the API.

## Updating

Railway auto-deploys on push to the branch the service tracks (default
`main`). Change to a staging branch if you want a promotion gate.

## Rollback

Railway → service → Deployments → pick a previous green build → Redeploy.
Because the DuckDB schema is additive-only (no destructive migrations),
rolling back code is safe even if the schema has moved forward.

## What does NOT work yet

- **No auth on Streamlit.** The operator UI is publicly reachable once
  the domain is generated. Either keep the URL private, or add Streamlit
  basic auth / Railway private networking before sharing it anywhere.
- **DuckDB single-writer constraint.** Two processes (uvicorn for click
  events and streamlit for operator writes) share one file. They rarely
  collide in practice but under heavy contention you may see a transient
  `IOError` on writes. Postgres migration is the permanent fix.
- **No CI gate.** Every push deploys. Add GitHub Actions (`pytest` +
  `ruff`) as a merge gate before making this a shared project.
- **Container memory**. Three processes (nginx + uvicorn + streamlit) in
  one container use ~400–600 MB at idle. Railway's free tier caps at
  512 MB per service — you may need to upgrade to Hobby ($5/mo) if the
  service gets OOM-killed during creative generation spikes.
