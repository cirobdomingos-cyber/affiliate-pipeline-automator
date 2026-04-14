# Deploying to Railway

One Railway service, one Docker container, one process: **Streamlit**.
Railway probes Streamlit's native `/_stcore/health` endpoint for liveness.
Simple, boring, ships.

## What this deploy includes — and what it doesn't

| Feature | Works on Railway? |
|---|---|
| Streamlit operator dashboard (all 7 phases) | ✅ |
| Creative brief generation (Haiku) | ✅ |
| Direct image/video generation (Replicate / fal.ai) | ✅ |
| MailerLite sync | ✅ |
| DuckDB persistence (via Volume) | ✅ |
| **Public `/r/{slug}` tracked redirects** | ❌ not exposed |
| **Public `/bp/{slug}` bridge pages** | ❌ not exposed |
| **`/mailerlite/embed/{id}` iframe form** | ❌ not exposed |

The FastAPI public routes (click tracker and bridge pages) require uvicorn
to be bound to the public port. This container runs Streamlit on the
public port, and Streamlit has no way to host FastAPI endpoints inline.
Running both processes under nginx + supervisord was tried first and
failed to boot in Railway's environment with no diagnostic logs — the
pivot to single-process was the pragmatic move.

**To actually use `/r/{slug}` and `/bp/{slug}` in production**, one of:
1. Add a second Railway service running FastAPI + a Railway Postgres
   addon (not Volume). When the app's `db.py` is ported to Postgres, the
   two services can run concurrently without the Volume-sharing constraint
   that killed the first attempt.
2. Deploy the FastAPI service to a different platform (Fly.io, Render)
   that allows multi-process containers more predictably.

Locally, everything still works end-to-end — `uvicorn backend.app.main:app`
hosts the public routes on port 8000, `streamlit run ui/streamlit_app.py`
hosts the UI on 8503, both read the same local DuckDB file.

## One-time setup

1. **Create Railway project** → Deploy from GitHub repo →
   `cirobdomingos-cyber/affiliate-pipeline-automator`. Railway reads
   `railway.toml`, sees `builder = "DOCKERFILE"`, and builds from the
   root `Dockerfile`. First build is 2–4 minutes.

2. **Create a Volume**: + New → Volume. Name `data`, size 1 GB.
   Attach to the service with mount path `/data`. This is where
   `affiliate.duckdb` lives across deploys.

3. **Set environment variables** (service → Variables):

   ```
   ANTHROPIC_API_KEY=sk-ant-...
   DATA_DIR=/data
   ```

   Optional:

   ```
   REPLICATE_API_TOKEN=r8_...         # creative generation
   MAILERLITE_API_KEY=...             # email sync
   CREATIVE_PROVIDER=replicate        # force provider if both set
   REPLICATE_IMAGE_MODEL=...          # override defaults
   REPLICATE_VIDEO_MODEL=...
   ```

   `APP_BASE_URL` is irrelevant on Railway since `/r` and `/bp` aren't
   served here — leave it unset or keep the localhost default.

4. **Expose a public domain**: Settings → Networking → Generate Domain.
   The URL goes straight to the Streamlit UI.

5. **Smoke test**:

   ```bash
   curl https://<your-url>.up.railway.app/_stcore/health
   # ok
   ```

   Open the root URL in a browser — the full Streamlit dashboard should
   render.

## Local development parity

Unchanged. Run `uvicorn backend.app.main:app --port 8000` and
`streamlit run ui/streamlit_app.py` as usual. `DATA_DIR` defaults to the
repo root locally.

To test the production container shape locally (requires Docker):

```bash
docker build -t affiliate-pipeline .
docker run --rm -p 8080:8080 \
  -e PORT=8080 \
  -e DATA_DIR=/tmp/data \
  -e ANTHROPIC_API_KEY=sk-ant-... \
  -v $PWD/.tmp-data:/tmp/data \
  affiliate-pipeline
```

Then open `http://localhost:8080/`.

## Updating

Railway auto-deploys on every push to `main`. Bad deploy? Service →
Deployments → pick a previous green build → Redeploy.

## What does NOT work yet

- **No auth on the dashboard.** Anyone with the Railway URL can see and
  edit your managed products. Keep the URL private or add Streamlit
  basic auth / Railway private networking before sharing.
- **Public affiliate tracking links aren't hosted here.** See the table
  at the top. Migration path is Postgres + second service.
- **No CI gate.** Every push deploys. Add GitHub Actions (`pytest` +
  `ruff`) as a merge gate before this becomes a shared project.
- **Container memory**. Streamlit + dependencies ~300–500 MB. Railway
  free tier caps at 512 MB — you may hit the Hobby plan ($5/mo) under
  creative generation load.
