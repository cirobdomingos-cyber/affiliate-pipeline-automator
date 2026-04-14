# Single-process Streamlit deploy for Railway.
#
# Previously this container ran nginx + uvicorn + streamlit under
# supervisord to host both the Streamlit admin UI and the FastAPI public
# routes (/r/{slug}, /bp/{slug}) on one port. That setup kept failing
# Railway health checks with no runtime logs to diagnose, so we pivoted to
# the simplest thing that works: Streamlit is the only public server, and
# Railway hits its native /_stcore/health endpoint.
#
# Tradeoff: the FastAPI-owned public routes (/r and /bp) are NOT reachable
# from this deploy. They still work locally when `uvicorn backend.app.main`
# is running. Re-enabling them in production requires either a two-service
# Railway project with a shared Postgres (not Volume), or a single container
# that runs both processes behind a reverse proxy — the latter is what we
# tried first and failed to debug through Railway's log pipeline.

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends gcc \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml ./
RUN pip install --upgrade pip \
    && pip install -e .

COPY . .

# Volume mount point. Railway attaches the persistent Volume here and
# backend/app/db.py reads DATA_DIR to build the DuckDB path.
RUN mkdir -p /data
ENV DATA_DIR=/data

# Railway injects $PORT at runtime. Streamlit binds 0.0.0.0:$PORT and
# Railway probes /_stcore/health (Streamlit's own health endpoint).
# --server.headless avoids Streamlit's email prompt on first run.
# sh -c is used so $PORT expands at runtime, not at image build time.
CMD ["sh", "-c", "streamlit run ui/streamlit_app.py --server.port $PORT --server.address 0.0.0.0 --server.headless true --browser.gatherUsageStats false"]
