"""Monetizze scraper.

Adapter for Monetizze's public marketplace. Same shape as the Hotmart adapter
on purpose — adding a new platform should be a closed change. The HTML parser
is separable from the HTTP fetch so tests can pass HTML directly.

V1 scope: structural parser + selectors that match Monetizze's current
marketplace layout. Production should swap this for Monetizze's affiliate
API once credentials are wired in.
"""

from __future__ import annotations

import logging
import re

import httpx
from selectolax.parser import HTMLParser

from ..models import Niche, Platform, Product
from .base import ScraperError, ScraperProtocol
from .hotmart import _classify_niche, _parse_brl  # reuse Brazilian-locale helpers

logger = logging.getLogger(__name__)

_MARKETPLACE_URL = "https://app.monetizze.com.br/mktplace"
_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


def parse_marketplace_html(html: str, *, limit: int = 50) -> list[Product]:
    tree = HTMLParser(html)
    nodes = (
        tree.css('[data-test="product-card"]')
        or tree.css(".produto-card")
        or tree.css("div.card-produto")
    )

    products: list[Product] = []
    for node in nodes[:limit]:
        try:
            name_node = node.css_first("h3, h2, .nome-produto")
            link_node = node.css_first("a")
            if not name_node or not link_node:
                continue
            name = name_node.text(strip=True)
            url = link_node.attributes.get("href") or ""
            if not name or not url:
                continue
            if url.startswith("/"):
                url = f"https://app.monetizze.com.br{url}"

            external_id = url.rstrip("/").split("/")[-1]

            price_node = node.css_first(".preco, .valor")
            price = _parse_brl(price_node.text() if price_node else None)

            commission_node = node.css_first(".comissao")
            commission_pct = _parse_brl(commission_node.text() if commission_node else None)

            category_node = node.css_first(".categoria")
            category = category_node.text(strip=True) if category_node else None

            producer_node = node.css_first(".produtor")
            producer = producer_node.text(strip=True) if producer_node else None

            products.append(
                Product(
                    platform=Platform.MONETIZZE,
                    external_id=external_id,
                    name=name,
                    url=url,
                    category=category,
                    niche=_classify_niche(category),
                    price_brl=price,
                    commission_pct=commission_pct,
                    producer_name=producer,
                    raw={"source": "monetizze_marketplace_html"},
                )
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Skipping malformed Monetizze card: %s", exc)
            continue

    return products


class MonetizzeScraper(ScraperProtocol):
    platform = Platform.MONETIZZE

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
                raise ScraperError(f"Monetizze fetch failed: {exc}") from exc

        products = parse_marketplace_html(resp.text, limit=limit)
        if not products:
            raise ScraperError(
                "Monetizze returned 0 parseable products. Selectors likely drifted."
            )
        return products
