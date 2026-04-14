"""Integration test for the discovery service using MockScraper + DuckDB."""

from __future__ import annotations

from pathlib import Path

import pytest

from backend.app.db import ProductRepository
from backend.app.scrapers import MockScraper
from backend.app.services.discovery import run_discovery


@pytest.mark.asyncio
async def test_run_discovery_with_mock_scraper(tmp_path: Path):
    repo = ProductRepository(db_path=tmp_path / "test.duckdb")
    result = await run_discovery(
        repo=repo,
        limit_per_source=50,
        top_n=5,
        scrapers=[MockScraper()],
    )

    assert result.fetched > 0
    assert result.scored == result.fetched
    assert len(result.top) == 5
    # Top result should have the highest score.
    scores = [sp.score.score for sp in result.top]
    assert scores == sorted(scores, reverse=True)

    persisted = repo.top_products(limit=5)
    assert len(persisted) == 5
    assert persisted[0].score.score == result.top[0].score.score
