"""Integration test — discovery service with LLM analyzer and niche re-ranking.

Exercises the full V1 slice: MockScraper → LLM sales-page analysis →
LLM niche-fit ranking → DuckDB persistence. Still offline (fake client).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from backend.app.db import ProductRepository
from backend.app.llm import LLMAnalyzer
from backend.app.models import NicheFit, NicheFitBatch, SalesPageSignals
from backend.app.scrapers import MockScraper
from backend.app.services.discovery import run_discovery
from backend.tests.test_llm_analyzer import FakeAnthropicClient


def _canned_signals(composite: float) -> SalesPageSignals:
    return SalesPageSignals(
        scarcity=0.5,
        social_proof=0.7,
        guarantee_strength=0.6,
        urgency=0.4,
        audience_clarity=0.9,
        composite=composite,
        notes="Test signals.",
    )


@pytest.mark.asyncio
async def test_discovery_with_llm_enrichment(tmp_path: Path):
    repo = ProductRepository(db_path=tmp_path / "test.duckdb")

    # Mock fixture has 8 products, so we need 8 sales-page responses + 1 niche-fit batch.
    sales_page_responses = [_canned_signals(0.5 + i * 0.05) for i in range(8)]

    # We don't know the product IDs until the scraper runs, so build the
    # niche-fit response lazily inside a side-effect client. For simplicity,
    # feed a batch that matches the fixture's 8 products by running the
    # scraper first to get their IDs.
    mock_scraper = MockScraper()
    fixture_products = await mock_scraper.fetch(limit=50)
    niche_rankings = NicheFitBatch(
        rankings=[
            NicheFit(
                product_id=p.id,
                fit_score=100.0 - idx * 10.0,
                reasoning=f"Fit for {p.name}",
            )
            for idx, p in enumerate(fixture_products)
        ]
    )

    fake_client = FakeAnthropicClient.with_responses(
        *sales_page_responses,
        niche_rankings,
    )
    analyzer = LLMAnalyzer(client=fake_client)

    def page_text_for(product):
        return f"<fake sales page for {product.name}>"

    result = await run_discovery(
        repo=repo,
        limit_per_source=50,
        top_n=8,
        scrapers=[MockScraper()],
        llm_analyzer=analyzer,
        sales_page_text_provider=page_text_for,
        target_niche="digital marketing",
        target_audience="first-year affiliates",
    )

    assert result.fetched == 8
    assert result.llm_signals_filled == 8
    assert len(result.niche_rankings) == 8
    assert not result.errors

    # Top pick should be the product the niche ranker put first (fit_score=100).
    top_product_id = result.top[0].product.id
    assert top_product_id == niche_rankings.rankings[0].product_id

    # Haiku called 8 times + Sonnet called 1 time = 9 total.
    assert len(fake_client.messages.calls) == 9
    haiku_calls = [c for c in fake_client.messages.calls if c["model"] == "claude-haiku-4-5"]
    sonnet_calls = [c for c in fake_client.messages.calls if c["model"] == "claude-sonnet-4-6"]
    assert len(haiku_calls) == 8
    assert len(sonnet_calls) == 1
