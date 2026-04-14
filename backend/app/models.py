"""Domain models — pure pydantic, no I/O.

These are the contract that scrapers, scoring, persistence, and the API all
agree on. Keeping them isolated is the single most important architectural
rule in this repo: the moment a model imports httpx or duckdb, the
testability of the whole system collapses.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from hashlib import sha1
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, computed_field


class Platform(StrEnum):
    HOTMART = "hotmart"
    MONETIZZE = "monetizze"
    EDUZZ = "eduzz"
    AMAZON = "amazon"
    SHOPEE = "shopee"
    MAGALU = "magalu"
    HOSTINGER = "hostinger"
    SEMRUSH = "semrush"
    DIRECT = "direct"


class Niche(StrEnum):
    FINANCE = "finance"
    DIGITAL_MARKETING = "digital_marketing"
    HEALTH = "health"
    TECH_SAAS = "tech_saas"
    BUSINESS = "business"
    OTHER = "other"


def _stable_id(platform: Platform, external_id: str) -> str:
    return sha1(f"{platform.value}:{external_id}".encode()).hexdigest()[:16]


class Product(BaseModel):
    """A single affiliate product as observed at a point in time."""

    model_config = ConfigDict(frozen=False)

    platform: Platform
    external_id: str
    name: str
    url: str
    category: str | None = None
    niche: Niche | None = None
    price_brl: float | None = Field(default=None, ge=0)
    commission_pct: float | None = Field(default=None, ge=0, le=100)
    popularity: float | None = Field(default=None, ge=0)
    producer_name: str | None = None
    producer_reputation: float | None = Field(default=None, ge=0, le=1)
    sales_page_signals: float | None = Field(default=None, ge=0, le=1)
    scraped_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    raw: dict[str, Any] = Field(default_factory=dict)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def id(self) -> str:
        return _stable_id(self.platform, self.external_id)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def commission_brl(self) -> float | None:
        if self.price_brl is None or self.commission_pct is None:
            return None
        return round(self.price_brl * self.commission_pct / 100.0, 2)


class ScoreBreakdown(BaseModel):
    """Per-component contributions to the final score. Drives explainability."""

    commission: float = 0.0
    ticket: float = 0.0
    reputation: float = 0.0
    popularity: float = 0.0
    sales_signals: float = 0.0


class ProductScore(BaseModel):
    product_id: str
    score: float = Field(ge=0, le=100)
    components: ScoreBreakdown
    expected_value_per_visit: float = Field(
        ge=0,
        description=(
            "Heuristic EPC: commission_brl * assumed conversion rate. "
            "Used to compare products on absolute earning potential, not just rank."
        ),
    )
    computed_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class ScoredProduct(BaseModel):
    """Convenience join of Product + ProductScore for the API and UI."""

    product: Product
    score: ProductScore


class SalesPageSignals(BaseModel):
    """LLM-extracted signals from a product's sales page.

    Each field is a 0.0–1.0 score reflecting how strongly the page exhibits
    that quality. The aggregate `composite` is what the scoring module reads
    via `Product.sales_page_signals` — it lets the rest of the pipeline stay
    LLM-agnostic.
    """

    scarcity: float = Field(ge=0, le=1, description="Scarcity tactics density (limited spots, countdown, last copies)")
    social_proof: float = Field(ge=0, le=1, description="Testimonials, student count, media mentions density")
    guarantee_strength: float = Field(ge=0, le=1, description="Money-back guarantee clarity and risk-reversal language")
    urgency: float = Field(ge=0, le=1, description="Time-bound urgency framing (deadlines, expiring bonuses)")
    audience_clarity: float = Field(ge=0, le=1, description="How clearly the page names its target audience")
    composite: float = Field(ge=0, le=1, description="Weighted aggregate the scoring module consumes")
    notes: str = Field(default="", description="One-sentence summary of why the page is or isn't compelling")


class NicheFit(BaseModel):
    """LLM ranking of how well a product matches an operator's stated niche."""

    product_id: str
    fit_score: float = Field(ge=0, le=100, description="0–100 — how well this product fits the target niche")
    reasoning: str = Field(description="One sentence explaining the fit")


class NicheFitBatch(BaseModel):
    """Wrapper so a single LLM call can return rankings for many products."""

    rankings: list[NicheFit]


class LinkStatus(StrEnum):
    """Approval state of an affiliate link on a platform.

    Most Brazilian networks (Hotmart, Monetizze, Eduzz) require per-producer
    affiliate approval — your link is worthless until `APPROVED`. Tracking
    status explicitly lets the Link Vault flag pending/rejected links so an
    operator doesn't waste ad spend pointing traffic at a dead URL.
    """

    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"


class AffiliateLink(BaseModel):
    """A single affiliate link stored in the vault.

    The `raw_url` is the bare affiliate URL from the platform — no UTM params
    yet. `TrackedLink` composes `raw_url + UTMParams` into a final URL to use
    in ads, bios, or email campaigns. Keeping them separate means the same
    affiliate link can be reused across campaigns with different UTM tags.
    """

    model_config = ConfigDict(frozen=False)

    id: str = Field(description="Stable ID — hash of (platform, raw_url).")
    product_id: str | None = Field(
        default=None,
        description="Optional link back to a Product from Stage 1 discovery.",
    )
    platform: Platform
    label: str = Field(description="Operator-chosen name — e.g. 'Curso X - Instagram bio'.")
    raw_url: str = Field(description="Base affiliate URL without UTM params.")
    approval_status: LinkStatus = LinkStatus.PENDING
    approved_at: datetime | None = None
    notes: str = ""
    tags: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class UTMParams(BaseModel):
    """UTM parameters for a tracked campaign URL.

    Field names match Google Analytics conventions. Values are normalized
    (lowercased, spaces → hyphens) by the UTM builder so that
    'Instagram Bio' and 'instagram-bio' land in the same analytics bucket.
    """

    source: str = Field(description="utm_source — the channel (instagram, google, tiktok, email).")
    medium: str = Field(description="utm_medium — paid/organic/email/cpc/social.")
    campaign: str = Field(description="utm_campaign — the campaign name.")
    term: str | None = Field(default=None, description="utm_term — paid search keyword.")
    content: str | None = Field(default=None, description="utm_content — creative variant.")


class TrackedLink(BaseModel):
    """The result of composing an AffiliateLink + UTMParams into a final URL."""

    affiliate_link_id: str
    final_url: str
    utm: UTMParams


class TrafficChannel(StrEnum):
    """Channels an affiliate can use to drive traffic.

    Organic channels live in ContentBrief; paid channels live in AdCopyVariant.
    Keeping them in one enum means the UI can show a single 'pick a channel'
    selector that fans out to the right LLM path.
    """

    INSTAGRAM_REEL = "instagram_reel"
    INSTAGRAM_CAROUSEL = "instagram_carousel"
    INSTAGRAM_STORY = "instagram_story"
    TIKTOK = "tiktok"
    YOUTUBE_LONG = "youtube_long"
    YOUTUBE_SHORT = "youtube_short"
    BLOG_POST = "blog_post"
    WHATSAPP_BROADCAST = "whatsapp_broadcast"
    META_AD = "meta_ad"
    GOOGLE_AD = "google_ad"
    TIKTOK_AD = "tiktok_ad"


ORGANIC_CHANNELS: set[TrafficChannel] = {
    TrafficChannel.INSTAGRAM_REEL,
    TrafficChannel.INSTAGRAM_CAROUSEL,
    TrafficChannel.INSTAGRAM_STORY,
    TrafficChannel.TIKTOK,
    TrafficChannel.YOUTUBE_LONG,
    TrafficChannel.YOUTUBE_SHORT,
    TrafficChannel.BLOG_POST,
    TrafficChannel.WHATSAPP_BROADCAST,
}

PAID_CHANNELS: set[TrafficChannel] = {
    TrafficChannel.META_AD,
    TrafficChannel.GOOGLE_AD,
    TrafficChannel.TIKTOK_AD,
}


class ContentBrief(BaseModel):
    """A single organic post brief — one row on the content calendar."""

    channel: TrafficChannel
    day_offset: int = Field(ge=0, description="Day number in the plan (0-indexed).")
    hook: str = Field(description="Opening 1–2 sentences that stop the scroll.")
    body: str = Field(description="Main content — 3–5 sentences of what to say.")
    call_to_action: str = Field(description="Exactly what the viewer should do next.")
    hashtags: list[str] = Field(default_factory=list, description="5–10 relevant hashtags, no # prefix.")
    format_notes: str = Field(
        default="",
        description="Production hints — 'vertical video, 15s, text overlay', etc.",
    )


class OrganicPlan(BaseModel):
    """Complete organic strategy + content calendar for a product.

    The `target_audience`, `positioning`, and `key_messages` fields are the
    strategy doc — written once, reused across every brief. The `briefs` list
    is the calendar. Keeping them in one object means the LLM produces a
    cohesive plan in a single call rather than stitching together unrelated
    posts.
    """

    target_audience: str
    positioning: str
    key_messages: list[str]
    posting_rhythm: str
    briefs: list[ContentBrief]


class AdCopyVariant(BaseModel):
    """A single paid-ad creative variant."""

    platform: TrafficChannel
    headline: str = Field(description="Max 40 chars — the scroll-stopper.")
    primary_text: str = Field(description="The main body of the ad — 2–4 sentences.")
    description: str = Field(description="Supporting text below primary — 1 sentence.")
    target_audience: str = Field(description="Who this variant is aimed at.")
    daily_budget_brl: float = Field(ge=0, description="Suggested daily spend in BRL.")
    creative_notes: str = Field(default="", description="Hook, angle, creative direction.")


class AdVariantBatch(BaseModel):
    """Wrapper so a single LLM call can return many ad variants."""

    variants: list[AdCopyVariant]


class CreativeBrief(BaseModel):
    """A ready-to-paste prompt pair (image + video) for one ad variant.

    Designed around the 2026 SOTA: Ideogram v3 for static images (only tool
    that renders Portuguese headlines cleanly) and Veo 3 / Runway Gen-4 for
    short video. Fields are deliberately plain strings rather than tool-
    specific schemas so the operator can paste them verbatim.

    The `ready_tools` hint tells the UI which external tool each prompt is
    calibrated for, and also doubles as the dispatch key when we later add
    direct API generation via `services/creatives.py`.
    """

    variant_index: int = Field(ge=0, description="Index back into the AdCopyVariant list.")
    platform: TrafficChannel
    aspect_ratio: str = Field(description="e.g. '9:16' for Reels, '1:1' for feed, '16:9' for YouTube.")
    image_prompt: str = Field(
        description="Ideogram v3 / Midjourney compatible prompt. Must include any on-image text verbatim."
    )
    image_tool: str = Field(default="ideogram", description="Recommended image tool for this prompt.")
    video_prompt: str = Field(
        description="Veo 3 / Runway Gen-4 compatible prompt — shot list, camera, 5–8 seconds."
    )
    video_tool: str = Field(default="veo3", description="Recommended video tool for this prompt.")
    video_duration_s: int = Field(default=8, ge=3, le=15)


class CreativeBriefBatch(BaseModel):
    """Wrapper so one LLM call can produce briefs for every ad variant at once."""

    briefs: list[CreativeBrief]


class GeneratedCreative(BaseModel):
    """A concrete image/video produced for one ad variant via an external API.

    Persisted so that refreshing Phase 3 doesn't re-bill. `product_id` is a
    loose string key — can point at either a scraped Product or a
    ManagedProduct; we don't enforce FKs anywhere else in this app either.
    The cost field is optional because fal.ai's response sometimes omits it;
    when present we aggregate it on the KPIs tab as "creative spend".
    """

    id: str
    product_id: str
    variant_index: int = Field(ge=0)
    kind: str  # 'image' | 'video'
    asset_url: str
    source_prompt: str
    model: str
    cost_brl: float | None = Field(default=None, ge=0)
    width: int | None = None
    height: int | None = None
    duration_s: int | None = None
    generated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class ManagedProduct(BaseModel):
    """A product the operator is actively promoting — manually curated.

    Distinct from `Product` (which is scraper output): `ManagedProduct` is the
    operator's working catalog. Each row drives the rest of the affiliate
    module (channels, bridge pages, email sequences, KPIs, scale checklist).
    Keeping it separate from the scrape catalog means a manual entry is never
    overwritten by a re-scrape, and the scrape pipeline stays idempotent.
    """

    model_config = ConfigDict(frozen=False)

    id: str
    name: str
    platform: Platform
    niche: Niche
    commission_pct: float = Field(ge=0, le=100)
    ticket_brl: float = Field(ge=0)
    sales_page_url: str | None = None
    affiliate_url: str
    quality_score: float = Field(ge=0, le=100)
    epc_actual: float | None = Field(default=None, ge=0)
    cpv_actual: float | None = Field(default=None, ge=0)
    notes: str = ""
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @computed_field  # type: ignore[prop-decorator]
    @property
    def recommended(self) -> bool:
        return self.quality_score > 70.0


class OperatorProfile(BaseModel):
    """Single-row onboarding preference. The operator's declared primary niche
    drives default filters on dashboards and LLM prompts downstream."""

    primary_niche: Niche
    completed_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class ShortLink(BaseModel):
    """Operator-owned tracked redirect: /r/{slug} → destination_url.

    The slug is the public identifier — short, memorable, and used as the PK.
    `destination_url` already has UTMs baked in (we compose them at creation
    time rather than at redirect time, so the destination link is testable
    outside the click handler).
    """

    slug: str
    managed_product_id: str
    destination_url: str
    utm_source: str | None = None
    utm_medium: str | None = None
    utm_campaign: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class ClickEvent(BaseModel):
    """Append-only click log — powers Stage 2 stats and Stage 6 KPIs.

    `target_type` splits short-link clicks from bridge-page CTA clicks so we
    can compute bridge conversion as (bridge_cta / short_link) without
    joining multiple tables. `ip_prefix` keeps only the first 3 octets per
    the brief (LGPD-friendly). `ua_family` is a coarse bucket, not a full
    user-agent string.
    """

    id: str
    target_type: str  # 'short_link' | 'bridge_cta'
    target_id: str  # slug for short_link; bridge page slug for bridge_cta
    managed_product_id: str
    ts: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    ip_prefix: str | None = None
    ua_family: str | None = None
    utm_source: str | None = None


class ShortLinkStats(BaseModel):
    """Aggregate view of a short link's clicks."""

    slug: str
    total_clicks: int
    unique_clicks: int  # distinct ip_prefix
    last_click_at: datetime | None = None
    by_utm_source: dict[str, int] = Field(default_factory=dict)


class ChannelStatus(StrEnum):
    ACTIVE = "active"
    PAUSED = "paused"


class ChannelConfig(BaseModel):
    """Per-product traffic channel configuration (Stage 3).

    One row per (product, channel) pair — enforced at DB level. Organic
    channels leave `daily_budget_brl` null; paid channels require it. The
    stale-alert logic in the API only inspects paid channels with status
    ACTIVE, so pausing a channel is the operator's opt-out from the alert.
    """

    id: str
    managed_product_id: str
    channel: TrafficChannel
    status: ChannelStatus = ChannelStatus.ACTIVE
    daily_budget_brl: float | None = Field(default=None, ge=0)
    daily_click_goal: int | None = Field(default=None, ge=0)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class StaleChannelAlert(BaseModel):
    """One line item on the alerts panel — a paid channel running without
    clicks in the last 24h."""

    managed_product_id: str
    managed_product_name: str
    channel: TrafficChannel
    daily_budget_brl: float | None
    clicks_last_24h: int


class BridgePage(BaseModel):
    """Standalone landing page rendered at /bp/{slug}.

    Minimal content shape intentionally — a bridge page is pre-sell, not a
    full sales letter. If the operator wants a full sales letter the
    destination is the producer's page, which is where `cta_url` points.
    """

    slug: str
    managed_product_id: str
    headline: str
    subheadline: str | None = None
    bullets: list[str] = Field(default_factory=list, max_length=5)
    cta_text: str
    cta_url: str
    primary_color: str = "#2563eb"
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class EmailSequenceStep(BaseModel):
    """One email in a nurture sequence. `delay_days` is measured from the
    previous step (or from opt-in, if this is step 0)."""

    id: str
    sequence_id: str
    step_order: int = Field(ge=0, le=6)  # up to 7 (0..6)
    delay_days: int = Field(ge=0)
    subject: str
    body: str


class EmailSequence(BaseModel):
    """A named multi-email nurture sequence for a single managed product.
    Persisted here so a future scheduler (APScheduler, Celery, etc.) can fire
    the steps without the operator rewriting them."""

    id: str
    managed_product_id: str
    name: str
    mailerlite_group_id: str | None = None
    steps: list[EmailSequenceStep] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class SubscriberCountSnapshot(BaseModel):
    """Latest MailerLite group-subscriber count for a managed product.
    Refreshed on demand by hitting the /mailerlite/sync-counts endpoint."""

    managed_product_id: str
    count: int = Field(ge=0)
    synced_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
