"""FastAPI application entrypoint."""

from __future__ import annotations

from fastapi import FastAPI

from .api.bridge_pages import public_router as bridge_public_router, router as bridge_router
from .api.channels import alerts_router, router as channels_router
from .api.dashboard import router as dashboard_router
from .api.email import mailerlite_router, sequences_router
from .api.scale import router as scale_router
from .api.links import router as links_router
from .api.managed_products import onboarding_router, router as managed_products_router
from .api.products import router as products_router
from .api.shortener import redirect_router, router as shortener_router
from .api.traffic import router as traffic_router

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
app.include_router(traffic_router)
app.include_router(managed_products_router)
app.include_router(onboarding_router)
app.include_router(shortener_router)
app.include_router(redirect_router)
app.include_router(channels_router)
app.include_router(alerts_router)
app.include_router(bridge_router)
app.include_router(bridge_public_router)
app.include_router(sequences_router)
app.include_router(mailerlite_router)
app.include_router(dashboard_router)
app.include_router(scale_router)


@app.get("/health", tags=["meta"])
def health() -> dict[str, str]:
    return {"status": "ok"}
