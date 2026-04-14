"""Tests for the Amazon PA-API scraper.

Coverage:
- Commission rate mapping (known groups, unknown fallback)
- Pure response parser against the captured fixture
- SigV4 signer structure (Authorization header format, determinism)
- MockAmazonScraper end-to-end
- Credentials gating on AmazonScraper (no env vars → fail fast)
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import pytest

from backend.app.models import Niche, Platform
from backend.app.scrapers.amazon import (
    AMAZON_COMMISSION_RATES,
    AmazonScraper,
    MockAmazonScraper,
    commission_rate_for,
    parse_search_response,
    sign_paapi_request,
)
from backend.app.scrapers.base import ScraperError


_FIXTURE = (
    Path(__file__).resolve().parent
    / "fixtures"
    / "amazon_search_response.json"
)


@pytest.fixture
def fixture_payload() -> dict:
    return json.loads(_FIXTURE.read_text(encoding="utf-8"))


# -------------------- Commission rates --------------------


class TestCommissionRates:
    def test_book_is_10_percent(self):
        assert commission_rate_for("Book") == 10.0

    def test_electronics_is_2_5_percent(self):
        assert commission_rate_for("Electronics") == 2.5

    def test_shoes_is_8_percent(self):
        assert commission_rate_for("Shoes") == 8.0

    def test_unknown_group_falls_back_to_4_percent(self):
        assert commission_rate_for("UnknownCategory") == 4.0

    def test_none_falls_back_to_4_percent(self):
        assert commission_rate_for(None) == 4.0

    def test_rate_table_covers_major_categories(self):
        # Regression guard — if somebody drops a category from the table,
        # this test tells them which one went missing.
        required = {"Book", "Electronics", "Kitchen", "Shoes", "HealthPersonalCare"}
        assert required.issubset(AMAZON_COMMISSION_RATES.keys())


# -------------------- Response parser --------------------


class TestParseSearchResponse:
    def test_returns_all_fixture_items(self, fixture_payload):
        products = parse_search_response(fixture_payload)
        assert len(products) == 8

    def test_all_products_have_amazon_platform(self, fixture_payload):
        products = parse_search_response(fixture_payload)
        assert all(p.platform == Platform.AMAZON for p in products)

    def test_external_id_is_asin(self, fixture_payload):
        products = parse_search_response(fixture_payload)
        assert products[0].external_id == "B07WJNBXGQ"

    def test_mindset_book_has_10_pct_commission(self, fixture_payload):
        products = parse_search_response(fixture_payload)
        mindset = next(p for p in products if "Mindset" in p.name)
        assert mindset.commission_pct == 10.0
        assert mindset.category == "Book"

    def test_kindle_electronics_has_2_5_pct_commission(self, fixture_payload):
        products = parse_search_response(fixture_payload)
        kindle = next(p for p in products if "Kindle" in p.name)
        assert kindle.commission_pct == 2.5
        assert kindle.category == "Electronics"

    def test_nike_shoes_has_8_pct_commission(self, fixture_payload):
        products = parse_search_response(fixture_payload)
        nike = next(p for p in products if "Nike" in p.name)
        assert nike.commission_pct == 8.0

    def test_prices_parsed(self, fixture_payload):
        products = parse_search_response(fixture_payload)
        notebook = next(p for p in products if "Notebook Lenovo" in p.name)
        assert notebook.price_brl == 3299.0

    def test_commission_brl_computed(self, fixture_payload):
        products = parse_search_response(fixture_payload)
        notebook = next(p for p in products if "Notebook Lenovo" in p.name)
        # 3299 * 2.5% ≈ 82.475, float rounds to 82.47 — tolerance absorbs that.
        assert notebook.commission_brl == pytest.approx(82.47, abs=0.02)

    def test_producer_name_from_brand(self, fixture_payload):
        products = parse_search_response(fixture_payload)
        kindle = next(p for p in products if "Kindle" in p.name)
        assert kindle.producer_name == "Amazon"

    def test_electronics_classified_as_tech_saas_niche(self, fixture_payload):
        products = parse_search_response(fixture_payload)
        kindle = next(p for p in products if "Kindle" in p.name)
        assert kindle.niche == Niche.TECH_SAAS

    def test_book_classified_as_business_niche(self, fixture_payload):
        products = parse_search_response(fixture_payload)
        mindset = next(p for p in products if "Mindset" in p.name)
        assert mindset.niche == Niche.BUSINESS

    def test_detail_url_preserved(self, fixture_payload):
        products = parse_search_response(fixture_payload)
        assert all(p.url.startswith("https://www.amazon.com.br/dp/") for p in products)

    def test_limit_caps_results(self, fixture_payload):
        products = parse_search_response(fixture_payload, limit=3)
        assert len(products) == 3

    def test_empty_response_returns_empty_list(self):
        assert parse_search_response({}) == []
        assert parse_search_response({"SearchResult": {}}) == []
        assert parse_search_response({"SearchResult": {"Items": []}}) == []


# -------------------- SigV4 signer --------------------


class TestSigV4Signer:
    _args = dict(
        access_key="AKIDEXAMPLE",
        secret_key="wJalrXUtnFEMI/K7MDENG+bPxRfiCYEXAMPLEKEY",
        host="webservices.amazon.com.br",
        region="us-east-1",
        target="com.amazon.paapi5.v1.ProductAdvertisingAPIv1.SearchItems",
        body='{"Keywords":"test"}',
        now=dt.datetime(2026, 4, 13, 12, 0, 0, tzinfo=dt.timezone.utc),
    )

    def test_returns_all_required_headers(self):
        headers = sign_paapi_request(**self._args)
        for key in ("Host", "Content-Type", "Content-Encoding", "X-Amz-Target", "X-Amz-Date", "Authorization"):
            assert key in headers, f"missing header {key}"

    def test_authorization_header_structure(self):
        headers = sign_paapi_request(**self._args)
        auth = headers["Authorization"]
        assert auth.startswith("AWS4-HMAC-SHA256 ")
        assert "Credential=AKIDEXAMPLE/" in auth
        assert "SignedHeaders=content-encoding;content-type;host;x-amz-date;x-amz-target" in auth
        assert "Signature=" in auth
        # Signature is 64 hex chars (SHA256)
        signature = auth.split("Signature=")[1]
        assert len(signature) == 64
        assert all(c in "0123456789abcdef" for c in signature)

    def test_amz_date_matches_frozen_time(self):
        headers = sign_paapi_request(**self._args)
        assert headers["X-Amz-Date"] == "20260413T120000Z"

    def test_credential_scope_contains_region_and_service(self):
        headers = sign_paapi_request(**self._args)
        auth = headers["Authorization"]
        assert "us-east-1/ProductAdvertisingAPI/aws4_request" in auth

    def test_determinism(self):
        # Same inputs → same signature. This is the whole point — it's what
        # makes SigV4 verifiable on the server side.
        h1 = sign_paapi_request(**self._args)
        h2 = sign_paapi_request(**self._args)
        assert h1["Authorization"] == h2["Authorization"]

    def test_different_body_produces_different_signature(self):
        h1 = sign_paapi_request(**self._args)
        h2 = sign_paapi_request(**{**self._args, "body": '{"Keywords":"different"}'})
        assert h1["Authorization"] != h2["Authorization"]

    def test_different_secret_produces_different_signature(self):
        h1 = sign_paapi_request(**self._args)
        h2 = sign_paapi_request(**{**self._args, "secret_key": "DIFFERENT" + "x" * 30})
        assert h1["Authorization"] != h2["Authorization"]

    def test_different_timestamp_produces_different_signature(self):
        h1 = sign_paapi_request(**self._args)
        later = self._args["now"] + dt.timedelta(hours=1)
        h2 = sign_paapi_request(**{**self._args, "now": later})
        assert h1["Authorization"] != h2["Authorization"]


# -------------------- Scraper integration --------------------


@pytest.mark.asyncio
class TestMockAmazonScraper:
    async def test_fetch_returns_fixture_products(self):
        scraper = MockAmazonScraper()
        products = await scraper.fetch(limit=50)
        assert len(products) == 8
        assert products[0].platform == Platform.AMAZON

    async def test_fetch_respects_limit(self):
        scraper = MockAmazonScraper()
        products = await scraper.fetch(limit=3)
        assert len(products) == 3


class TestAmazonScraperCredentials:
    def test_no_env_vars_means_not_configured(self, monkeypatch):
        for var in (
            "AMAZON_ACCESS_KEY",
            "AMAZON_SECRET_KEY",
            "AMAZON_PARTNER_TAG",
        ):
            monkeypatch.delenv(var, raising=False)
        scraper = AmazonScraper()
        assert scraper.credentials_configured is False

    def test_all_env_vars_means_configured(self, monkeypatch):
        monkeypatch.setenv("AMAZON_ACCESS_KEY", "AKIDEXAMPLE")
        monkeypatch.setenv("AMAZON_SECRET_KEY", "s" * 40)
        monkeypatch.setenv("AMAZON_PARTNER_TAG", "demo-20")
        scraper = AmazonScraper()
        assert scraper.credentials_configured is True

    @pytest.mark.asyncio
    async def test_fetch_without_credentials_raises_clear_error(self, monkeypatch):
        for var in (
            "AMAZON_ACCESS_KEY",
            "AMAZON_SECRET_KEY",
            "AMAZON_PARTNER_TAG",
        ):
            monkeypatch.delenv(var, raising=False)
        scraper = AmazonScraper()
        with pytest.raises(ScraperError, match="credentials missing"):
            await scraper.fetch(limit=10)
