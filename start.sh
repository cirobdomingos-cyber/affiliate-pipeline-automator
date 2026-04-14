#!/bin/sh
# Container entrypoint for Railway. Dispatches on $SERVICE_ROLE so the
# SAME Dockerfile image can run as either service in the project:
#
#   SERVICE_ROLE=ui   (default)   → Streamlit operator dashboard
#   SERVICE_ROLE=api              → FastAPI uvicorn for public routes
#
# This way neither service needs a "Custom Start Command" override in
# Railway's dashboard — Railway's Custom Start Command field sometimes
# passes strings to exec without shell expansion, which is how earlier
# deploys ended up with a literal '$PORT' in the streamlit invocation.
# With start.sh doing its own expansion, the container works regardless
# of how Railway invoked it.
set -e

: "${PORT:=8501}"
: "${SERVICE_ROLE:=ui}"

echo "[boot] container starting"
echo "[boot] SERVICE_ROLE=$SERVICE_ROLE"
echo "[boot] PORT=$PORT"
echo "[boot] DATA_DIR=${DATA_DIR:-<unset>}"
echo "[boot] DATABASE_URL=${DATABASE_URL:+<set>}"
python --version

if [ "$SERVICE_ROLE" = "api" ]; then
    echo "[boot] launching uvicorn (FastAPI) on :$PORT"
    exec python -m uvicorn backend.app.main:app \
        --host 0.0.0.0 \
        --port "$PORT"
fi

echo "[boot] launching streamlit (UI) on :$PORT"
exec python -m streamlit run ui/streamlit_app.py \
    --server.port "$PORT" \
    --server.address 0.0.0.0 \
    --server.headless true \
    --browser.gatherUsageStats false
