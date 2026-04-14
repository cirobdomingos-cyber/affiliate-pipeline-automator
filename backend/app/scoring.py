"""Product scoring — pure functions, no I/O.

This module is the heart of Stage 1. Every scraped product flows through
`score_product` to produce a 0–100 score plus a per-component breakdown that
the UI can show as "why is this product ranked here?".

Design rules:
- No network, no DB, no LLM calls. Trivially unit-testable.
- Missing fields degrade gracefully — a product missing popularity should
  still get a score, just with that component contributing zero.
- Weights are configurable so we can tune them later without touching code
  that calls this module.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from .models import Product, ProductScore, ScoreBreakdown


@dataclass(frozen=True)
class ScoringWeights:
    """Weights must sum to 1.0. Defaults reflect the MVP heuristic:
    popularity and commission matter most, sales-page signals matter least
    until the LLM analyzer in V1 can fill that field reliably.
    """

    commission: float = 0.25
    ticket: float = 0.20
    reputation: float = 0.20
    popularity: float = 0.25
    sales_signals: float = 0.10

    def __post_init__(self) -> None:
        total = (
            self.commission
            + self.ticket
            + self.reputation
            + self.popularity
            + self.sales_signals
        )
        if not math.isclose(total, 1.0, abs_tol=1e-6):
            raise ValueError(f"ScoringWeights must sum to 1.0, got {total}")


DEFAULT_WEIGHTS = ScoringWeights()

# Reference ticket size in BRL. Products at this price contribute the full
# ticket component; cheaper products contribute proportionally less on a log
# scale so that R$ 1000 isn't 10x R$ 100 — it's roughly 1.5x.
_TICKET_REFERENCE_BRL = 500.0
_POPULARITY_REFERENCE = 100.0
_ASSUMED_CONVERSION_RATE = 0.02  # 2% — industry-typical bridge-page CVR


def _normalize_commission(pct: float | None) -> float:
    """Map commission percent (0–100) to a 0–1 score.
    Commissions above 60% are common in Brazilian infoproducts; cap there to
    avoid letting one outlier dominate."""
    if pct is None:
        return 0.0
    return min(pct, 60.0) / 60.0


def _normalize_ticket(price_brl: float | None) -> float:
    """Log-scaled — diminishing returns above the reference price."""
    if price_brl is None or price_brl <= 0:
        return 0.0
    return min(1.0, math.log1p(price_brl) / math.log1p(_TICKET_REFERENCE_BRL))


def _normalize_popularity(pop: float | None) -> float:
    if pop is None or pop <= 0:
        return 0.0
    return min(1.0, math.log1p(pop) / math.log1p(_POPULARITY_REFERENCE))


def _normalize_unit_interval(value: float | None) -> float:
    return 0.0 if value is None else max(0.0, min(1.0, value))


def score_product(
    product: Product,
    weights: ScoringWeights = DEFAULT_WEIGHTS,
) -> ProductScore:
    """Compute a 0–100 score and per-component breakdown for a single product."""
    commission = _normalize_commission(product.commission_pct)
    ticket = _normalize_ticket(product.price_brl)
    reputation = _normalize_unit_interval(product.producer_reputation)
    popularity = _normalize_popularity(product.popularity)
    sales = _normalize_unit_interval(product.sales_page_signals)

    breakdown = ScoreBreakdown(
        commission=round(commission * weights.commission * 100, 2),
        ticket=round(ticket * weights.ticket * 100, 2),
        reputation=round(reputation * weights.reputation * 100, 2),
        popularity=round(popularity * weights.popularity * 100, 2),
        sales_signals=round(sales * weights.sales_signals * 100, 2),
    )

    total = round(
        breakdown.commission
        + breakdown.ticket
        + breakdown.reputation
        + breakdown.popularity
        + breakdown.sales_signals,
        2,
    )

    epc = (product.commission_brl or 0.0) * _ASSUMED_CONVERSION_RATE

    return ProductScore(
        product_id=product.id,
        score=total,
        components=breakdown,
        expected_value_per_visit=round(epc, 4),
    )


def rank_products(products: list[Product], weights: ScoringWeights = DEFAULT_WEIGHTS):
    """Score and sort. Returns highest-scoring first."""
    scored = [(p, score_product(p, weights)) for p in products]
    scored.sort(key=lambda ps: ps[1].score, reverse=True)
    return scored
