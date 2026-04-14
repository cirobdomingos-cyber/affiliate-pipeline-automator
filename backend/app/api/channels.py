"""Channel config + stale-traffic alerts (Stage 3).

Lets the operator persist which organic/paid channels they've actually turned
on for each managed product. The /alerts/stale-channels endpoint flags paid
channels running ACTIVE with zero clicks in the last 24h — the symptom of
spending money on a pixel that isn't firing.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ..db import (
    ChannelConfigRepository,
    ClickEventRepository,
    ManagedProductRepository,
)
from ..models import (
    PAID_CHANNELS,
    ChannelConfig,
    ChannelStatus,
    StaleChannelAlert,
    TrafficChannel,
)

router = APIRouter(prefix="/managed-products/{product_id}/channels", tags=["channels"])
alerts_router = APIRouter(prefix="/alerts", tags=["alerts"])


def get_channel_repo() -> ChannelConfigRepository:
    return ChannelConfigRepository()


def get_managed_repo() -> ManagedProductRepository:
    return ManagedProductRepository()


def get_click_repo() -> ClickEventRepository:
    return ClickEventRepository()


class ChannelConfigUpsert(BaseModel):
    channel: TrafficChannel
    status: ChannelStatus = ChannelStatus.ACTIVE
    daily_budget_brl: float | None = Field(default=None, ge=0)
    daily_click_goal: int | None = Field(default=None, ge=0)


@router.get("", response_model=list[ChannelConfig])
def list_channels(
    product_id: str,
    repo: ChannelConfigRepository = Depends(get_channel_repo),
) -> list[ChannelConfig]:
    return repo.list_for_product(product_id)


@router.put("", response_model=ChannelConfig)
def upsert_channel(
    product_id: str,
    body: ChannelConfigUpsert,
    repo: ChannelConfigRepository = Depends(get_channel_repo),
    managed_repo: ManagedProductRepository = Depends(get_managed_repo),
) -> ChannelConfig:
    if managed_repo.get(product_id) is None:
        raise HTTPException(status_code=404, detail="managed product not found")
    now = datetime.now(timezone.utc)
    existing = {c.channel: c for c in repo.list_for_product(product_id)}
    prev = existing.get(body.channel)
    cfg = ChannelConfig(
        id=prev.id if prev else str(uuid.uuid4()),
        managed_product_id=product_id,
        channel=body.channel,
        status=body.status,
        daily_budget_brl=body.daily_budget_brl,
        daily_click_goal=body.daily_click_goal,
        created_at=prev.created_at if prev else now,
        updated_at=now,
    )
    repo.upsert(cfg)
    return cfg


@router.delete("/{channel_id}")
def delete_channel(
    product_id: str,
    channel_id: str,
    repo: ChannelConfigRepository = Depends(get_channel_repo),
) -> dict[str, str]:
    repo.delete(channel_id)
    return {"status": "deleted", "id": channel_id}


@alerts_router.get("/stale-channels", response_model=list[StaleChannelAlert])
def stale_channels(
    channel_repo: ChannelConfigRepository = Depends(get_channel_repo),
    click_repo: ClickEventRepository = Depends(get_click_repo),
    managed_repo: ManagedProductRepository = Depends(get_managed_repo),
) -> list[StaleChannelAlert]:
    alerts: list[StaleChannelAlert] = []
    name_by_id = {mp.id: mp.name for mp in managed_repo.list()}
    for cfg in channel_repo.list_all():
        if cfg.status != ChannelStatus.ACTIVE:
            continue
        if cfg.channel not in PAID_CHANNELS:
            continue
        clicks_24h = click_repo.recent_count(cfg.managed_product_id, hours=24)
        if clicks_24h > 0:
            continue
        alerts.append(
            StaleChannelAlert(
                managed_product_id=cfg.managed_product_id,
                managed_product_name=name_by_id.get(cfg.managed_product_id, "(unknown)"),
                channel=cfg.channel,
                daily_budget_brl=cfg.daily_budget_brl,
                clicks_last_24h=clicks_24h,
            )
        )
    return alerts
