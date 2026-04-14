"""Creative generation service layer.

Today this module exposes a single Protocol and one pass-through implementation.
The point of having it exist NOW — before we actually call any external image
or video API — is to establish the seam so the Phase 3 UI talks to
`CreativeGenerator` rather than to `traffic_planner.generate_creative_briefs`
directly. When we later add real image/video generation, we drop in a new
implementation without touching the UI.

## Roadmap for direct generation (not implemented yet)

The intended upgrade path:

1. **IdeogramGenerator** — calls fal.ai's Ideogram v3 endpoint. Takes
   `CreativeBrief.image_prompt` + `aspect_ratio`, returns a signed image URL.
   fal.ai is chosen over the direct Ideogram API because it's cheaper for
   single-operator volume (~R$0.15/image at the time of writing), supports
   pay-as-you-go without a subscription, and gives us one billing contract
   for multiple image models.

2. **Veo3Generator** — calls Google Vertex AI's Veo 3 endpoint with
   `CreativeBrief.video_prompt` + `video_duration_s`. Vertex requires a GCP
   service account; we mount it via `GOOGLE_APPLICATION_CREDENTIALS`.

3. **RunwayGen4Generator** — fallback video path when Veo is rate-limited or
   the prompt contains human subjects Veo refuses. Runway's API is simpler
   (bearer token) and cheaper per clip but lower fidelity.

Both direct-generation paths should persist the output alongside the brief
(probably a new `generated_creatives` table with the blob URL + cost in BRL
so Phase 6 KPIs can aggregate creative spend).

Until those land, the UI calls `PromptOnlyGenerator` which just returns the
brief — the operator copy/pastes into Ideogram / Veo manually.
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone
from typing import Protocol

from ..models import AdCopyVariant, CreativeBrief, GeneratedCreative, Product
from ..traffic_planner import TrafficPlanner
from . import fal, replicate


class CreativeGenerator(Protocol):
    """Everything Phase 3 UI needs to produce creatives for a set of ads.

    A "generator" might return ready-to-paste prompts (today), a list of
    signed image URLs (tomorrow with Ideogram), or a mix (video still prompt-
    only, images live). The Protocol stays narrow so UI changes are avoided.
    """

    def briefs(
        self,
        *,
        product: Product,
        target_audience: str,
        variants: list[AdCopyVariant],
    ) -> list[CreativeBrief]:  # pragma: no cover - protocol stub
        ...


class PromptOnlyGenerator:
    """Current implementation: forward to `TrafficPlanner.generate_creative_briefs`.

    No external API calls, no image or video generation. The operator pastes
    the returned prompts into whatever external tool they already use.
    """

    def __init__(self, planner: TrafficPlanner) -> None:
        self._planner = planner

    def briefs(
        self,
        *,
        product: Product,
        target_audience: str,
        variants: list[AdCopyVariant],
    ) -> list[CreativeBrief]:
        return self._planner.generate_creative_briefs(
            product=product,
            target_audience=target_audience,
            variants=variants,
        )


_DEFAULT_IMAGE_MODEL = "fal-ai/ideogram/v3"
_DEFAULT_VIDEO_MODEL = "fal-ai/veo3"

# Best-effort per-call cost estimates in BRL. fal.ai pricing changes; we use
# these only as a fallback when the response omits billing metadata.
_IMAGE_FALLBACK_COST_BRL = 0.40
_VIDEO_FALLBACK_COST_BRL = 15.00  # Veo 3 default; actual varies by model


class FalAIGenerator:
    """Direct image/video generation via fal.ai.

    The class is *compositional*: text brief generation is still delegated to
    `PromptOnlyGenerator` (i.e. Claude). `FalAIGenerator` adds on top the
    ability to actually materialize the image and the video.

    Usage from the UI:
        gen = FalAIGenerator(...)
        briefs = gen.briefs(product=, target_audience=, variants=)
        asset = gen.generate_image(product_id=, brief=)
        asset = gen.generate_video(product_id=, brief=)
    """

    def __init__(
        self,
        prompt_generator: CreativeGenerator,
        *,
        image_model: str | None = None,
        video_model: str | None = None,
    ) -> None:
        self._prompts = prompt_generator
        self._image_model = image_model or os.environ.get(
            "FAL_IMAGE_MODEL", _DEFAULT_IMAGE_MODEL
        )
        self._video_model = video_model or os.environ.get(
            "FAL_VIDEO_MODEL", _DEFAULT_VIDEO_MODEL
        )

    # --- brief pass-through (protocol compliance) ---
    def briefs(
        self,
        *,
        product: Product,
        target_audience: str,
        variants: list[AdCopyVariant],
    ) -> list[CreativeBrief]:
        return self._prompts.briefs(
            product=product, target_audience=target_audience, variants=variants
        )

    # --- image ---
    def generate_image(
        self, *, product_id: str, brief: CreativeBrief
    ) -> GeneratedCreative:
        payload = {
            "prompt": brief.image_prompt,
            "aspect_ratio": brief.aspect_ratio,
            "rendering_speed": "BALANCED",
        }
        result = fal.run_sync(self._image_model, payload)
        url, width, height, cost = _extract_image_fields(result)
        asset = GeneratedCreative(
            id=str(uuid.uuid4()),
            product_id=product_id,
            variant_index=brief.variant_index,
            kind="image",
            asset_url=url,
            source_prompt=brief.image_prompt,
            model=self._image_model,
            cost_brl=cost if cost is not None else _IMAGE_FALLBACK_COST_BRL,
            width=width,
            height=height,
            generated_at=datetime.now(timezone.utc),
        )
        return asset

    # --- video ---
    def generate_video(
        self, *, product_id: str, brief: CreativeBrief
    ) -> GeneratedCreative:
        payload = {
            "prompt": brief.video_prompt,
            "aspect_ratio": brief.aspect_ratio,
            "duration": f"{brief.video_duration_s}s",
        }
        result = fal.run_queued(
            self._video_model,
            payload,
            poll_interval_s=3.0,
            max_wait_s=360.0,
        )
        url, cost = _extract_video_fields(result)
        asset = GeneratedCreative(
            id=str(uuid.uuid4()),
            product_id=product_id,
            variant_index=brief.variant_index,
            kind="video",
            asset_url=url,
            source_prompt=brief.video_prompt,
            model=self._video_model,
            cost_brl=cost if cost is not None else _VIDEO_FALLBACK_COST_BRL,
            duration_s=brief.video_duration_s,
            generated_at=datetime.now(timezone.utc),
        )
        return asset


def _extract_image_fields(result: dict) -> tuple[str, int | None, int | None, float | None]:
    """Pull the image URL + dimensions + billed cost from a fal response.

    fal.ai responses vary slightly per model. Ideogram returns:
      {"images": [{"url": ..., "width": ..., "height": ...}], "timings": ...}
    Most image models follow the same shape.
    """
    images = result.get("images") or []
    if not images:
        raise fal.FalError(f"fal response missing 'images': {result}")
    first = images[0]
    url = first.get("url")
    if not url:
        raise fal.FalError(f"fal image missing url: {first}")
    width = first.get("width")
    height = first.get("height")
    cost = _extract_cost(result)
    return url, width, height, cost


def _extract_video_fields(result: dict) -> tuple[str, float | None]:
    """Pull video URL from a fal response. Veo returns:
      {"video": {"url": ...}, "timings": ...}
    """
    video = result.get("video") or {}
    url = video.get("url")
    if not url:
        raise fal.FalError(f"fal video missing url: {result}")
    return url, _extract_cost(result)


def _extract_cost(result: dict) -> float | None:
    """fal exposes cost on `timings.billable_cost` on some models. When
    absent, return None — the caller falls back to a hard-coded estimate."""
    timings = result.get("timings") or {}
    cost = timings.get("billable_cost")
    if cost is None:
        return None
    try:
        return float(cost)
    except (TypeError, ValueError):
        return None


# Ideogram v3 is on Replicate, so we default to it for parity with fal.ai's
# image default. "balanced" hits the sweet spot of quality vs. cost — the
# only model on Replicate that reliably renders Portuguese headlines inside
# the image. Swap via REPLICATE_IMAGE_MODEL if you want flux-2-pro,
# imagen-4, seedream-4, etc.
_DEFAULT_REPLICATE_IMAGE_MODEL = "ideogram-ai/ideogram-v3-balanced"
# Video default: Google Veo 3. SOTA quality for text-to-video at ~R$15/clip,
# fixed 8s, supports native audio on some variants. Escalations via env:
#   google/veo-3-fast             — cheaper/faster Veo 3
#   kwaivgi/kling-v1.6-standard   — Kling text-to-video, ~R$2/clip
#   kwaivgi/kling-v2.0-master     — Kling 2.0 master
#   minimax/video-01              — budget fallback, 6s 720p
# Note: Kling v1.6 Pro on Replicate is image-to-video only and will 422 on
# plain text prompts. Use the "standard" slug for text-to-video Kling.
_DEFAULT_REPLICATE_VIDEO_MODEL = "google/veo-3"


class ReplicateGenerator:
    """Direct image/video generation via Replicate.

    Same shape as `FalAIGenerator`, different provider. Replicate tends to
    be friendlier to Brazilian billing (accepts international cards without
    the fal.ai tax-ID rejection). Defaults:

    - Image: `black-forest-labs/flux-1.1-pro` — SOTA quality, weaker at
      Portuguese text than Ideogram. Override with `REPLICATE_IMAGE_MODEL`
      if you want to try `ideogram-ai/ideogram-v2a` or whichever model.
    - Video: `luma/ray` (Luma Dream Machine) — reliably available on
      Replicate, decent quality for affiliate-ad use. Override with
      `REPLICATE_VIDEO_MODEL` for `minimax/video-01` etc.

    On-image Portuguese text: if your ads lean heavily on headlines rendered
    inside the image (e.g. "APENAS HOJE R$97"), Ideogram on fal.ai is still
    stronger. Replicate's FLUX gets the layout right but can scramble
    individual letters. For text-light product-focused ads, FLUX is fine.
    """

    def __init__(
        self,
        prompt_generator: CreativeGenerator,
        *,
        image_model: str | None = None,
        video_model: str | None = None,
    ) -> None:
        self._prompts = prompt_generator
        self._image_model = image_model or os.environ.get(
            "REPLICATE_IMAGE_MODEL", _DEFAULT_REPLICATE_IMAGE_MODEL
        )
        self._video_model = video_model or os.environ.get(
            "REPLICATE_VIDEO_MODEL", _DEFAULT_REPLICATE_VIDEO_MODEL
        )

    def briefs(
        self,
        *,
        product: Product,
        target_audience: str,
        variants: list[AdCopyVariant],
    ) -> list[CreativeBrief]:
        return self._prompts.briefs(
            product=product, target_audience=target_audience, variants=variants
        )

    def generate_image(
        self, *, product_id: str, brief: CreativeBrief
    ) -> GeneratedCreative:
        # Model-agnostic minimal payload. Every image model on Replicate
        # accepts `prompt` and `aspect_ratio`; extras (output_format,
        # safety_tolerance, style_type) vary and trip 422s.
        payload = {
            "prompt": brief.image_prompt,
            "aspect_ratio": brief.aspect_ratio,
        }
        result = replicate.run(self._image_model, payload)
        url = _first_url(result.get("output"))
        if not url:
            raise replicate.ReplicateError(
                f"replicate image response missing output: {result}"
            )
        return GeneratedCreative(
            id=str(uuid.uuid4()),
            product_id=product_id,
            variant_index=brief.variant_index,
            kind="image",
            asset_url=url,
            source_prompt=brief.image_prompt,
            model=f"replicate/{self._image_model}",
            cost_brl=_IMAGE_FALLBACK_COST_BRL,
            generated_at=datetime.now(timezone.utc),
        )

    def generate_video(
        self, *, product_id: str, brief: CreativeBrief
    ) -> GeneratedCreative:
        payload = _build_video_payload(self._video_model, brief)
        result = replicate.run(
            self._video_model,
            payload,
            poll_interval_s=5.0,
            max_wait_s=600.0,  # Veo 3 can take 2–5 minutes
        )
        url = _first_url(result.get("output"))
        if not url:
            raise replicate.ReplicateError(
                f"replicate video response missing output: {result}"
            )
        return GeneratedCreative(
            id=str(uuid.uuid4()),
            product_id=product_id,
            variant_index=brief.variant_index,
            kind="video",
            asset_url=url,
            source_prompt=brief.video_prompt,
            model=f"replicate/{self._video_model}",
            cost_brl=_VIDEO_FALLBACK_COST_BRL,
            duration_s=brief.video_duration_s,
            generated_at=datetime.now(timezone.utc),
        )


def _build_video_payload(model: str, brief: CreativeBrief) -> dict:
    """Per-model-family video payload. Most video models on Replicate accept
    `prompt`; only some support `aspect_ratio` and `duration`. Sending
    unsupported keys returns 422, so we dispatch on the model slug prefix.

    Kling (v1.6 and v2.0) accepts `aspect_ratio` (16:9, 9:16, 1:1) and
    `duration` (5 or 10, integer seconds). We clamp the brief's
    `video_duration_s` into Kling's discrete set.

    Veo 3 accepts `aspect_ratio` (16:9, 9:16) and `duration_seconds`
    (4 to 8). Different key name, so the dispatch matters.

    Minimax video-01 is fixed at 6s 720p; sending extras is an error.
    """
    base = {"prompt": brief.video_prompt}
    if model.startswith("kwaivgi/kling"):
        # Kling supports 5 or 10 second durations only.
        duration = 10 if brief.video_duration_s >= 8 else 5
        return {
            **base,
            "aspect_ratio": brief.aspect_ratio if brief.aspect_ratio in {"16:9", "9:16", "1:1"} else "9:16",
            "duration": duration,
            "cfg_scale": 0.5,
            "negative_prompt": "low quality, blurry, distorted faces, watermark, text gibberish",
        }
    if model.startswith("google/veo"):
        # Veo 3 on Replicate: 8s fixed, accepts `aspect_ratio` and an
        # optional `enhance_prompt`. Duration is NOT a user param.
        return {
            **base,
            "aspect_ratio": brief.aspect_ratio if brief.aspect_ratio in {"16:9", "9:16"} else "16:9",
            "enhance_prompt": True,
        }
    # minimax/video-01, luma/ray-*, wan-*, pixverse/*, etc — stick to prompt.
    return base


def _first_url(output) -> str | None:
    """Replicate returns `output` as a URL string, a list of URL strings, or
    a dict with a url. Normalize."""
    if output is None:
        return None
    if isinstance(output, str):
        return output
    if isinstance(output, list) and output:
        return _first_url(output[0])
    if isinstance(output, dict):
        return output.get("url")
    return None


def build_default_creative_generator() -> CreativeGenerator:
    """Default wiring. Always starts with a `PromptOnlyGenerator` so Claude
    can produce briefs. Then, based on env vars, wraps it in a direct-
    generation provider:

    1. `REPLICATE_API_TOKEN` — `ReplicateGenerator` (preferred for Brazilian
       operators who have Replicate billing set up but not fal.ai).
    2. `FAL_API_KEY` — `FalAIGenerator` (preferred when fal.ai billing works
       — Ideogram v3 on fal is still best for Portuguese on-image text).

    If both are set, Replicate wins. Override with `CREATIVE_PROVIDER=fal`
    or `CREATIVE_PROVIDER=replicate` to force a specific provider.
    """
    # Load .env *before* reading provider env vars. The other builders call
    # load_dotenv too, but the env var check in this function has to happen
    # first, so we can't rely on them. Idempotent, cheap, override=False so
    # shell-exported values still win.
    from pathlib import Path

    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parents[3] / ".env", override=False)

    from ..traffic_planner import build_default_traffic_planner

    prompt_gen = PromptOnlyGenerator(planner=build_default_traffic_planner())
    has_replicate = bool(
        os.environ.get("REPLICATE_API_TOKEN") or os.environ.get("REPLICATE_API_KEY")
    )
    has_fal = bool(os.environ.get("FAL_API_KEY") or os.environ.get("FAL_KEY"))

    forced = (os.environ.get("CREATIVE_PROVIDER") or "").strip().lower()
    if forced == "fal" and has_fal:
        return FalAIGenerator(prompt_generator=prompt_gen)
    if forced == "replicate" and has_replicate:
        return ReplicateGenerator(prompt_generator=prompt_gen)

    if has_replicate:
        return ReplicateGenerator(prompt_generator=prompt_gen)
    if has_fal:
        return FalAIGenerator(prompt_generator=prompt_gen)
    return prompt_gen
