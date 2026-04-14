"""UTM builder — pure functions, no I/O.

Composes a tracked URL from a base affiliate link and a set of UTM params.
Kept isolated from the repository and API layers so the full test matrix
(slug normalization, query-string preservation, fragment handling, URL
encoding) runs in milliseconds against a few dozen literals.

Design rules:
- Normalize UTM values (lowercase, spaces → hyphens) so "Instagram Bio" and
  "instagram-bio" land in the same analytics bucket. This is the #1 data
  hygiene win — mis-tagged traffic is indistinguishable from untagged
  traffic in GA4, and the fix belongs at write-time, not at analysis-time.
- Preserve any query params already present on `raw_url` (producers
  sometimes embed an affiliate ID as a query string).
- Deterministic param ordering: existing params first in their original
  order, then utm_* in a fixed sequence. Deterministic output means two
  identical inputs always produce the same URL, which matters for cache
  keys and regression tests.
- Never mutate the input URL's fragment.
"""

from __future__ import annotations

import re
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from .models import AffiliateLink, TrackedLink, UTMParams

_SLUG_RE = re.compile(r"[\s_]+")
_UTM_ORDER = ("utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content")


def normalize_utm_value(value: str) -> str:
    """Lowercase, strip, and collapse whitespace/underscores to single hyphens.

    'Instagram Bio' → 'instagram-bio'
    '  Paid_Search  ' → 'paid-search'
    'CPC' → 'cpc'
    """
    cleaned = _SLUG_RE.sub("-", value.strip().lower())
    # Collapse multiple consecutive hyphens.
    return re.sub(r"-+", "-", cleaned).strip("-")


def _utm_to_dict(utm: UTMParams) -> dict[str, str]:
    out = {
        "utm_source": normalize_utm_value(utm.source),
        "utm_medium": normalize_utm_value(utm.medium),
        "utm_campaign": normalize_utm_value(utm.campaign),
    }
    if utm.term:
        out["utm_term"] = normalize_utm_value(utm.term)
    if utm.content:
        out["utm_content"] = normalize_utm_value(utm.content)
    return out


def build_tracked_url(base_url: str, utm: UTMParams) -> str:
    """Compose a base URL and UTM params into a final tracked URL.

    Rules:
    - Existing query params are kept, in their original order.
    - If an existing param collides with a utm_* key, the UTM value wins
      (the operator is retagging on purpose).
    - utm_* params always appear in `_UTM_ORDER`, after existing params.
    - The fragment (#anchor) is preserved untouched.
    """
    parsed = urlparse(base_url)
    existing_params = parse_qsl(parsed.query, keep_blank_values=False)

    utm_dict = _utm_to_dict(utm)
    utm_keys = set(utm_dict.keys())

    # Drop any existing params that collide with UTM keys (UTM wins).
    filtered_existing = [(k, v) for k, v in existing_params if k not in utm_keys]

    # UTM params in fixed order so the output is deterministic.
    utm_pairs = [(k, utm_dict[k]) for k in _UTM_ORDER if k in utm_dict]

    new_query = urlencode(filtered_existing + utm_pairs, doseq=False)

    return urlunparse(
        (
            parsed.scheme,
            parsed.netloc,
            parsed.path,
            parsed.params,
            new_query,
            parsed.fragment,
        )
    )


def track(link: AffiliateLink, utm: UTMParams) -> TrackedLink:
    """Convenience wrapper that returns a TrackedLink model."""
    return TrackedLink(
        affiliate_link_id=link.id,
        final_url=build_tracked_url(link.raw_url, utm),
        utm=utm,
    )
