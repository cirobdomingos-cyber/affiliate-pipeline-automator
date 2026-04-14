"""Scraper port — every platform adapter implements this Protocol.

Adding a new platform is a closed change: write a class that satisfies
ScraperProtocol, register it in `services/discovery.py`, and the rest of the
pipeline (scoring, persistence, API, UI) does not need to know it exists.
This is the ports-and-adapters boundary that lets the MVP scale to V1
without rewrites.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from ..models import Platform, Product


class ScraperError(Exception):
    """Raised when a scraper cannot fetch or parse its source."""


@runtime_checkable
class ScraperProtocol(Protocol):
    platform: Platform

    async def fetch(self, *, limit: int = 50) -> list[Product]:
        """Return up to `limit` products from this platform's catalog."""
        ...
