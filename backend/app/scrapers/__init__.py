from .base import ScraperProtocol, ScraperError
from .eduzz import EduzzScraper
from .hotmart import HotmartScraper
from .mock import MockScraper
from .monetizze import MonetizzeScraper

__all__ = [
    "ScraperProtocol",
    "ScraperError",
    "HotmartScraper",
    "MonetizzeScraper",
    "EduzzScraper",
    "MockScraper",
]
