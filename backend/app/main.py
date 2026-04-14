"""FastAPI application entrypoint."""

from __future__ import annotations

from fastapi import FastAPI

from .api.links import router as links_router
from .api.products import router as products_router

app = FastAPI(
    title="Affiliate Pipeline Automator",
    description=(
        "End-to-end automation for the Brazilian affiliate marketing pipeline. "
        "Stage 1 (product discovery) is the MVP slice."
    ),
    version="0.1.0",
)

app.include_router(products_router)
app.include_router(links_router)


@app.get("/health", tags=["meta"])
def health() -> dict[str, str]:
    return {"status": "ok"}
