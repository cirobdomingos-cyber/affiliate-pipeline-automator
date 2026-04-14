"""Link vault service — thin orchestration over LinkRepository and UTM builder.

Kept separate from the API router so both FastAPI and Streamlit can call
the same functions without duplicating logic.
"""

from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha1

from ..db import LinkRepository
from ..models import AffiliateLink, LinkStatus, Platform, TrackedLink, UTMParams
from ..utm import track


def make_link_id(platform: Platform, raw_url: str) -> str:
    return sha1(f"{platform.value}:{raw_url}".encode()).hexdigest()[:16]


def add_link(
    repo: LinkRepository,
    *,
    platform: Platform,
    label: str,
    raw_url: str,
    product_id: str | None = None,
    notes: str = "",
    tags: list[str] | None = None,
    approval_status: LinkStatus = LinkStatus.PENDING,
) -> AffiliateLink:
    now = datetime.now(timezone.utc)
    link = AffiliateLink(
        id=make_link_id(platform, raw_url),
        product_id=product_id,
        platform=platform,
        label=label,
        raw_url=raw_url,
        approval_status=approval_status,
        approved_at=now if approval_status == LinkStatus.APPROVED else None,
        notes=notes,
        tags=tags or [],
        created_at=now,
        updated_at=now,
    )
    repo.upsert(link)
    return link


def compose_tracked_url(link: AffiliateLink, utm: UTMParams) -> TrackedLink:
    return track(link, utm)
