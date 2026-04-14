"""Tests for two related behaviors introduced after the analytics tab:

1. `clear_catalog()` wipes products + scores atomically and leaves links alone.
2. Running discovery with `use_mock=True` must NOT persist rows — mock
   data is in-memory-only, visible in the Top picks tab but never polluting
   the live analytics.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from backend.app.db import LinkRepository, ProductRepository
from backend.app.models import LinkStatus, Platform
from backend.app.scrapers import MockAmazonScraper, MockScraper
from backend.app.services.discovery import run_discovery
from backend.app.services.link_vault import add_link


@pytest.fixture
def tmp_repo(tmp_path: Path) -> ProductRepository:
    return ProductRepository(db_path=tmp_path / "analytics.duckdb")


class TestClearCatalog:
    @pytest.mark.asyncio
    async def test_clear_removes_all_products_and_scores(self, tmp_repo):
        await run_discovery(
            repo=tmp_repo,
            scrapers=[MockScraper()],
            limit_per_source=50,
            top_n=5,
            use_mock=False,  # force persistence so we have rows to delete
        )
        assert len(tmp_repo.top_products(limit=50)) == 8

        deleted = tmp_repo.clear_catalog()
        assert deleted == 8
        assert tmp_repo.top_products(limit=50) == []

    def test_clear_leaves_link_vault_untouched(self, tmp_path: Path):
        shared_db = tmp_path / "shared.duckdb"
        prod_repo = ProductRepository(db_path=shared_db)
        link_repo = LinkRepository(db_path=shared_db)
        add_link(
            link_repo,
            platform=Platform.HOTMART,
            label="Test link",
            raw_url="https://example.com/product/1",
            approval_status=LinkStatus.APPROVED,
        )
        assert len(link_repo.list()) == 1

        prod_repo.clear_catalog()
        # Link vault survives catalog wipe — different concern, different table.
        assert len(link_repo.list()) == 1


class TestMockPersistenceGating:
    @pytest.mark.asyncio
    async def test_use_mock_true_does_not_persist(self, tmp_repo):
        result = await run_discovery(
            repo=tmp_repo,
            limit_per_source=50,
            top_n=5,
            use_mock=True,
            scrapers=[MockScraper(), MockAmazonScraper()],
        )
        # The in-memory result should contain the scored products.
        assert result.fetched == 16
        assert len(result.top) == 5
        # But the DB must be empty.
        assert tmp_repo.top_products(limit=50) == []

    @pytest.mark.asyncio
    async def test_use_mock_false_persists_normally(self, tmp_repo):
        # Passing scrapers explicitly so we don't hit the live Hotmart API
        # from the test suite; use_mock=False is what controls persistence,
        # not scraper selection.
        result = await run_discovery(
            repo=tmp_repo,
            limit_per_source=50,
            top_n=5,
            use_mock=False,
            scrapers=[MockScraper(), MockAmazonScraper()],
        )
        assert result.fetched == 16
        assert len(tmp_repo.top_products(limit=50)) == 16
