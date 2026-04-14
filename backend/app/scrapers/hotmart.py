"""Hotmart marketplace scraper — real data via Next.js hydration blob.

# The breakthrough

The public `https://hotmart.com/pt-br/marketplace` page is a Next.js React SPA.
At first glance it looks unscrapable without Playwright: the HTML shell
has no product cards, just a skeleton + JS bundles that render content
client-side. CSS selectors against the initial HTML return zero cards.

But Next.js ships every page with a **hydration blob** — a JSON document
embedded in a `<script>` tag containing all the server-side data the page
was rendered with. The client uses this to avoid re-fetching on mount.
For `/pt-br/marketplace` that blob includes a `bestSellers` list of 10 real
products, complete with title, category, producer name, rating, review
count, slug, and description. No JavaScript execution required.

This is the right way to scrape any Next.js site:

1. Fetch the HTML with plain httpx
2. Find the `<script>` whose content starts with `{"props"` (the Next.js
   hydration convention)
3. Parse it as JSON
4. Walk to `props.pageProps.bestSellers` (or whatever key the page exposes)
5. Map each product object into your domain model

It's faster, more reliable, and more ethical than launching a headless
browser — you're using the same data the page is already shipping to
every visitor.

# What we can and cannot extract

Visible to unauthenticated visitors on the public marketplace:
- Title, category slug, topic slug
- Producer (ownerName) and producer reference code
- Rating (0–5) and total review count
- Slug (for the public product URL)
- Description (up to ~500 chars of marketing copy)
- Language / locale

**Not** visible — gated behind affiliate login:
- Price in BRL
- Commission percentage
- Gravity / heat / sales-volume metrics

For missing fields the scoring module already degrades gracefully. We
synthesize a `popularity` number from `rating × log(total_reviews + 1)`
so products with real traction outrank zero-review products.
"""

from __future__ import annotations

import json
import logging
import math
import re
from typing import Any

import httpx

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
_SCRIPT_RE = re.compile(r"<script[^>]*>(.*?)</script>", re.DOTALL)


def _parse_brl(text: str | None) -> float | None:
    """Parse a BRL string like 'R$ 1.997,00' into 1997.0. Kept for other scrapers."""
    if not text:
        return None
    m = _PRICE_RE.search(text)
    if not m:
        return None
    return float(m.group(1).replace(".", "").replace(",", "."))


# Map Hotmart's category/topic slugs to our internal Niche taxonomy.
# Both `category` (broad: "education", "finance", "health") and `topic`
# (fine-grained: "investimentos", "marketing-digital", "emagrecimento") show
# up in the hydration blob — we prefer `topic` for precision and fall back
# to `category`.
def _classify_niche(category_or_topic: str | None) -> Niche | None:
    if not category_or_topic:
        return None
    c = category_or_topic.lower()
    if any(k in c for k in ("finan", "invest", "dinheiro", "renda", "bitcoin", "trading")):
        return Niche.FINANCE
    if any(k in c for k in ("marketing", "vendas", "tráfego", "trafego", "afiliad", "trafego-pago")):
        return Niche.DIGITAL_MARKETING
    if any(k in c for k in ("saúde", "saude", "fitness", "emagrec", "dieta", "nutri")):
        return Niche.HEALTH
    if any(k in c for k in ("tecnologia", "saas", "software", "código", "codigo", "programa")):
        return Niche.TECH_SAAS
    if any(k in c for k in ("negócio", "negocio", "empreend", "business")):
        return Niche.BUSINESS
    return Niche.OTHER


def _popularity_from_rating(rating: float | None, total_reviews: int | None) -> float | None:
    """Synthesize a comparable popularity number from rating + review count.

    Hotmart's public blob exposes rating (0–5) and total_reviews but not any
    raw popularity/heat/gravity metric. A product with 2000 reviews at 4.8★
    should outrank one with 20 reviews at 4.9★ because the former has
    actually been tested by buyers. `rating × log1p(reviews)` gives us that:
    review count dominates at scale, rating dominates when review counts
    are small.
    """
    if rating is None or total_reviews is None:
        return None
    try:
        return float(rating) * math.log1p(max(int(total_reviews), 0))
    except (TypeError, ValueError):
        return None


def _extract_hydration_json(html: str) -> dict[str, Any] | None:
    """Find the Next.js hydration `<script>` tag and parse it."""
    for match in _SCRIPT_RE.finditer(html):
        content = match.group(1).lstrip()
        if content.startswith('{"props"'):
            try:
                return json.loads(content)
            except json.JSONDecodeError as exc:
                logger.warning("Hotmart hydration JSON parse failed: %s", exc)
                return None
    return None


def _find_products_in_tree(tree: Any) -> list[dict]:
    """DFS the hydration tree for the first list whose items contain
    `producerReferenceCode`. Tolerates Hotmart reshuffling the blob shape
    (bestSellers → featured → products → ...).
    """
    if isinstance(tree, list):
        if tree and isinstance(tree[0], dict) and "producerReferenceCode" in tree[0]:
            return tree
        for item in tree:
            result = _find_products_in_tree(item)
            if result:
                return result
    elif isinstance(tree, dict):
        for value in tree.values():
            result = _find_products_in_tree(value)
            if result:
                return result
    return []


def _product_from_json(item: dict) -> Product | None:
    try:
        ref = item.get("producerReferenceCode")
        if not ref:
            return None

        title = item.get("title") or item.get("name") or ""
        if not title:
            return None

        slug = item.get("slug") or ""
        url = (
            f"https://www.hotmart.com/product/{slug}" if slug
            else f"https://hotmart.com/pt-br/marketplace/produtos/{ref}"
        )

        topic = item.get("topic")
        category = topic or item.get("category")
        niche = _classify_niche(topic) or _classify_niche(item.get("category"))

        rating = item.get("rating")
        total_reviews = item.get("totalReviews")
        popularity = _popularity_from_rating(rating, total_reviews)

        owner = item.get("owner") or {}
        producer_name = item.get("ownerName") or owner.get("name")

        # Derive a reputation proxy from rating (0–5 → 0–1). Review count
        # is already folded into popularity, so this is purely quality.
        producer_reputation = (float(rating) / 5.0) if isinstance(rating, (int, float)) else None

        return Product(
            platform=Platform.HOTMART,
            external_id=str(ref),
            name=title,
            url=url,
            category=category,
            niche=niche,
            producer_name=producer_name,
            producer_reputation=producer_reputation,
            popularity=popularity,
            # Price and commission are gated behind affiliate login — leave None.
            price_brl=None,
            commission_pct=None,
            raw={
                "source": "hotmart_marketplace_nextdata",
                "description": item.get("description"),
                "rating": rating,
                "total_reviews": total_reviews,
                "topic": topic,
                "hotmart_category": item.get("category"),
                "slug": slug,
            },
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Skipping malformed Hotmart product object: %s", exc)
        return None


def parse_marketplace_html(html: str, *, limit: int = 50) -> list[Product]:
    """Entry point — pure function from HTML to Product list.

    The old version scraped CSS-selected product cards, which never existed
    in the server-rendered HTML. The new version parses Next.js hydration
    JSON, which is the actual data source the React app uses. Same return
    type, same call site, completely different internals.
    """
    tree = _extract_hydration_json(html)
    if tree is None:
        return []

    raw_products = _find_products_in_tree(tree)
    products: list[Product] = []
    for item in raw_products[:limit]:
        product = _product_from_json(item)
        if product is not None:
            products.append(product)
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
                "The Next.js hydration blob shape likely changed — inspect the HTML "
                "and update _find_products_in_tree if needed."
            )
        return products

    @staticmethod
    def from_html(html: str, limit: int = 50) -> list[Product]:
        """Test/REPL helper — parse a saved HTML payload without HTTP."""
        return parse_marketplace_html(html, limit=limit)


__all__ = [
    "HotmartScraper",
    "parse_marketplace_html",
    "_classify_niche",
    "_parse_brl",
    "_popularity_from_rating",
]
