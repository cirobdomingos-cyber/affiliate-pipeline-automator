# Single-container deploy for Railway.
#
# Runs nginx + uvicorn (FastAPI) + streamlit (UI) in one container via
# supervisord. nginx terminates on $PORT and reverse-proxies:
#   /r/*, /bp/*, /health, /docs, /openapi.json   → uvicorn localhost:8000
#   everything else (including websockets)       → streamlit localhost:8501
#
# This lets a single Railway service host both the public redirect API and
# the operator UI without needing a shared volume between two services
# (which the hobby tier doesn't allow).

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        nginx \
        supervisor \
        gettext-base \
        curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python deps first so rebuilds are fast when only app code changes.
COPY pyproject.toml ./
RUN pip install --upgrade pip \
    && pip install -e .

COPY . .

# Volume mount point for DuckDB persistence. Railway mounts the volume here
# when you attach it; backend/app/db.py reads DATA_DIR to find the file.
RUN mkdir -p /data
ENV DATA_DIR=/data

# Ports inside the container:
#   8000 uvicorn (FastAPI) — not exposed, only reached via nginx
#   8501 streamlit         — not exposed, only reached via nginx
#   $PORT nginx            — Railway injects this at runtime
# nginx.conf references ${PORT}; supervisord uses envsubst at boot to render
# the final config.

COPY deploy/nginx.conf.template /etc/nginx/templates/default.conf.template
COPY deploy/supervisord.conf /etc/supervisor/conf.d/supervisord.conf
COPY deploy/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

CMD ["/entrypoint.sh"]
