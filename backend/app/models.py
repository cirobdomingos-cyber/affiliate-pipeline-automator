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
