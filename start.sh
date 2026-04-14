#!/bin/sh
# Container entrypoint for Railway. Reads $PORT from the environment and
# launches Streamlit bound to it. This exists as a standalone script so
# Railway's "Custom Start Command" (which doesn't always expand $VAR in
# its command field) can point at it without needing shell escaping.
#
# Override the Custom Start Command in Railway to this file OR leave it
# empty so the Dockerfile CMD runs — either way, $PORT gets expanded here,
# not in whatever command string Railway passed in.
set -e

: "${PORT:=8501}"

echo "[boot] container starting"
echo "[boot] PORT=$PORT"
echo "[boot] DATA_DIR=${DATA_DIR:-<unset>}"
echo "[boot] DATABASE_URL=${DATABASE_URL:+<set>}"
python --version
echo "[boot] launching streamlit on :$PORT"

exec python -m streamlit run ui/streamlit_app.py \
    --server.port "$PORT" \
    --server.address 0.0.0.0 \
    --server.headless true \
    --browser.gatherUsageStats false
