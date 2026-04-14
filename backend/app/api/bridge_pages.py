"""Bridge page builder + public /bp/{slug} renderer (Stage 4).

HTML is rendered inline from a module-level template — no Jinja, no external
templates directory, zero JS dependencies. The page must be fast and the
content is simple enough that f-strings + html.escape cover it. The CTA click
handler at /bp/{slug}/click writes a click_event and 302s to the real
destination, mirroring how /r/{slug} works for short links.
"""

from __future__ import annotations

import html
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel, Field

from ..db import BridgePageRepository, ClickEventRepository, ManagedProductRepository
from ..models import BridgePage, ClickEvent
from ..services.shortener import anonymize_ip, generate_slug, ua_family

router = APIRouter(prefix="/bridge-pages", tags=["bridge-pages"])
public_router = APIRouter(tags=["bridge-public"])


def get_bridge_repo() -> BridgePageRepository:
    return BridgePageRepository()


def get_click_repo() -> ClickEventRepository:
    return ClickEventRepository()


def get_managed_repo() -> ManagedProductRepository:
    return ManagedProductRepository()


class BridgePageUpsert(BaseModel):
    managed_product_id: str
    headline: str
    subheadline: str | None = None
    bullets: list[str] = Field(default_factory=list, max_length=5)
    cta_text: str
    cta_url: str
    primary_color: str = "#2563eb"
    custom_slug: str | None = Field(default=None, min_length=3, max_length=32)


@router.post("", response_model=BridgePage)
def create(
    body: BridgePageUpsert,
    bridge_repo: BridgePageRepository = Depends(get_bridge_repo),
    managed_repo: ManagedProductRepository = Depends(get_managed_repo),
) -> BridgePage:
    if managed_repo.get(body.managed_product_id) is None:
        raise HTTPException(status_code=404, detail="managed product not found")
    slug = body.custom_slug or generate_slug()
    if bridge_repo.get(slug) is not None:
        raise HTTPException(status_code=409, detail=f"slug '{slug}' already exists")
    page = BridgePage(
        slug=slug,
        managed_product_id=body.managed_product_id,
        headline=body.headline,
        subheadline=body.subheadline,
        bullets=body.bullets,
        cta_text=body.cta_text,
        cta_url=body.cta_url,
        primary_color=body.primary_color,
    )
    bridge_repo.upsert(page)
    return page


@router.get("", response_model=list[BridgePage])
def list_all(
    managed_product_id: str | None = None,
    bridge_repo: BridgePageRepository = Depends(get_bridge_repo),
) -> list[BridgePage]:
    return bridge_repo.list(managed_product_id=managed_product_id)


@router.patch("/{slug}", response_model=BridgePage)
def update(
    slug: str,
    body: BridgePageUpsert,
    bridge_repo: BridgePageRepository = Depends(get_bridge_repo),
) -> BridgePage:
    existing = bridge_repo.get(slug)
    if existing is None:
        raise HTTPException(status_code=404, detail="bridge page not found")
    updated = BridgePage(
        slug=slug,
        managed_product_id=body.managed_product_id,
        headline=body.headline,
        subheadline=body.subheadline,
        bullets=body.bullets,
        cta_text=body.cta_text,
        cta_url=body.cta_url,
        primary_color=body.primary_color,
        created_at=existing.created_at,
        updated_at=datetime.now(timezone.utc),
    )
    bridge_repo.upsert(updated)
    return updated


@router.delete("/{slug}")
def delete(
    slug: str,
    bridge_repo: BridgePageRepository = Depends(get_bridge_repo),
) -> dict[str, str]:
    bridge_repo.delete(slug)
    return {"status": "deleted", "slug": slug}


_BRIDGE_TEMPLATE = """<!doctype html>
<html lang="pt-BR">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{headline}</title>
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;background:#f9fafb;color:#111827;line-height:1.5;-webkit-font-smoothing:antialiased}}
.wrap{{max-width:640px;margin:0 auto;padding:48px 24px;text-align:center}}
h1{{font-size:36px;font-weight:800;color:#111827;margin-bottom:16px;line-height:1.15}}
.sub{{font-size:18px;color:#4b5563;margin-bottom:32px}}
ul{{list-style:none;text-align:left;margin:0 auto 40px;max-width:480px}}
li{{padding:12px 0 12px 32px;position:relative;font-size:16px;color:#1f2937}}
li:before{{content:"✓";position:absolute;left:0;top:12px;color:{color};font-weight:700;font-size:18px}}
.cta{{display:inline-block;background:{color};color:#fff;padding:16px 36px;border-radius:8px;font-size:18px;font-weight:700;text-decoration:none;box-shadow:0 4px 12px rgba(0,0,0,0.1);transition:transform 0.1s}}
.cta:hover{{transform:translateY(-1px)}}
@media(max-width:480px){{h1{{font-size:28px}}.wrap{{padding:32px 20px}}}}
</style>
</head>
<body>
<div class="wrap">
<h1>{headline}</h1>
{subheadline_html}
{bullets_html}
<a class="cta" href="{cta_href}">{cta_text}</a>
</div>
</body>
</html>"""


def _render_html(page: BridgePage) -> str:
    sub_html = (
        f'<p class="sub">{html.escape(page.subheadline)}</p>' if page.subheadline else ""
    )
    if page.bullets:
        items = "".join(f"<li>{html.escape(b)}</li>" for b in page.bullets)
        bullets_html = f"<ul>{items}</ul>"
    else:
        bullets_html = ""
    return _BRIDGE_TEMPLATE.format(
        headline=html.escape(page.headline),
        subheadline_html=sub_html,
        bullets_html=bullets_html,
        cta_text=html.escape(page.cta_text),
        cta_href=f"/bp/{page.slug}/click",
        color=html.escape(page.primary_color),
    )


@public_router.get("/bp/{slug}", response_class=HTMLResponse)
def render_bridge(
    slug: str,
    request: Request,
    bridge_repo: BridgePageRepository = Depends(get_bridge_repo),
    click_repo: ClickEventRepository = Depends(get_click_repo),
) -> HTMLResponse:
    page = bridge_repo.get(slug)
    if page is None:
        raise HTTPException(status_code=404, detail="bridge page not found")
    client_ip = request.client.host if request.client else None
    click_repo.log(
        ClickEvent(
            id=str(uuid.uuid4()),
            target_type="bridge_view",
            target_id=slug,
            managed_product_id=page.managed_product_id,
            ip_prefix=anonymize_ip(client_ip),
            ua_family=ua_family(request.headers.get("user-agent")),
            utm_source=request.query_params.get("utm_source"),
        )
    )
    return HTMLResponse(content=_render_html(page))


@public_router.get("/bp/{slug}/click")
def bridge_click(
    slug: str,
    request: Request,
    bridge_repo: BridgePageRepository = Depends(get_bridge_repo),
    click_repo: ClickEventRepository = Depends(get_click_repo),
) -> RedirectResponse:
    page = bridge_repo.get(slug)
    if page is None:
        raise HTTPException(status_code=404, detail="bridge page not found")
    client_ip = request.client.host if request.client else None
    click_repo.log(
        ClickEvent(
            id=str(uuid.uuid4()),
            target_type="bridge_cta",
            target_id=slug,
            managed_product_id=page.managed_product_id,
            ip_prefix=anonymize_ip(client_ip),
            ua_family=ua_family(request.headers.get("user-agent")),
            utm_source=request.query_params.get("utm_source"),
        )
    )
    return RedirectResponse(url=page.cta_url, status_code=302)
