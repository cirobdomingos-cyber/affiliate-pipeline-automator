"""Short-link API + public /r/{slug} redirect (Stage 2).

`/r/{slug}` is the public, no-auth redirect endpoint. It must stay fast: one
DuckDB read (the slug), one append (the click event), one 302. No LLM, no
heavy imports at request time.

Click tracking captures only what the brief requires: timestamp, anonymized
IP (first 3 octets), coarse UA family, optional utm_source from the query
string. Full user-agent strings and full IPs are deliberately dropped — LGPD
friendlier and keeps the table small.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field

from ..db import ClickEventRepository, ManagedProductRepository, ShortLinkRepository
from ..models import ClickEvent, ShortLink, ShortLinkStats
from ..services.shortener import (
    anonymize_ip,
    compose_destination,
    generate_slug,
    ua_family,
)

router = APIRouter(prefix="/short-links", tags=["short-links"])
redirect_router = APIRouter(tags=["redirect"])


def get_short_repo() -> ShortLinkRepository:
    return ShortLinkRepository()


def get_click_repo() -> ClickEventRepository:
    return ClickEventRepository()


def get_managed_repo() -> ManagedProductRepository:
    return ManagedProductRepository()


class CreateShortLinkRequest(BaseModel):
    managed_product_id: str
    utm_source: str | None = None
    utm_medium: str | None = None
    utm_campaign: str | None = None
    custom_slug: str | None = Field(default=None, min_length=3, max_length=32)


@router.post("", response_model=ShortLink)
def create(
    body: CreateShortLinkRequest,
    short_repo: ShortLinkRepository = Depends(get_short_repo),
    managed_repo: ManagedProductRepository = Depends(get_managed_repo),
) -> ShortLink:
    product = managed_repo.get(body.managed_product_id)
    if product is None:
        raise HTTPException(status_code=404, detail="managed product not found")

    slug = body.custom_slug or generate_slug()
    if short_repo.get(slug) is not None:
        raise HTTPException(status_code=409, detail=f"slug '{slug}' already exists")

    destination = compose_destination(
        affiliate_url=product.affiliate_url,
        utm_source=body.utm_source,
        utm_medium=body.utm_medium,
        utm_campaign=body.utm_campaign,
    )
    link = ShortLink(
        slug=slug,
        managed_product_id=product.id,
        destination_url=destination,
        utm_source=body.utm_source,
        utm_medium=body.utm_medium,
        utm_campaign=body.utm_campaign,
    )
    short_repo.upsert(link)
    return link


@router.get("", response_model=list[ShortLink])
def list_all(
    managed_product_id: str | None = None,
    short_repo: ShortLinkRepository = Depends(get_short_repo),
) -> list[ShortLink]:
    return short_repo.list(managed_product_id=managed_product_id)


@router.get("/{slug}/stats", response_model=ShortLinkStats)
def stats(
    slug: str,
    short_repo: ShortLinkRepository = Depends(get_short_repo),
) -> ShortLinkStats:
    if short_repo.get(slug) is None:
        raise HTTPException(status_code=404, detail="slug not found")
    return short_repo.stats(slug)


@router.delete("/{slug}")
def delete(
    slug: str,
    short_repo: ShortLinkRepository = Depends(get_short_repo),
) -> dict[str, str]:
    short_repo.delete(slug)
    return {"status": "deleted", "slug": slug}


@redirect_router.get("/r/{slug}")
def redirect(
    slug: str,
    request: Request,
    short_repo: ShortLinkRepository = Depends(get_short_repo),
    click_repo: ClickEventRepository = Depends(get_click_repo),
) -> RedirectResponse:
    link = short_repo.get(slug)
    if link is None:
        raise HTTPException(status_code=404, detail="short link not found")

    client_ip = request.client.host if request.client else None
    click_repo.log(
        ClickEvent(
            id=str(uuid.uuid4()),
            target_type="short_link",
            target_id=slug,
            managed_product_id=link.managed_product_id,
            ip_prefix=anonymize_ip(client_ip),
            ua_family=ua_family(request.headers.get("user-agent")),
            utm_source=link.utm_source or request.query_params.get("utm_source"),
        )
    )
    return RedirectResponse(url=link.destination_url, status_code=302)
