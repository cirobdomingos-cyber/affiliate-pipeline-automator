"""Scale-readiness checklist (Stage 7).

Each product is scored against six gates; any unchecked gate becomes a
human-readable suggestion. No new tables — every check reads an existing
repository. The whole module is a read-only projection over state that
Stages 1–6 already persist.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..db import (
    BridgePageRepository,
    ChannelConfigRepository,
    ClickEventRepository,
    EmailSequenceRepository,
    ManagedProductRepository,
    ShortLinkRepository,
)
from ..models import ChannelStatus

router = APIRouter(prefix="/managed-products/{product_id}", tags=["scale"])

_MIN_CLICKS_FOR_SCALE = 100


class ChecklistItem(BaseModel):
    label: str
    passed: bool
    detail: str


class ScaleReadiness(BaseModel):
    managed_product_id: str
    name: str
    checklist: list[ChecklistItem]
    suggestions: list[str]
    ready_to_scale: bool


def get_managed_repo() -> ManagedProductRepository:
    return ManagedProductRepository()


def get_short_repo() -> ShortLinkRepository:
    return ShortLinkRepository()


def get_bridge_repo() -> BridgePageRepository:
    return BridgePageRepository()


def get_channel_repo() -> ChannelConfigRepository:
    return ChannelConfigRepository()


def get_seq_repo() -> EmailSequenceRepository:
    return EmailSequenceRepository()


def get_click_repo() -> ClickEventRepository:
    return ClickEventRepository()


@router.get("/scale-readiness", response_model=ScaleReadiness)
def scale_readiness(
    product_id: str,
    managed_repo: ManagedProductRepository = Depends(get_managed_repo),
    short_repo: ShortLinkRepository = Depends(get_short_repo),
    bridge_repo: BridgePageRepository = Depends(get_bridge_repo),
    channel_repo: ChannelConfigRepository = Depends(get_channel_repo),
    seq_repo: EmailSequenceRepository = Depends(get_seq_repo),
    click_repo: ClickEventRepository = Depends(get_click_repo),
) -> ScaleReadiness:
    mp = managed_repo.get(product_id)
    if mp is None:
        raise HTTPException(status_code=404, detail="managed product not found")

    short_links = short_repo.list(managed_product_id=product_id)
    links_with_utm = [
        s for s in short_links if s.utm_source or s.utm_medium or s.utm_campaign
    ]
    bridges = bridge_repo.list(managed_product_id=product_id)
    channels = channel_repo.list_for_product(product_id)
    active_channels = [c for c in channels if c.status == ChannelStatus.ACTIVE]
    sequences = seq_repo.list(managed_product_id=product_id)
    connected_sequences = [
        s for s in sequences if s.mailerlite_group_id and s.steps
    ]
    total_clicks = click_repo.count_for_product(product_id, target_type="short_link")

    checks: list[tuple[str, bool, str, str]] = [
        (
            "Tracked link with UTMs",
            len(links_with_utm) > 0,
            f"{len(links_with_utm)} tracked link(s) with UTMs · {len(short_links)} total",
            "Crie um short link com utm_source/medium/campaign na aba My Products.",
        ),
        (
            "Bridge page published",
            len(bridges) > 0,
            f"{len(bridges)} bridge page(s)",
            "Crie uma bridge page na aba Bridge Pages para aumentar a conversão antes do afiliado.",
        ),
        (
            "At least 1 active traffic channel",
            len(active_channels) > 0,
            f"{len(active_channels)} active / {len(channels)} configured",
            "Ative pelo menos um canal (orgânico ou pago) na aba My Products → Traffic channels.",
        ),
        (
            "Email list connected",
            len(connected_sequences) > 0,
            f"{len(connected_sequences)} sequence(s) linked to a MailerLite group",
            "Configure um grupo MailerLite e salve uma sequência na aba Email.",
        ),
        (
            f"{_MIN_CLICKS_FOR_SCALE}+ clicks registered",
            total_clicks >= _MIN_CLICKS_FOR_SCALE,
            f"{total_clicks} / {_MIN_CLICKS_FOR_SCALE}",
            f"Você tem {total_clicks} cliques. Dirija mais tráfego antes de escalar — "
            f"abaixo de {_MIN_CLICKS_FOR_SCALE} os dados não são estatisticamente significativos.",
        ),
        (
            "EPC > 0",
            (mp.epc_actual or 0) > 0,
            f"EPC = R$ {mp.epc_actual:.2f}" if mp.epc_actual else "not set",
            "Rastreie uma comissão real e preencha o EPC na aba KPIs.",
        ),
    ]

    checklist = [
        ChecklistItem(label=label, passed=passed, detail=detail)
        for label, passed, detail, _ in checks
    ]
    suggestions = [suggestion for _, passed, _, suggestion in checks if not passed]

    return ScaleReadiness(
        managed_product_id=product_id,
        name=mp.name,
        checklist=checklist,
        suggestions=suggestions,
        ready_to_scale=all(item.passed for item in checklist),
    )
