"""Discovery service — orchestrates scrapers, scoring, and persistence.

This is where the ports come together. The API and UI both call
`run_discovery`; nothing else in the codebase needs to know which platforms
are wired in or how scoring works.
"""

from __future__ import annotations

import asyncio
import logging

from pydantic import BaseModel

from ..db import ProductRepository
from ..models import Product, ScoredProduct
from ..scoring import DEFAULT_WEIGHTS, ScoringWeights, rank_products
from ..scrapers import HotmartScraper, MockScraper, ScraperProtocol

logger = logging.getLogger(__name__)


class DiscoveryResult(BaseModel):
    fetched: int
    scored: int
    top: list[ScoredProduct]
    sources: list[str]
    errors: list[str]


def _default_scrapers(use_mock: bool) -> list[ScraperProtocol]:
    if use_mock:
        return [MockScraper()]
    return [HotmartScraper()]


async def run_discovery(
    *,
    repo: ProductRepository,
    limit_per_source: int = 50,
    top_n: int = 25,
    weights: ScoringWeights = DEFAULT_WEIGHTS,
    use_mock: bool = False,
    scrapers: list[ScraperProtocol] | None = None,
) -> DiscoveryResult:
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

    repo.upsert_products(all_products)

    ranked = rank_products(all_products, weights=weights)
    scores = [score for _, score in ranked]
    repo.replace_scores(scores)

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
    )
