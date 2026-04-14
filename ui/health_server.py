"""Minimal FastAPI health-check server for Railway deployments.

Runs on port 8001 in a background daemon thread so it doesn't block
Streamlit. Railway's healthcheck probes GET /health on this port and
receives {"status": "ok"} as soon as the Python process starts — well
before Streamlit finishes its own initialisation.

Usage (called automatically when streamlit_app.py is imported):
    from ui.health_server import start_health_server
    start_health_server()
"""

from __future__ import annotations

import threading

import uvicorn
from fastapi import FastAPI

_HEALTH_PORT = 8001
_started = False
_lock = threading.Lock()

_app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)


@_app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


def start_health_server() -> None:
    """Start the health server in a background thread (idempotent)."""
    global _started
    with _lock:
        if _started:
            return
        _started = True

    config = uvicorn.Config(
        _app,
        host="0.0.0.0",
        port=_HEALTH_PORT,
        log_level="warning",
        access_log=False,
    )
    server = uvicorn.Server(config)

    thread = threading.Thread(target=server.run, daemon=True, name="health-server")
    thread.start()
