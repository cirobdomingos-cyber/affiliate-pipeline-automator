"""Unit tests for the scoring module.

The whole point of keeping `scoring.py` pure is that these tests run with no
network, no DB, no fixtures heavier than a Product literal. If a regression
breaks scoring, this file fails in milliseconds.
"""

from __future__ import annotations

import math

import pytest

from backend.app.models import Niche, Platform, Product
from backend.app.scoring import (
    DEFAULT_WEIGHTS,
    ScoringWeights,
    rank_products,
    score_product,
)


def make_product(**overrides) -> Product:
    base = dict(
        platform=Platform.HOTMART,
        external_id="TEST-001",
        name="Test Product",
        url="https://example.com/p/TEST-001",
        category="Marketing Digital",
        niche=Niche.DIGITAL_MARKETING,
        price_brl=500.0,
        commission_pct=50.0,
        popularity=50.0,
        producer_reputation=0.8,
        sales_page_signals=0.7,
    )
    base.update(overrides)
    return Product(**base)


class TestScoreBounds:
    def test_score_is_between_0_and_100(self):
        score = score_product(make_product())
        assert 0.0 <= score.score <= 100.0

    def test_all_max_yields_100(self):
        product = make_product(
            commission_pct=60.0,
            price_brl=10_000.0,
            popularity=10_000.0,
            producer_reputation=1.0,
            sales_page_signals=1.0,
        )
        score = score_product(product)
        assert score.score == pytest.approx(100.0, abs=0.5)

    def test_all_missing_yields_0(self):
        product = Product(
            platform=Platform.HOTMART,
            external_id="EMPTY",
            name="Empty",
            url="https://example.com/empty",
        )
        score = score_product(product)
        assert score.score == 0.0
        assert score.expected_value_per_visit == 0.0


class TestComponentBreakdown:
    def test_components_sum_to_total(self):
        product = make_product()
        score = score_product(product)
        components_sum = (
            score.components.commission
            + score.components.ticket
            + score.components.reputation
            + score.components.popularity
            + score.components.sales_signals
        )
        assert score.score == pytest.approx(components_sum, abs=0.05)

    def test_zero_commission_zeros_commission_component(self):
        product = make_product(commission_pct=0.0)
        score = score_product(product)
        assert score.components.commission == 0.0
        assert score.components.ticket > 0  # other components unaffected

    def test_missing_popularity_does_not_break_score(self):
        product = make_product(popularity=None)
        score = score_product(product)
        assert score.components.popularity == 0.0
        assert score.score > 0  # remaining components still contribute


class TestEPC:
    def test_epc_uses_commission_brl(self):
        product = make_product(price_brl=1000.0, commission_pct=50.0)
        score = score_product(product)
        # commission_brl = 500, assumed CVR 2% -> EPC = 10.0
        assert score.expected_value_per_visit == pytest.approx(10.0, abs=0.01)

    def test_epc_zero_when_commission_unknown(self):
        product = make_product(commission_pct=None)
        score = score_product(product)
        assert score.expected_value_per_visit == 0.0


class TestRanking:
    def test_higher_commission_outranks_lower_when_other_fields_equal(self):
        low = make_product(external_id="LOW", commission_pct=20.0)
        high = make_product(external_id="HIGH", commission_pct=55.0)
        ranked = rank_products([low, high])
        assert ranked[0][0].external_id == "HIGH"

    def test_higher_popularity_outranks_lower(self):
        cold = make_product(external_id="COLD", popularity=5.0)
        hot = make_product(external_id="HOT", popularity=95.0)
        ranked = rank_products([cold, hot])
        assert ranked[0][0].external_id == "HOT"

    def test_ranking_preserves_all_input_products(self):
        products = [make_product(external_id=f"P{i}", commission_pct=float(i * 5)) for i in range(1, 11)]
        ranked = rank_products(products)
        assert len(ranked) == len(products)
        scores = [s.score for _, s in ranked]
        assert scores == sorted(scores, reverse=True)


class TestScoringWeights:
    def test_weights_must_sum_to_one(self):
        with pytest.raises(ValueError):
            ScoringWeights(
                commission=0.5,
                ticket=0.5,
                reputation=0.5,
                popularity=0.5,
                sales_signals=0.5,
            )

    def test_default_weights_sum_to_one(self):
        w = DEFAULT_WEIGHTS
        total = w.commission + w.ticket + w.reputation + w.popularity + w.sales_signals
        assert math.isclose(total, 1.0)

    def test_custom_weights_change_ranking(self):
        # Heavily weight commission — a high-commission, low-popularity product
        # should beat a low-commission, high-popularity product.
        commission_heavy = ScoringWeights(
            commission=0.80,
            ticket=0.05,
            reputation=0.05,
            popularity=0.05,
            sales_signals=0.05,
        )
        big_commission = make_product(
            external_id="BIGCOM",
            commission_pct=60.0,
            popularity=10.0,
        )
        big_popularity = make_product(
            external_id="BIGPOP",
            commission_pct=10.0,
            popularity=100.0,
        )
        ranked = rank_products([big_popularity, big_commission], weights=commission_heavy)
        assert ranked[0][0].external_id == "BIGCOM"
