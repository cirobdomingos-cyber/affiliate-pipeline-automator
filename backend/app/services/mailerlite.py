"""MailerLite API v2 client — thin wrapper, no retries, no async.

Only the endpoints Stage 5 actually uses:
- `create_subscriber(email, group_id, name=None)` — POST /api/subscribers +
  assignment to the group, which is what the embed form calls on opt-in.
- `group_subscriber_count(group_id)` — GET /api/groups/{id}, returns the
  `active` count that powers the "inscritos por produto" snapshot.

The api key is read lazily (not at import) so the module can be imported in
tests without MAILERLITE_API_KEY set. A missing key raises a clear error at
call time, not at import time.

MailerLite v2 base URL is https://connect.mailerlite.com/api. If a future
migration to v3 is needed, only this module changes.
"""

from __future__ import annotations

import os

import httpx

_BASE_URL = "https://connect.mailerlite.com/api"


class MailerLiteError(RuntimeError):
    pass


def _api_key() -> str:
    key = os.environ.get("MAILERLITE_API_KEY")
    if not key:
        raise MailerLiteError(
            "MAILERLITE_API_KEY is not set. Add it to .env and restart the app."
        )
    return key


def _headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {_api_key()}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }


def create_subscriber(
    *,
    email: str,
    group_id: str,
    name: str | None = None,
    timeout_seconds: float = 10.0,
) -> dict:
    """Upsert a subscriber and assign them to the given group in one call."""
    payload: dict = {"email": email, "groups": [group_id]}
    if name:
        payload["fields"] = {"name": name}
    with httpx.Client(timeout=timeout_seconds) as client:
        resp = client.post(
            f"{_BASE_URL}/subscribers", headers=_headers(), json=payload
        )
    if resp.status_code >= 400:
        raise MailerLiteError(
            f"MailerLite {resp.status_code}: {resp.text[:200]}"
        )
    return resp.json()


def group_subscriber_count(group_id: str, *, timeout_seconds: float = 10.0) -> int:
    """Return the count of active subscribers in a group."""
    with httpx.Client(timeout=timeout_seconds) as client:
        resp = client.get(f"{_BASE_URL}/groups/{group_id}", headers=_headers())
    if resp.status_code >= 400:
        raise MailerLiteError(
            f"MailerLite {resp.status_code}: {resp.text[:200]}"
        )
    data = resp.json().get("data", {})
    return int(data.get("active_count", 0))
