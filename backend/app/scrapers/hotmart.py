"""Hotmart marketplace scraper.

Honest scope note: Hotmart's public marketplace is server-rendered HTML on
some surfaces and JS-hydrated on others, and the exact selectors drift.
This adapter is structured so that:

1. The HTTP fetch and the HTML parse are separable. Tests can pass HTML
   directly to `parse_marketplace_html` without touching the network.
2. If selectors break, only `parse_marketplace_html` needs to change — the
   rest of the pipeline (scoring, persistence, UI) is untouched.
3. For the MVP demo path, operators can use `MockScraper` to populate the
   pipeline with realistic fixture data while Hotmart selectors are tuned.

V1 will replace selector-based parsing with Hotmart's official affiliate
API once credentials are wired in.
"""

from __future__ import annotations

import logging
import re
from typing import Any

import httpx
from selectolax.parser import HTMLParser

from ..models import Niche, Platform, Product
from .base import ScraperError, ScraperProtocol

logger = logging.getLogger(__name__)

_MARKETPLACE_URL = "https://hotmart.com/pt-br/marketplace"
_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)
_PRICE_RE = re.compile(r"R\$\s*([\d\.]+,\d{2})")


def _parse_brl(text: str | None) -> float | None:
    if not text:
        return None
    m = _PRICE_RE.search(text)
    if not m:
        return None
    return float(m.group(1).replace(".", "").replace(",", "."))


def _classify_niche(category: str | None) -> Niche | None:
    if not category:
        return None
    c = category.lower()
    if any(k in c for k in ("finan", "invest", "dinheiro", "renda")):
        return Niche.FINANCE
    if any(k in c for k in ("marketing", "vendas", "tráfego", "trafego")):
        return Niche.DIGITAL_MARKETING
    if any(k in c for k in ("saúde", "saude", "fitness", "emagrec", "dieta")):
        return Niche.HEALTH
    if any(k in c for k in ("tecnologia", "saas", "software", "código", "codigo")):
        return Niche.TECH_SAAS
    if any(k in c for k in ("negócio", "negocio", "empreend", "business")):
        return Niche.BUSINESS
    return Niche.OTHER


def parse_marketplace_html(html: str, *, limit: int = 50) -> list[Product]:
    """Parse a Hotmart marketplace HTML payload into Product objects.

    Tolerant by design: any card that doesn't yield a name+url is skipped
    rather than failing the whole batch. A platform-rendered page should
    yield dozens of cards; if it yields zero, the caller should treat that
    as a selector regression and switch to MockScraper for the demo while
    the selectors are repaired.
    """
    tree = HTMLParser(html)
    products: list[Product] = []

    # Hotmart cards historically use [data-test="product-card"] or
    # article.product-card. We try a small ladder of selectors.
    nodes = (
        tree.css('[data-test="product-card"]')
        or tree.css("article.product-card")
        or tree.css("a.product-card")
    )

    for node in nodes[:limit]:
        try:
            name_node = node.css_first("h3, h2, .product-name")
            link_node = node.css_first("a")
            if not name_node or not link_node:
                continue
            name = name_node.text(strip=True)
            url = link_node.attributes.get("href") or ""
            if not name or not url:
                continue
            if url.startswith("/"):
                url = f"https://hotmart.com{url}"

            external_id = url.rstrip("/").split("/")[-1]

            price_node = node.css_first(".price, [data-test='price']")
            price = _parse_brl(price_node.text() if price_node else None)

            commission_node = node.css_first(".commission, [data-test='commission']")
            commission_pct = _parse_brl(commission_node.text() if commission_node else None)

            popularity_node = node.css_first(".heat, [data-test='heat']")
            popularity_text = popularity_node.text(strip=True) if popularity_node else None
            popularity = float(popularity_text) if popularity_text and popularity_text.replace(".", "").isdigit() else None

            category_node = node.css_first(".category, [data-test='category']")
            category = category_node.text(strip=True) if category_node else None

            producer_node = node.css_first(".producer, [data-test='producer']")
            producer = producer_node.text(strip=True) if producer_node else None

            products.append(
                Product(
                    platform=Platform.HOTMART,
                    external_id=external_id,
                    name=name,
                    url=url,
                    category=category,
                    niche=_classify_niche(category),
                    price_brl=price,
                    commission_pct=commission_pct,
                    popularity=popularity,
                    producer_name=producer,
                    raw={"source": "hotmart_marketplace_html"},
                )
            )
        except Exception as exc:  # noqa: BLE001 — tolerant per-card
            logger.warning("Skipping malformed Hotmart card: %s", exc)
            continue

    return products


class HotmartScraper(ScraperProtocol):
    platform = Platform.HOTMART

    def __init__(self, *, timeout: float = 15.0) -> None:
        self._timeout = timeout

    async def fetch(self, *, limit: int = 50) -> list[Product]:
        async with httpx.AsyncClient(
            timeout=self._timeout,
            headers={"User-Agent": _USER_AGENT, "Accept-Language": "pt-BR,pt;q=0.9"},
            follow_redirects=True,
        ) as client:
            try:
                resp = await client.get(_MARKETPLACE_URL)
                resp.raise_for_status()
            except httpx.HTTPError as exc:
                raise ScraperError(f"Hotmart fetch failed: {exc}") from exc

        products = parse_marketplace_html(resp.text, limit=limit)
        if not products:
            raise ScraperError(
                "Hotmart marketplace returned 0 parseable products. "
                "Selectors likely drifted — fall back to MockScraper while repairing."
            )
        return products

    @staticmethod
    def from_html(html: str, limit: int = 50) -> list[Product]:
        """Test/REPL helper — parse a saved HTML payload without HTTP."""
        return parse_marketplace_html(html, limit=limit)


__all__ = ["HotmartScraper", "parse_marketplace_html"]
