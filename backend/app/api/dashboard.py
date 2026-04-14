"""KPI dashboard (Stage 6).

Aggregates the other stages into a single per-product view: clicks on short
links, bridge-page views and CTA clicks, subscribers captured, and the
operator-entered EPC/CPV actuals. Everything is computed on-read from the
click_events append-only log — no derived tables, no snapshotting. DuckDB
handles this comfortably for the volume a single operator produces.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from ..db import (
    ClickEventRepository,
    ManagedProductRepository,
    SubscriberCountRepository,
)

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


def get_managed_repo() -> ManagedProductRepository:
    return ManagedProductRepository()


def get_click_repo() -> ClickEventRepository:
    return ClickEventRepository()


def get_sub_repo() -> SubscriberCountRepository:
    return SubscriberCountRepository()


class ProductKPI(BaseModel):
    managed_product_id: str
    name: str
    niche: str
    affiliate_clicks: int
    bridge_views: int
    bridge_cta_clicks: int
    bridge_conversion_rate: float  # 0..1
    subscribers: int
    epc_actual: float | None
    cpv_actual: float | None


class TimeseriesPoint(BaseModel):
    date: str  # YYYY-MM-DD
    affiliate_clicks: int
    bridge_views: int
    bridge_cta_clicks: int


@router.get("/kpis", response_model=list[ProductKPI])
def kpis(
    managed_repo: ManagedProductRepository = Depends(get_managed_repo),
    click_repo: ClickEventRepository = Depends(get_click_repo),
    sub_repo: SubscriberCountRepository = Depends(get_sub_repo),
) -> list[ProductKPI]:
    subs = sub_repo.all()
    out: list[ProductKPI] = []
    for mp in managed_repo.list():
        aff = click_repo.count_for_product(mp.id, target_type="short_link")
        views = click_repo.count_for_product(mp.id, target_type="bridge_view")
        cta = click_repo.count_for_product(mp.id, target_type="bridge_cta")
        conv = (cta / views) if views else 0.0
        out.append(
            ProductKPI(
                managed_product_id=mp.id,
                name=mp.name,
                niche=mp.niche.value,
                affiliate_clicks=aff,
                bridge_views=views,
                bridge_cta_clicks=cta,
                bridge_conversion_rate=round(conv, 4),
                subscribers=subs.get(mp.id, 0),
                epc_actual=mp.epc_actual,
                cpv_actual=mp.cpv_actual,
            )
        )
    # Rank by EPC desc, None last.
    out.sort(key=lambda k: (k.epc_actual is None, -(k.epc_actual or 0.0)))
    return out


@router.get("/timeseries", response_model=list[TimeseriesPoint])
def timeseries(
    managed_product_id: str,
    days: int = 30,
    click_repo: ClickEventRepository = Depends(get_click_repo),
) -> list[TimeseriesPoint]:
    aff = dict(click_repo.daily_timeseries(managed_product_id, days=days, target_type="short_link"))
    views = dict(click_repo.daily_timeseries(managed_product_id, days=days, target_type="bridge_view"))
    cta = dict(click_repo.daily_timeseries(managed_product_id, days=days, target_type="bridge_cta"))
    all_dates = sorted(set(aff) | set(views) | set(cta))
    return [
        TimeseriesPoint(
            date=d,
            affiliate_clicks=aff.get(d, 0),
            bridge_views=views.get(d, 0),
            bridge_cta_clicks=cta.get(d, 0),
        )
        for d in all_dates
    ]
