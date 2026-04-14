"""Discovery service — orchestrates scrapers, LLM analysis, scoring, and persistence.

This is where the ports come together. The API and UI both call
`run_discovery`; nothing else in the codebase needs to know which platforms
are wired in, whether the LLM analyzer ran, or how scoring works.
"""

from __future__ import annotations

import asyncio
import logging

from pydantic import BaseModel

from ..db import ProductRepository
from ..llm import LLMAnalyzer
from ..models import NicheFit, Product, ScoredProduct
from ..scoring import DEFAULT_WEIGHTS, ScoringWeights, rank_products
from ..scrapers import (
    AmazonScraper,
    HotmartScraper,
    MockAmazonScraper,
    MockScraper,
    ScraperProtocol,
)

logger = logging.getLogger(__name__)


class DiscoveryResult(BaseModel):
    fetched: int
    scored: int
    top: list[ScoredProduct]
    sources: list[str]
    errors: list[str]
    llm_signals_filled: int = 0
    niche_rankings: list[NicheFit] = []


def _default_scrapers(use_mock: bool) -> list[ScraperProtocol]:
    """Pick the scraper set based on mode.

    Mock mode returns Hotmart + Amazon fixtures so the demo shows multi-platform
    data. Live mode always includes Hotmart, and only adds Amazon when
    PA-API credentials are present in the environment — otherwise the Amazon
    scraper would always fail with a credentials error and pollute the
    discovery result.
    """
    if use_mock:
        return [MockScraper(), MockAmazonScraper()]

    scrapers: list[ScraperProtocol] = [HotmartScraper()]
    amazon = AmazonScraper()
    if amazon.credentials_configured:
        scrapers.append(amazon)
    return scrapers


async def run_discovery(
    *,
    repo: ProductRepository,
    limit_per_source: int = 50,
    top_n: int = 25,
    weights: ScoringWeights = DEFAULT_WEIGHTS,
    use_mock: bool = False,
    scrapers: list[ScraperProtocol] | None = None,
    llm_analyzer: LLMAnalyzer | None = None,
    sales_page_text_provider=None,
    target_niche: str | None = None,
    target_audience: str | None = None,
) -> DiscoveryResult:
    """Run the full discovery pipeline.

    Optional LLM enrichment:
    - If `llm_analyzer` and `sales_page_text_provider` are both given, every
      fetched product gets a Haiku sales-page analysis and `sales_page_signals`
      is populated before scoring.
    - If `llm_analyzer`, `target_niche`, and `target_audience` are all given,
      a single Sonnet call re-ranks the fetched catalog by niche fit.
    Both are off by default — the MVP path stays free.
    """
    scrapers = scrapers or _default_scrapers(use_mock)
    all_products: list[Product] = []
    sources: list[str] = []
    errors: list[str] = []

    fetch_results = await asyncio.gather(
        *(s.fetch(limit=limit_per_source) for s in scrapers),
        return_exceptions=True,
    )

    for scraper, result in zip(scrapers, fetch_results):
        sources.append(scraper.platform.value)
        if isinstance(result, Exception):
            msg = f"{scraper.platform.value}: {result}"
            logger.warning("Scraper failed — %s", msg)
            errors.append(msg)
            continue
        all_products.extend(result)

    llm_signals_filled = 0
    if llm_analyzer is not None and sales_page_text_provider is not None:
        for product in all_products:
            try:
                page_text = sales_page_text_provider(product)
                if not page_text:
                    continue
                signals = llm_analyzer.analyze_sales_page(
                    product_name=product.name,
                    page_text=page_text,
                )
                product.sales_page_signals = signals.composite
                product.raw["sales_page_signals_detail"] = signals.model_dump()
                llm_signals_filled += 1
            except Exception as exc:  # noqa: BLE001 — degrade gracefully
                logger.warning("LLM sales-page analysis failed for %s: %s", product.name, exc)
                errors.append(f"llm sales-page: {product.name}: {exc}")

    niche_rankings: list[NicheFit] = []
    if (
        llm_analyzer is not None
        and target_niche
        and target_audience
        and all_products
    ):
        try:
            niche_rankings = llm_analyzer.rank_by_niche_fit(
                target_niche=target_niche,
                target_audience=target_audience,
                products=all_products,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("LLM niche-fit ranking failed: %s", exc)
            errors.append(f"llm niche-fit: {exc}")

    # Mock data stays in-memory only. Persisting it would pollute the
    # "live catalog" analytics with hand-crafted fixture rows that the
    # operator has no business making link-vault or traffic-plan decisions
    # against. The Top picks tab still works in mock mode because it reads
    # `result.top` from session state, not from the DB.
    if not use_mock:
        repo.upsert_products(all_products)

    ranked = rank_products(all_products, weights=weights)
    scores = [score for _, score in ranked]
    if not use_mock:
        repo.replace_scores(scores)

    if niche_rankings:
        fit_by_id = {nf.product_id: nf.fit_score for nf in niche_rankings}
        ranked.sort(
            key=lambda ps: (
                fit_by_id.get(ps[0].id, 0.0),
                ps[1].score,
            ),
            reverse=True,
        )

    top = [
        ScoredProduct(product=product, score=score)
        for product, score in ranked[:top_n]
    ]

    return DiscoveryResult(
        fetched=len(all_products),
        scored=len(scores),
        top=top,
        sources=sources,
        errors=errors,
        llm_signals_filled=llm_signals_filled,
        niche_rankings=niche_rankings,
    )
