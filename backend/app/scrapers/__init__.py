from .base import ScraperProtocol, ScraperError
from .hotmart import HotmartScraper
from .mock import MockScraper

__all__ = ["ScraperProtocol", "ScraperError", "HotmartScraper", "MockScraper"]
