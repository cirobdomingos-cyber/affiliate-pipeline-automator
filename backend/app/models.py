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
