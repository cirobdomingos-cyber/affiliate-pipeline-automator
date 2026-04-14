"""Product discovery HTTP API.

Thin layer — every route delegates to `services.discovery`. Keeping the
router thin is the rule that lets us swap Streamlit for Next.js in V1
without touching business logic.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from ..db import ProductRepository
from ..models import ScoredProduct
from ..services.discovery import DiscoveryResult, run_discovery

router = APIRouter(prefix="/products", tags=["products"])


def get_repo() -> ProductRepository:
    return ProductRepository()


@router.post("/discover", response_model=DiscoveryResult)
async def discover(
    limit: int = Query(50, ge=1, le=200),
    top_n: int = Query(25, ge=1, le=100),
    use_mock: bool = Query(False),
    repo: ProductRepository = Depends(get_repo),
) -> DiscoveryResult:
    return await run_discovery(
        repo=repo,
        limit_per_source=limit,
        top_n=top_n,
        use_mock=use_mock,
    )


@router.get("/top", response_model=list[ScoredProduct])
def top(
    limit: int = Query(25, ge=1, le=200),
    repo: ProductRepository = Depends(get_repo),
) -> list[ScoredProduct]:
    return repo.top_products(limit=limit)
