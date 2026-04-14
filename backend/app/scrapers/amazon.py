"""Amazon Product Advertising API 5.0 scraper (Brazilian marketplace).

Unlike the Hotmart / Monetizze / Eduzz scrapers, this one hits an **official
affiliate API** — no HTML parsing, no selector drift, no Playwright. The
trade-off is that every request is signed with AWS SigV4 and gated behind
Amazon Associates credentials.

# Why hand-rolled SigV4 instead of a wrapper library

Two reasons. First, the community PA-API SDKs (`python-amazon-paapi`,
`paapi5-python-sdk`) are in varying states of repair and add ~15 transitive
dependencies for a job that is ~60 lines of stdlib + hmac. Second — and
this is the portfolio angle — writing SigV4 from scratch proves you
understand what AWS auth actually is, which is a question that comes up in
interviews for any role that touches S3, Lambda, DynamoDB, or any other
AWS service. The signing algorithm here is the same one every AWS SDK
implements internally.

# Credential handling

Reads credentials lazily from environment variables so tests and the mock
path never need them:

- AMAZON_ACCESS_KEY
- AMAZON_SECRET_KEY
- AMAZON_PARTNER_TAG    (e.g. "yourname-20" — your Associates tag)
- AMAZON_HOST           (defaults to webservices.amazon.com.br)
- AMAZON_REGION         (defaults to us-east-1 — PA-API BR uses us-east-1)
- AMAZON_MARKETPLACE    (defaults to www.amazon.com.br)
- AMAZON_SEARCH_KEYWORDS (default search query — defaults to 'livros mais vendidos')

If AMAZON_ACCESS_KEY is not set, the discovery service skips this scraper
entirely. No errors, no crashes.

# Commission rates

Amazon Associates rates vary by category and are NOT returned in the API
response. We map PA-API `ItemInfo.Classifications.ProductGroup` to the
Brazilian rate table (https://afiliados.amazon.com.br/help/operating/agreement).
Rates as of early 2026 — update if Amazon revises the schedule.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import json
import logging
import os
from typing import Any

import httpx

from ..models import Niche, Platform, Product
from .base import ScraperError, ScraperProtocol

logger = logging.getLogger(__name__)


# Rate table for amazon.com.br Associates program.
# Source: https://afiliados.amazon.com.br/help/operating/agreement
# PA-API returns ProductGroup in English even for BR marketplace — we key on
# that. Unknown groups fall back to the 4% catch-all.
AMAZON_COMMISSION_RATES: dict[str, float] = {
    "Book": 10.0,
    "eBooks": 10.0,
    "Digital Ebook Purchas": 10.0,
    "Apparel": 8.0,
    "Shoes": 8.0,
    "Luggage": 8.0,
    "Beauty": 8.0,
    "HealthPersonalCare": 8.0,
    "Baby Product": 6.0,
    "Home": 6.0,
    "Kitchen": 6.0,
    "Furniture": 6.0,
    "Sports": 6.0,
    "Lawn & Patio": 6.0,
    "Toy": 6.0,
    "Office Product": 6.0,
    "Electronics": 2.5,
    "Personal Computer": 2.5,
    "Video Games": 2.5,
    "Software": 2.5,
    "Wireless": 2.5,
}
_DEFAULT_COMMISSION_RATE = 4.0


def commission_rate_for(product_group: str | None) -> float:
    if not product_group:
        return _DEFAULT_COMMISSION_RATE
    return AMAZON_COMMISSION_RATES.get(product_group, _DEFAULT_COMMISSION_RATE)


# -------------------- SigV4 signer --------------------
#
# Implements AWS Signature Version 4 for the ProductAdvertisingAPI service.
# The algorithm is:
#   1. Build a canonical request (method, path, query, headers, body hash)
#   2. Build a "string to sign" from the canonical request + timestamp + scope
#   3. Derive a per-day signing key via four nested HMACs
#   4. Sign the string-to-sign with that key
#   5. Emit an Authorization header naming the credential, headers, signature
#
# The caller supplies `now` for testability — tests can freeze time to
# verify a known canonical request produces a known signature.


_ALGORITHM = "AWS4-HMAC-SHA256"
_SERVICE = "ProductAdvertisingAPI"


def _hmac_sha256(key: bytes, msg: str) -> bytes:
    return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest()


def _sha256_hex(data: str | bytes) -> str:
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def _signing_key(secret: str, date_stamp: str, region: str, service: str) -> bytes:
    k_date = _hmac_sha256(f"AWS4{secret}".encode("utf-8"), date_stamp)
    k_region = _hmac_sha256(k_date, region)
    k_service = _hmac_sha256(k_region, service)
    k_signing = _hmac_sha256(k_service, "aws4_request")
    return k_signing


def sign_paapi_request(
    *,
    access_key: str,
    secret_key: str,
    host: str,
    region: str,
    target: str,
    body: str,
    now: dt.datetime,
) -> dict[str, str]:
    """Return the set of headers (including Authorization) for a PA-API call.

    `target` is the fully-qualified PA-API operation, e.g.
    "com.amazon.paapi5.v1.ProductAdvertisingAPIv1.SearchItems".
    `body` is the JSON request body already serialized to a string.
    `now` must be UTC.
    """
    amz_date = now.strftime("%Y%m%dT%H%M%SZ")
    date_stamp = now.strftime("%Y%m%d")

    content_type = "application/json; charset=utf-8"
    content_encoding = "amz-1.0"

    canonical_uri = "/paapi5/searchitems"  # PA-API 5 SearchItems path
    canonical_querystring = ""
    canonical_headers = (
        f"content-encoding:{content_encoding}\n"
        f"content-type:{content_type}\n"
        f"host:{host}\n"
        f"x-amz-date:{amz_date}\n"
        f"x-amz-target:{target}\n"
    )
    signed_headers = "content-encoding;content-type;host;x-amz-date;x-amz-target"
    payload_hash = _sha256_hex(body)

    canonical_request = (
        f"POST\n{canonical_uri}\n{canonical_querystring}\n"
        f"{canonical_headers}\n{signed_headers}\n{payload_hash}"
    )

    credential_scope = f"{date_stamp}/{region}/{_SERVICE}/aws4_request"
    string_to_sign = (
        f"{_ALGORITHM}\n{amz_date}\n{credential_scope}\n{_sha256_hex(canonical_request)}"
    )

    signing_key = _signing_key(secret_key, date_stamp, region, _SERVICE)
    signature = hmac.new(signing_key, string_to_sign.encode("utf-8"), hashlib.sha256).hexdigest()

    authorization = (
        f"{_ALGORITHM} Credential={access_key}/{credential_scope}, "
        f"SignedHeaders={signed_headers}, Signature={signature}"
    )

    return {
        "Host": host,
        "Content-Type": content_type,
        "Content-Encoding": content_encoding,
        "X-Amz-Target": target,
        "X-Amz-Date": amz_date,
        "Authorization": authorization,
    }


# -------------------- Response parser --------------------
#
# Pure function: (PA-API JSON response, platform-specific limit) → list[Product].
# No network, no signing, no environment lookups. Testable against a captured
# fixture in milliseconds.


def _classify_niche_from_group(product_group: str | None) -> Niche | None:
    if not product_group:
        return None
    g = product_group.lower()
    if g in ("book", "ebooks", "digital ebook purchas"):
        return Niche.BUSINESS  # most Brazilian affiliate books are business/finance
    if "beauty" in g or "health" in g:
        return Niche.HEALTH
    if any(k in g for k in ("electronic", "computer", "software", "video game", "wireless")):
        return Niche.TECH_SAAS
    return Niche.OTHER


def parse_search_response(payload: dict[str, Any], *, limit: int = 50) -> list[Product]:
    """Convert a PA-API `SearchItems` response into our Product model."""
    search_result = payload.get("SearchResult") or {}
    items = search_result.get("Items") or []

    products: list[Product] = []
    for item in items[:limit]:
        try:
            asin = item.get("ASIN")
            if not asin:
                continue

            info = item.get("ItemInfo") or {}
            title = (info.get("Title") or {}).get("DisplayValue") or ""
            if not title:
                continue

            byline = info.get("ByLineInfo") or {}
            brand = (byline.get("Brand") or {}).get("DisplayValue")
            manufacturer = (byline.get("Manufacturer") or {}).get("DisplayValue")
            producer_name = brand or manufacturer

            classifications = info.get("Classifications") or {}
            product_group = (classifications.get("ProductGroup") or {}).get("DisplayValue")

            price_brl: float | None = None
            offers = item.get("Offers") or {}
            listings = offers.get("Listings") or []
            if listings:
                price_block = listings[0].get("Price") or {}
                raw_amount = price_block.get("Amount")
                if isinstance(raw_amount, (int, float)):
                    price_brl = float(raw_amount)

            commission_pct = commission_rate_for(product_group)
            detail_url = item.get("DetailPageURL") or f"https://www.amazon.com.br/dp/{asin}"

            products.append(
                Product(
                    platform=Platform.AMAZON,
                    external_id=asin,
                    name=title,
                    url=detail_url,
                    category=product_group,
                    niche=_classify_niche_from_group(product_group),
                    price_brl=price_brl,
                    commission_pct=commission_pct,
                    producer_name=producer_name,
                    raw={"source": "amazon_paapi_search_items", "asin": asin},
                )
            )
        except Exception as exc:  # noqa: BLE001 — per-item tolerance
            logger.warning("Skipping malformed Amazon item: %s", exc)
            continue

    return products


# -------------------- Scraper adapter --------------------


class AmazonScraper(ScraperProtocol):
    platform = Platform.AMAZON

    _DEFAULT_HOST = "webservices.amazon.com.br"
    _DEFAULT_REGION = "us-east-1"
    _DEFAULT_MARKETPLACE = "www.amazon.com.br"
    _DEFAULT_KEYWORDS = "livros mais vendidos"
    _SEARCH_TARGET = "com.amazon.paapi5.v1.ProductAdvertisingAPIv1.SearchItems"
    _SEARCH_URL_TEMPLATE = "https://{host}/paapi5/searchitems"

    def __init__(
        self,
        *,
        access_key: str | None = None,
        secret_key: str | None = None,
        partner_tag: str | None = None,
        host: str | None = None,
        region: str | None = None,
        marketplace: str | None = None,
        keywords: str | None = None,
        timeout: float = 15.0,
    ) -> None:
        self._access_key = access_key or os.getenv("AMAZON_ACCESS_KEY", "")
        self._secret_key = secret_key or os.getenv("AMAZON_SECRET_KEY", "")
        self._partner_tag = partner_tag or os.getenv("AMAZON_PARTNER_TAG", "")
        self._host = host or os.getenv("AMAZON_HOST", self._DEFAULT_HOST)
        self._region = region or os.getenv("AMAZON_REGION", self._DEFAULT_REGION)
        self._marketplace = marketplace or os.getenv("AMAZON_MARKETPLACE", self._DEFAULT_MARKETPLACE)
        self._keywords = keywords or os.getenv("AMAZON_SEARCH_KEYWORDS", self._DEFAULT_KEYWORDS)
        self._timeout = timeout

    @property
    def credentials_configured(self) -> bool:
        return bool(self._access_key and self._secret_key and self._partner_tag)

    async def fetch(self, *, limit: int = 50) -> list[Product]:
        if not self.credentials_configured:
            raise ScraperError(
                "Amazon PA-API credentials missing. Set AMAZON_ACCESS_KEY, "
                "AMAZON_SECRET_KEY, and AMAZON_PARTNER_TAG to enable live fetching."
            )

        body_dict = {
            "Keywords": self._keywords,
            "PartnerTag": self._partner_tag,
            "PartnerType": "Associates",
            "Marketplace": self._marketplace,
            "ItemCount": min(limit, 10),  # PA-API returns up to 10 per SearchItems call
            "Resources": [
                "ItemInfo.Title",
                "ItemInfo.ByLineInfo",
                "ItemInfo.Classifications",
                "Offers.Listings.Price",
            ],
        }
        body = json.dumps(body_dict, separators=(",", ":"))
        now = dt.datetime.now(dt.timezone.utc)

        headers = sign_paapi_request(
            access_key=self._access_key,
            secret_key=self._secret_key,
            host=self._host,
            region=self._region,
            target=self._SEARCH_TARGET,
            body=body,
            now=now,
        )

        url = self._SEARCH_URL_TEMPLATE.format(host=self._host)

        async with httpx.AsyncClient(timeout=self._timeout) as client:
            try:
                resp = await client.post(url, content=body, headers=headers)
                resp.raise_for_status()
            except httpx.HTTPError as exc:
                raise ScraperError(f"Amazon PA-API request failed: {exc}") from exc

        try:
            payload = resp.json()
        except Exception as exc:  # noqa: BLE001
            raise ScraperError(f"Amazon PA-API returned non-JSON response: {exc}") from exc

        products = parse_search_response(payload, limit=limit)
        if not products:
            errors = payload.get("Errors") or []
            err_summary = "; ".join(
                f"{e.get('Code', '?')}: {e.get('Message', '?')}" for e in errors
            )
            raise ScraperError(
                f"Amazon PA-API returned 0 products. Errors: {err_summary or 'none'}"
            )
        return products


# -------------------- Mock scraper --------------------


class MockAmazonScraper(ScraperProtocol):
    """Offline Amazon scraper backed by a hand-curated fixture.

    Paired with MockScraper (Hotmart) to give the demo two-platform data
    without requiring Associates credentials.

    Tags every product's `raw.source` as `amazon_mock_fixture` (not
    `amazon_paapi_search_items`) so mock rows are distinguishable from
    real live PA-API data after persistence. Without this, the two would
    be indistinguishable in DuckDB and "clean mock data from the catalog"
    would be impossible without wiping real rows too.
    """

    platform = Platform.AMAZON

    async def fetch(self, *, limit: int = 50) -> list[Product]:
        from pathlib import Path

        fixture_path = (
            Path(__file__).resolve().parents[2]
            / "tests"
            / "fixtures"
            / "amazon_search_response.json"
        )
        payload = json.loads(fixture_path.read_text(encoding="utf-8"))
        products = parse_search_response(payload, limit=limit)
        for p in products:
            p.raw["source"] = "amazon_mock_fixture"
        return products
