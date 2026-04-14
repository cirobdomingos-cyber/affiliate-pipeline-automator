"""Traffic planner HTTP API (Stage 3).

Thin — both routes delegate to TrafficPlanner. The planner is built lazily
on each request because it wraps a real Anthropic client; tests inject a
different dependency via FastAPI's `dependency_overrides`.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..db import ProductRepository
from ..models import (
    AdCopyVariant,
    OrganicPlan,
    Platform,
    Product,
    TrafficChannel,
)
from ..traffic_planner import TrafficPlanner, build_default_traffic_planner

router = APIRouter(prefix="/traffic", tags=["traffic"])


def get_planner() -> TrafficPlanner:
    return build_default_traffic_planner()


def get_product_repo() -> ProductRepository:
    return ProductRepository()


class OrganicRequest(BaseModel):
    product_id: str
    target_audience: str
    channels: list[TrafficChannel]
    days: int = 7


class AdVariantsRequest(BaseModel):
    product_id: str
    target_audience: str
    platform: TrafficChannel
    count: int = 5


def _load_product(repo: ProductRepository, product_id: str) -> Product:
    persisted = repo.top_products(limit=500)
    for sp in persisted:
        if sp.product.id == product_id:
            return sp.product
    raise HTTPException(status_code=404, detail=f"product {product_id} not found")


@router.post("/organic", response_model=OrganicPlan)
def plan_organic(
    body: OrganicRequest,
    planner: TrafficPlanner = Depends(get_planner),
    repo: ProductRepository = Depends(get_product_repo),
) -> OrganicPlan:
    product = _load_product(repo, body.product_id)
    return planner.plan_organic(
        product=product,
        target_audience=body.target_audience,
        channels=body.channels,
        days=body.days,
    )


@router.post("/ad-variants", response_model=list[AdCopyVariant])
def generate_ad_variants(
    body: AdVariantsRequest,
    planner: TrafficPlanner = Depends(get_planner),
    repo: ProductRepository = Depends(get_product_repo),
) -> list[AdCopyVariant]:
    product = _load_product(repo, body.product_id)
    return planner.generate_ad_variants(
        product=product,
        target_audience=body.target_audience,
        platform=body.platform,
        count=body.count,
    )
