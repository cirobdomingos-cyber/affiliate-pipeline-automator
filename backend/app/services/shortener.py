"""Short-link service — slug generation, UTM composition, click parsing.

Pure helpers only. No FastAPI, no DB. The API route wires these into the
request flow and the repositories in `db.py`.
"""

from __future__ import annotations

import secrets
from urllib.parse import urlencode, urlparse, urlunparse

_SLUG_ALPHABET = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
_SLUG_LENGTH = 8


def generate_slug() -> str:
    """8-char base62 slug. ~218T combinations — collision-free in practice
    for a single operator's link vault."""
    return "".join(secrets.choice(_SLUG_ALPHABET) for _ in range(_SLUG_LENGTH))


def compose_destination(
    *,
    affiliate_url: str,
    utm_source: str | None,
    utm_medium: str | None,
    utm_campaign: str | None,
) -> str:
    """Append UTM params to an affiliate URL without stomping existing query."""
    parts = urlparse(affiliate_url)
    existing = parts.query
    params: list[tuple[str, str]] = []
    if utm_source:
        params.append(("utm_source", utm_source))
    if utm_medium:
        params.append(("utm_medium", utm_medium))
    if utm_campaign:
        params.append(("utm_campaign", utm_campaign))
    if not params:
        return affiliate_url
    new_query = (existing + "&" if existing else "") + urlencode(params)
    return urlunparse(parts._replace(query=new_query))


def anonymize_ip(ip: str | None) -> str | None:
    """Return the first 3 octets of an IPv4 address. IPv6 returns None.

    Brief requirement: 'primeiros 3 octetos'. Dropping the last octet gives
    /24 granularity — enough to count uniques, never enough to fingerprint a
    household.
    """
    if not ip:
        return None
    parts = ip.split(".")
    if len(parts) != 4:
        return None  # IPv6 or malformed
    try:
        for p in parts:
            int(p)
    except ValueError:
        return None
    return ".".join(parts[:3]) + ".0"


def ua_family(user_agent: str | None) -> str | None:
    """Collapse a user-agent string into one of four buckets: mobile-ios,
    mobile-android, desktop, bot. Coarse on purpose — the dashboard doesn't
    need fine-grained browser detection."""
    if not user_agent:
        return None
    ua = user_agent.lower()
    if "bot" in ua or "crawler" in ua or "spider" in ua:
        return "bot"
    if "iphone" in ua or "ipad" in ua or "ios" in ua:
        return "mobile-ios"
    if "android" in ua:
        return "mobile-android"
    return "desktop"
