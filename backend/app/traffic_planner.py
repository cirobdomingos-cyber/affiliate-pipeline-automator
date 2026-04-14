"""Traffic planner — LLM-generated organic calendars and paid ad variants.

Stage 3 of the pipeline. Takes a scored product from discovery and produces
the raw material for actually driving traffic to it:

- **Organic plan (Sonnet 4.6, one-shot).** A cohesive 7–14 day content
  calendar across the channels the operator picked. Sonnet because the
  calendar needs to *cohere* — the same positioning, the same key messages,
  varied formats across platforms. Fragmenting this into per-post Haiku
  calls would produce a disconnected plan.

- **Ad variant batch (Haiku 4.5, bulk).** N paid-ad copy variants for a
  single platform, returned in one call. Haiku because each variant is
  short, independent, and the real value is breadth — give the operator a
  dozen angles to A/B test, don't over-think any single one.

Both methods follow the same prompt-caching contract as `llm.py`:
frozen system prompts in module constants, per-call data in the user
message, `cache_control` on every request. The `AnthropicClientProtocol`
from `llm.py` is reused so tests can inject the same fake client.
"""

from __future__ import annotations

import logging

from .llm import AnthropicClientProtocol
from .models import (
    AdCopyVariant,
    AdVariantBatch,
    ContentBrief,
    OrganicPlan,
    Product,
    TrafficChannel,
)

logger = logging.getLogger(__name__)


_ORGANIC_PLAN_SYSTEM_PROMPT = """You are a senior content strategist for Brazilian affiliate marketers.

You will be given one affiliate product and a list of organic channels the operator wants to use. Your job is to produce a cohesive content strategy + multi-day calendar that would believably convert.

# Output structure — OrganicPlan

You must return a single OrganicPlan with the following fields:

1. **target_audience** — One sentence. Name the specific persona: age range, life stage, pain point, current state. Never "entrepreneurs" or "anyone interested in X".

2. **positioning** — One sentence. How this product is framed relative to alternatives. Avoid generic "best-in-class" language.

3. **key_messages** — 3 to 5 bullet-style messages that every post in the calendar should reinforce. These are the anchors — the calendar rotates through them.

4. **posting_rhythm** — One sentence on cadence. e.g. "Instagram: daily, alternating Reels and Carousels. TikTok: 3x/week. YouTube Short: 2x/week."

5. **briefs** — The content calendar itself. For each requested channel, produce posts spaced across the day range the operator asks for. Each brief needs:
   - `channel`: one of the requested channels
   - `day_offset`: 0-indexed day number within the plan
   - `hook`: 1–2 sentences that stop the scroll. Must be concrete and specific.
   - `body`: 3–5 sentences of what to actually say. Reference a key message by its idea, not by number.
   - `call_to_action`: Exactly what the viewer should do next. Be specific: "tap the link in bio and start the 7-day free trial" beats "click the link".
   - `hashtags`: 5–10 hashtags WITHOUT the # prefix. Mix broad and niche.
   - `format_notes`: Production hints. e.g. "vertical 15s video, text overlay on hook, voiceover on body".

# Quality rules

- Vary the hook pattern across briefs — don't reuse "Did you know..." or "Here's the truth about..." more than once.
- Reference the product's actual price and audience when relevant. Generic briefs are worthless.
- No posts should be pure pitch. Mix value-forward posts (education, story, demo) with direct offer posts roughly 4:1.
- Hashtags should be in Portuguese for Brazilian audiences, unless the target niche is explicitly international.

Return only the structured OrganicPlan. No prose outside it."""


_AD_VARIANTS_SYSTEM_PROMPT = """You are a direct-response paid-ads copywriter for Brazilian affiliate products.

You will be given one product, one ad platform, and a target audience. Your job is to produce multiple ad copy variants that an operator can A/B test against each other on day one of a paid campaign.

# Output — AdVariantBatch

Return a batch of AdCopyVariant objects. Each variant has:

1. **platform** — Use exactly the platform from the input.

2. **headline** — Max 40 characters. Must be scroll-stopping. Use a specific number, a sharp promise, or a pattern interrupt. Never "Learn more about X".

3. **primary_text** — 2–4 sentences. Open with the pain or desire, bridge to the product, end with the specific outcome. Write in the voice of the audience, not the brand.

4. **description** — One sentence that reinforces the primary text or adds a risk-reversal (guarantee, free trial, money-back). Shown below primary text on Meta, as a second line on Google.

5. **target_audience** — One sentence describing who this specific variant is aimed at. Variants within the same batch should differ on audience angle, not be copies.

6. **daily_budget_brl** — Suggested daily spend in BRL, based on the product price and Brazilian CPM norms. Use 30 BRL/day minimum for testing, up to 200 BRL/day for high-ticket products.

7. **creative_notes** — One sentence on the creative direction: hook pattern, visual angle, emotional lever. e.g. "Before/after transformation, student voiceover".

# Variant diversity rules

Within a single batch, the variants MUST differ on the *angle*, not just the wording:
- Variant 1: pain-point-led (audience is frustrated, product solves the pain)
- Variant 2: aspiration-led (audience wants the outcome, product is the shortcut)
- Variant 3: social-proof-led (others like the audience got results, audience can too)
- Variant 4: objection-handling-led (audience is skeptical, variant names the doubt and answers it)
- Variant 5+: curiosity-led, loss-aversion-led, or authority-led

If the operator asks for N variants, cycle through these angles in order and never produce two variants from the same angle in one batch.

# Platform rules

- **meta_ad** (Facebook/Instagram): conversational, emoji-friendly, mobile-first. Headlines can be questions.
- **google_ad**: search intent, keyword-anchored, no emojis, headline is what the searcher typed.
- **tiktok_ad**: Gen-Z voice, hook in the first 2 seconds, never feels like an ad.

Return only the structured AdVariantBatch. No prose outside it."""


class TrafficPlanner:
    SONNET_MODEL = "claude-sonnet-4-6"
    HAIKU_MODEL = "claude-haiku-4-5"

    def __init__(self, client: AnthropicClientProtocol) -> None:
        self._client = client

    def plan_organic(
        self,
        *,
        product: Product,
        target_audience: str,
        channels: list[TrafficChannel],
        days: int,
    ) -> OrganicPlan:
        """Generate a cohesive organic content plan — one Sonnet call.

        `channels` should contain only organic channels from `ORGANIC_CHANNELS`.
        The caller is responsible for filtering; the planner trusts its input
        and passes the full list through to the LLM.
        """
        channel_list = "\n".join(f"- {c.value}" for c in channels)
        user_message = (
            f"Product name: {product.name}\n"
            f"Platform: {product.platform.value}\n"
            f"Category: {product.category or 'unknown'}\n"
            f"Price: R${product.price_brl or 0:.2f}\n"
            f"Commission: {product.commission_pct or 0:.0f}%\n"
            f"Producer: {product.producer_name or 'unknown'}\n\n"
            f"Target audience (operator-stated): {target_audience}\n\n"
            f"Requested organic channels:\n{channel_list}\n\n"
            f"Plan horizon: {days} days"
        )

        response = self._client.messages.parse(
            model=self.SONNET_MODEL,
            max_tokens=8192,
            system=[
                {
                    "type": "text",
                    "text": _ORGANIC_PLAN_SYSTEM_PROMPT,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            messages=[{"role": "user", "content": user_message}],
            output_format=OrganicPlan,
        )
        plan: OrganicPlan = response.parsed_output
        return plan

    def generate_ad_variants(
        self,
        *,
        product: Product,
        target_audience: str,
        platform: TrafficChannel,
        count: int = 5,
    ) -> list[AdCopyVariant]:
        """Bulk-generate N paid-ad variants for one platform — single Haiku call.

        Returns variants differing on strategic angle (pain / aspiration /
        social proof / objection / curiosity) rather than just wording. The
        system prompt bakes the angle cycle in, so the caller just asks for
        `count` variants.
        """
        user_message = (
            f"Product: {product.name}\n"
            f"Category: {product.category or 'unknown'}\n"
            f"Price: R${product.price_brl or 0:.2f}\n"
            f"Commission: {product.commission_pct or 0:.0f}%\n"
            f"Producer: {product.producer_name or 'unknown'}\n\n"
            f"Target audience: {target_audience}\n"
            f"Platform: {platform.value}\n"
            f"Number of variants to generate: {count}"
        )

        response = self._client.messages.parse(
            model=self.HAIKU_MODEL,
            max_tokens=4096,
            system=[
                {
                    "type": "text",
                    "text": _AD_VARIANTS_SYSTEM_PROMPT,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            messages=[{"role": "user", "content": user_message}],
            output_format=AdVariantBatch,
        )
        batch: AdVariantBatch = response.parsed_output
        return batch.variants


def build_default_traffic_planner() -> TrafficPlanner:
    """Real-client constructor. Lazy import so tests don't need an API key."""
    import anthropic

    return TrafficPlanner(client=anthropic.Anthropic())
