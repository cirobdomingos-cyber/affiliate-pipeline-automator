"""Managed-product catalog HTTP API (Stage 1 of the affiliate module).

Hand-curated catalog. Separate from `/products` — that endpoint serves scrape
output, which is re-written on every discovery run. This one is where the
operator keeps the products they're actively promoting and drives every
downstream stage (channels, bridge pages, KPIs, scale checklist).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ..db import ManagedProductRepository, OperatorProfileRepository
from ..models import ManagedProduct, Niche, OperatorProfile, Platform
from ..scoring import score_managed_product

router = APIRouter(prefix="/managed-products", tags=["managed-products"])
onboarding_router = APIRouter(prefix="/onboarding", tags=["onboarding"])


def get_repo() -> ManagedProductRepository:
    return ManagedProductRepository()


def get_profile_repo() -> OperatorProfileRepository:
    return OperatorProfileRepository()


class ManagedProductUpsert(BaseModel):
    name: str
    platform: Platform
    niche: Niche
    commission_pct: float = Field(ge=0, le=100)
    ticket_brl: float = Field(ge=0)
    sales_page_url: str | None = None
    affiliate_url: str
    epc_actual: float | None = Field(default=None, ge=0)
    cpv_actual: float | None = Field(default=None, ge=0)
    notes: str = ""


@router.post("", response_model=ManagedProduct)
def create(
    body: ManagedProductUpsert,
    repo: ManagedProductRepository = Depends(get_repo),
) -> ManagedProduct:
    now = datetime.now(timezone.utc)
    mp = ManagedProduct(
        id=str(uuid.uuid4()),
        name=body.name,
        platform=body.platform,
        niche=body.niche,
        commission_pct=body.commission_pct,
        ticket_brl=body.ticket_brl,
        sales_page_url=body.sales_page_url,
        affiliate_url=body.affiliate_url,
        quality_score=score_managed_product(
            commission_pct=body.commission_pct,
            ticket_brl=body.ticket_brl,
            platform=body.platform,
        ),
        epc_actual=body.epc_actual,
        cpv_actual=body.cpv_actual,
        notes=body.notes,
        created_at=now,
        updated_at=now,
    )
    repo.upsert(mp)
    return mp


@router.get("", response_model=list[ManagedProduct])
def list_all(
    niche: Niche | None = None,
    repo: ManagedProductRepository = Depends(get_repo),
) -> list[ManagedProduct]:
    return repo.list(niche=niche)


@router.get("/{product_id}", response_model=ManagedProduct)
def get_one(
    product_id: str,
    repo: ManagedProductRepository = Depends(get_repo),
) -> ManagedProduct:
    mp = repo.get(product_id)
    if mp is None:
        raise HTTPException(status_code=404, detail=f"managed product {product_id} not found")
    return mp


@router.patch("/{product_id}", response_model=ManagedProduct)
def update(
    product_id: str,
    body: ManagedProductUpsert,
    repo: ManagedProductRepository = Depends(get_repo),
) -> ManagedProduct:
    existing = repo.get(product_id)
    if existing is None:
        raise HTTPException(status_code=404, detail=f"managed product {product_id} not found")
    updated = ManagedProduct(
        id=existing.id,
        name=body.name,
        platform=body.platform,
        niche=body.niche,
        commission_pct=body.commission_pct,
        ticket_brl=body.ticket_brl,
        sales_page_url=body.sales_page_url,
        affiliate_url=body.affiliate_url,
        quality_score=score_managed_product(
            commission_pct=body.commission_pct,
            ticket_brl=body.ticket_brl,
            platform=body.platform,
        ),
        epc_actual=body.epc_actual,
        cpv_actual=body.cpv_actual,
        notes=body.notes,
        created_at=existing.created_at,
        updated_at=datetime.now(timezone.utc),
    )
    repo.upsert(updated)
    return updated


@router.delete("/{product_id}")
def delete(
    product_id: str,
    repo: ManagedProductRepository = Depends(get_repo),
) -> dict[str, str]:
    repo.delete(product_id)
    return {"status": "deleted", "id": product_id}


class OnboardingRequest(BaseModel):
    primary_niche: Niche


@onboarding_router.get("", response_model=OperatorProfile | None)
def get_profile(
    repo: OperatorProfileRepository = Depends(get_profile_repo),
) -> OperatorProfile | None:
    return repo.get()


@onboarding_router.post("", response_model=OperatorProfile)
def save_profile(
    body: OnboardingRequest,
    repo: OperatorProfileRepository = Depends(get_profile_repo),
) -> OperatorProfile:
    profile = OperatorProfile(primary_niche=body.primary_niche)
    repo.save(profile)
    return profile
