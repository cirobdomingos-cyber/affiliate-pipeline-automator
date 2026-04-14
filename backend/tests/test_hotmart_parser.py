"""Hotmart parser tests against a real captured HTML fixture.

The fixture is `hotmart_marketplace.html` — a 188 KB snapshot of the
actual live `https://hotmart.com/pt-br/marketplace` response, saved so
the parser test runs offline in milliseconds.

If Hotmart changes their page structure, the right fix is:
1. Re-run `scripts/probe_hotmart.py` to confirm what changed
2. Refresh the fixture by re-fetching the live HTML
3. Update `_find_products_in_tree` or `_product_from_json` as needed
"""

from __future__ import annotations

from pathlib import Path

import pytest

from backend.app.models import Platform
from backend.app.scrapers.hotmart import (
    _classify_niche,
    _popularity_from_rating,
    parse_marketplace_html,
)


_FIXTURE_PATH = Path(__file__).resolve().parent / "fixtures" / "hotmart_marketplace.html"


@pytest.fixture
def live_html() -> str:
    return _FIXTURE_PATH.read_text(encoding="utf-8")


class TestParseLiveFixture:
    def test_extracts_at_least_one_product(self, live_html):
        products = parse_marketplace_html(live_html)
        assert len(products) > 0

    def test_all_products_are_hotmart(self, live_html):
        products = parse_marketplace_html(live_html)
        assert all(p.platform == Platform.HOTMART for p in products)

    def test_every_product_has_name_and_url(self, live_html):
        products = parse_marketplace_html(live_html)
        for p in products:
            assert p.name
            assert p.url.startswith("https://")

    def test_every_product_has_external_id(self, live_html):
        products = parse_marketplace_html(live_html)
        for p in products:
            assert p.external_id
            # Hotmart reference codes look like a letter + digits + letter
            assert len(p.external_id) >= 5

    def test_products_have_producer_name(self, live_html):
        products = parse_marketplace_html(live_html)
        # Not every product may have one in edge cases, but the vast majority should.
        with_producer = [p for p in products if p.producer_name]
        assert len(with_producer) >= len(products) * 0.7

    def test_popularity_derived_from_rating_and_reviews(self, live_html):
        products = parse_marketplace_html(live_html)
        with_popularity = [p for p in products if p.popularity is not None]
        assert len(with_popularity) > 0
        # rating × log1p(reviews) is always > 0 for rated products
        for p in with_popularity:
            assert p.popularity > 0

    def test_reputation_derived_from_rating(self, live_html):
        products = parse_marketplace_html(live_html)
        with_reputation = [p for p in products if p.producer_reputation is not None]
        assert len(with_reputation) > 0
        for p in with_reputation:
            assert 0 <= p.producer_reputation <= 1

    def test_description_stored_in_raw(self, live_html):
        # Critical for the LLM analyzer — the description field is what the
        # sales-page signal extractor will eventually read.
        products = parse_marketplace_html(live_html)
        with_desc = [
            p for p in products if (p.raw or {}).get("description")
        ]
        assert len(with_desc) > 0

    def test_price_and_commission_are_none_on_public_feed(self, live_html):
        # The public marketplace hides these behind affiliate login.
        # The scraper must not fabricate them.
        products = parse_marketplace_html(live_html)
        for p in products:
            assert p.price_brl is None
            assert p.commission_pct is None

    def test_source_label_on_raw(self, live_html):
        products = parse_marketplace_html(live_html)
        for p in products:
            assert p.raw.get("source") == "hotmart_marketplace_nextdata"

    def test_limit_caps_results(self, live_html):
        products = parse_marketplace_html(live_html, limit=3)
        assert len(products) <= 3


class TestEmptyInputs:
    def test_empty_html_returns_empty_list(self):
        assert parse_marketplace_html("") == []

    def test_html_without_hydration_script_returns_empty(self):
        assert parse_marketplace_html("<html><body>no data</body></html>") == []

    def test_hydration_blob_without_products_returns_empty(self):
        html = '<script>{"props":{"pageProps":{"foo":"bar"}}}</script>'
        assert parse_marketplace_html(html) == []


class TestPopularitySynth:
    def test_rating_times_log_reviews(self):
        # 4.8 × log1p(1000) ≈ 4.8 × 6.908 ≈ 33.16
        result = _popularity_from_rating(4.8, 1000)
        assert result == pytest.approx(33.16, abs=0.1)

    def test_zero_reviews_returns_zero(self):
        assert _popularity_from_rating(5.0, 0) == 0.0

    def test_none_rating_returns_none(self):
        assert _popularity_from_rating(None, 100) is None

    def test_none_reviews_returns_none(self):
        assert _popularity_from_rating(4.5, None) is None

    def test_high_reviews_outweigh_slight_rating_difference(self):
        # A product with 2000 reviews at 4.8 should outrank one with
        # 20 reviews at 4.95 — review count dominates.
        popular_ok = _popularity_from_rating(4.8, 2000)
        niche_great = _popularity_from_rating(4.95, 20)
        assert popular_ok > niche_great


class TestNicheClassification:
    def test_finance_keywords(self):
        from backend.app.models import Niche
        assert _classify_niche("investimentos") == Niche.FINANCE
        assert _classify_niche("bitcoin") == Niche.FINANCE
        assert _classify_niche("finance") == Niche.FINANCE

    def test_marketing_keywords(self):
        from backend.app.models import Niche
        assert _classify_niche("marketing") == Niche.DIGITAL_MARKETING
        assert _classify_niche("trafego-pago") == Niche.DIGITAL_MARKETING

    def test_unknown_returns_other(self):
        from backend.app.models import Niche
        assert _classify_niche("astrology") == Niche.OTHER

    def test_none_returns_none(self):
        assert _classify_niche(None) is None
