"""Mock scraper — deterministic fixture data for the demo path.

Used when a real platform scraper has selector drift, when running offline,
or when bootstrapping the pipeline before credentials are wired in. The
fixture intentionally spans niches and price points so the scoring and
ranking behavior is visible in the UI.
"""

from __future__ import annotations

import json
from importlib import resources
from pathlib import Path

from ..models import Niche, Platform, Product
from .base import ScraperProtocol


def _load_fixture() -> list[dict]:
    fixture_path = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "hotmart_sample.json"
    return json.loads(fixture_path.read_text(encoding="utf-8"))


class MockScraper(ScraperProtocol):
    platform = Platform.HOTMART

    async def fetch(self, *, limit: int = 50) -> list[Product]:
        rows = _load_fixture()[:limit]
        products: list[Product] = []
        for row in rows:
            products.append(
                Product(
                    platform=Platform(row["platform"]),
                    external_id=row["external_id"],
                    name=row["name"],
                    url=row["url"],
                    category=row.get("category"),
                    niche=Niche(row["niche"]) if row.get("niche") else None,
                    price_brl=row.get("price_brl"),
                    commission_pct=row.get("commission_pct"),
                    popularity=row.get("popularity"),
                    producer_name=row.get("producer_name"),
                    producer_reputation=row.get("producer_reputation"),
                    sales_page_signals=row.get("sales_page_signals"),
                    raw={"source": "mock_fixture"},
                )
            )
        return products
