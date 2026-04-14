"""Minimal fal.ai HTTP client for image and video generation.

fal.ai is a hosted inference gateway — one API key, one billing contract,
many models. We use it instead of hitting Ideogram / Google Vertex / Runway
directly because each of those requires separate auth, separate billing, and
(for Vertex) a service-account roundtrip we don't want in a single-operator
tool.

fal.ai exposes two endpoint shapes:
- **Sync** (`https://fal.run/{model}`): for fast models (images). Returns
  the final response directly.
- **Queue** (`https://queue.fal.run/{model}`): for long-running models
  (video). Returns a request_id; we poll status and then fetch the result.

We use sync for Ideogram (≤10s) and queue+polling for Veo 3 (30–120s).

Docs that matter:
- https://fal.ai/docs/reference/api-gateway
- https://fal.ai/models/fal-ai/ideogram/v3
- https://fal.ai/models/fal-ai/veo3

Costs are returned on a `timings.billable_time` field on some models but not
all; treat them as best-effort. The UI falls back to a hard-coded per-call
estimate when fal.ai doesn't return billing metadata.
"""

from __future__ import annotations

import os
import time

import httpx

_SYNC_BASE = "https://fal.run"
_QUEUE_BASE = "https://queue.fal.run"


class FalError(RuntimeError):
    pass


def _api_key() -> str:
    key = os.environ.get("FAL_API_KEY") or os.environ.get("FAL_KEY")
    if not key:
        raise FalError(
            "FAL_API_KEY is not set. Add it to .env and restart the app. "
            "Get a key at https://fal.ai/dashboard/keys."
        )
    return key


def _headers() -> dict[str, str]:
    return {
        "Authorization": f"Key {_api_key()}",
        "Content-Type": "application/json",
    }


def run_sync(model: str, payload: dict, *, timeout_seconds: float = 120.0) -> dict:
    """Hit the fal sync endpoint. Use for fast models (images)."""
    url = f"{_SYNC_BASE}/{model}"
    with httpx.Client(timeout=timeout_seconds) as client:
        resp = client.post(url, headers=_headers(), json=payload)
    if resp.status_code >= 400:
        raise FalError(f"fal {resp.status_code}: {resp.text[:300]}")
    return resp.json()


def run_queued(
    model: str,
    payload: dict,
    *,
    poll_interval_s: float = 2.0,
    max_wait_s: float = 300.0,
) -> dict:
    """Submit to the fal queue and poll until completion. Use for video.

    Steps:
    1. POST to queue endpoint, receive `status_url` and `response_url`.
    2. Poll `status_url` every `poll_interval_s` until status == "COMPLETED".
    3. GET `response_url` to fetch the final payload.

    Raises `FalError` on timeout, non-2xx, or final status of FAILED.
    """
    submit_url = f"{_QUEUE_BASE}/{model}"
    with httpx.Client(timeout=30.0) as client:
        resp = client.post(submit_url, headers=_headers(), json=payload)
        if resp.status_code >= 400:
            raise FalError(f"fal submit {resp.status_code}: {resp.text[:300]}")
        submit = resp.json()
        status_url = submit.get("status_url")
        response_url = submit.get("response_url")
        if not status_url or not response_url:
            raise FalError(f"fal queue response missing URLs: {submit}")

        deadline = time.monotonic() + max_wait_s
        while True:
            if time.monotonic() > deadline:
                raise FalError(f"fal queue wait exceeded {max_wait_s}s")
            sresp = client.get(status_url, headers=_headers())
            if sresp.status_code >= 400:
                raise FalError(f"fal status {sresp.status_code}: {sresp.text[:300]}")
            status = sresp.json().get("status")
            if status == "COMPLETED":
                break
            if status in {"FAILED", "ERROR"}:
                raise FalError(f"fal job failed: {sresp.json()}")
            time.sleep(poll_interval_s)

        fresp = client.get(response_url, headers=_headers())
        if fresp.status_code >= 400:
            raise FalError(f"fal fetch {fresp.status_code}: {fresp.text[:300]}")
        return fresp.json()
