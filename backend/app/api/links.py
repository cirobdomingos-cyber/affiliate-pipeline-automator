"""Affiliate link vault HTTP API (Stage 2).

Thin — every route delegates to LinkRepository + services/link_vault.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from ..db import LinkRepository
from ..models import AffiliateLink, LinkStatus, Platform, TrackedLink, UTMParams
from ..services.link_vault import add_link, compose_tracked_url

router = APIRouter(prefix="/links", tags=["links"])


def get_link_repo() -> LinkRepository:
    return LinkRepository()


class AddLinkRequest(BaseModel):
    platform: Platform
    label: str
    raw_url: str
    product_id: str | None = None
    notes: str = ""
    tags: list[str] = []
    approval_status: LinkStatus = LinkStatus.PENDING


class TrackRequest(BaseModel):
    utm: UTMParams


@router.post("", response_model=AffiliateLink)
def create(
    body: AddLinkRequest,
    repo: LinkRepository = Depends(get_link_repo),
) -> AffiliateLink:
    return add_link(
        repo,
        platform=body.platform,
        label=body.label,
        raw_url=body.raw_url,
        product_id=body.product_id,
        notes=body.notes,
        tags=body.tags,
        approval_status=body.approval_status,
    )


@router.get("", response_model=list[AffiliateLink])
def list_links(
    status: LinkStatus | None = Query(default=None),
    platform: Platform | None = Query(default=None),
    repo: LinkRepository = Depends(get_link_repo),
) -> list[AffiliateLink]:
    return repo.list(status=status, platform=platform)


@router.get("/{link_id}", response_model=AffiliateLink)
def get_one(
    link_id: str,
    repo: LinkRepository = Depends(get_link_repo),
) -> AffiliateLink:
    link = repo.get(link_id)
    if link is None:
        raise HTTPException(status_code=404, detail="link not found")
    return link


@router.patch("/{link_id}/status", response_model=AffiliateLink)
def set_status(
    link_id: str,
    status: LinkStatus,
    repo: LinkRepository = Depends(get_link_repo),
) -> AffiliateLink:
    link = repo.set_status(link_id, status)
    if link is None:
        raise HTTPException(status_code=404, detail="link not found")
    return link


@router.post("/{link_id}/track", response_model=TrackedLink)
def track_endpoint(
    link_id: str,
    body: TrackRequest,
    repo: LinkRepository = Depends(get_link_repo),
) -> TrackedLink:
    link = repo.get(link_id)
    if link is None:
        raise HTTPException(status_code=404, detail="link not found")
    return compose_tracked_url(link, body.utm)
