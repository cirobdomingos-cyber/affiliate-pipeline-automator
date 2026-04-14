"""Minimal Replicate HTTP client — image + video generation.

Replicate is the fallback for fal.ai when fal's billing rejects Brazilian tax
IDs. Same value proposition (one key, many models), different reliability
profile on international billing.

API shape:
- `POST https://api.replicate.com/v1/models/{owner}/{name}/predictions` with
  `{"input": {...}}`. Returns a prediction with `urls.get` to poll.
- Poll `urls.get` until `status == "succeeded"`. The payload then contains
  `output` — a URL (video) or list of URLs (images) depending on the model.
- `FAILED`/`CANCELED` surfaces the error.

Docs: https://replicate.com/docs/reference/http
"""

from __future__ import annotations

import os
import time

import httpx

_BASE_URL = "https://api.replicate.com/v1"


class ReplicateError(RuntimeError):
    pass


def _api_key() -> str:
    key = os.environ.get("REPLICATE_API_TOKEN") or os.environ.get("REPLICATE_API_KEY")
    if not key:
        raise ReplicateError(
            "REPLICATE_API_TOKEN is not set. Add it to .env and restart the app. "
            "Get a token at https://replicate.com/account/api-tokens."
        )
    return key


def _headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {_api_key()}",
        "Content-Type": "application/json",
        "Prefer": "wait=60",  # let Replicate hold the request up to 60s before polling kicks in
    }


def run(
    model: str,
    input_payload: dict,
    *,
    poll_interval_s: float = 3.0,
    max_wait_s: float = 360.0,
) -> dict:
    """Submit a prediction and block until it's done.

    `model` is a fully-qualified slug like `black-forest-labs/flux-1.1-pro`
    or `luma/ray` — owner/name format, no version hash required for official
    models. For community models with versions, pass the owner/name and
    Replicate will use the latest public version.
    """
    url = f"{_BASE_URL}/models/{model}/predictions"
    with httpx.Client(timeout=90.0) as client:
        resp = client.post(url, headers=_headers(), json={"input": input_payload})
        if resp.status_code >= 400:
            raise ReplicateError(f"replicate submit {resp.status_code}: {resp.text[:300]}")
        prediction = resp.json()

        # `Prefer: wait` sometimes returns a finished prediction immediately.
        if prediction.get("status") == "succeeded":
            return prediction

        get_url = (prediction.get("urls") or {}).get("get")
        if not get_url:
            raise ReplicateError(f"replicate response missing urls.get: {prediction}")

        deadline = time.monotonic() + max_wait_s
        while True:
            if time.monotonic() > deadline:
                raise ReplicateError(f"replicate prediction exceeded {max_wait_s}s")
            pr = client.get(get_url, headers=_headers())
            if pr.status_code >= 400:
                raise ReplicateError(f"replicate poll {pr.status_code}: {pr.text[:300]}")
            data = pr.json()
            status = data.get("status")
            if status == "succeeded":
                return data
            if status in {"failed", "canceled"}:
                raise ReplicateError(
                    f"replicate prediction {status}: {data.get('error') or data}"
                )
            time.sleep(poll_interval_s)
