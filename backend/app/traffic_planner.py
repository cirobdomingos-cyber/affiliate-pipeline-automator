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
    CreativeBrief,
    CreativeBriefBatch,
    OrganicPlan,
    Product,
    TrafficChannel,
)

logger = logging.getLogger(__name__)


_ORGANIC_PLAN_SYSTEM_PROMPT = """You are a senior content strategist for Brazilian affiliate marketers.

**LANGUAGE: Respond entirely in Brazilian Portuguese (pt-BR).** Every field of the OrganicPlan output — target_audience, positioning, key_messages, posting_rhythm, and every ContentBrief (hook, body, call_to_action, format_notes) — must be written in natural, native-level Brazilian Portuguese. Hashtags too. The only exception is field names in the output schema (those stay in English because they are Pydantic attributes).

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

**LANGUAGE: Respond entirely in Brazilian Portuguese (pt-BR).** Every field of every AdCopyVariant — headline, primary_text, description, target_audience, creative_notes — must be written in natural, native-level Brazilian Portuguese targeted at a Brazilian audience. Pricing in BRL (R$). Idioms should be Brazilian, not Portuguese from Portugal. The only exception is the `platform` enum value (stays as the literal enum string).

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


_CREATIVE_BRIEFS_SYSTEM_PROMPT = """You are the creative director for a Brazilian direct-response affiliate operation.

You will receive one product, one target audience, and N ad copy variants already written. Your job is to produce, for EACH variant, a matched pair of creative prompts — one for a static image and one for a short video — that an operator can paste into an external generation tool without editing.

# Output — CreativeBriefBatch

Return a `briefs` list with exactly N entries, one per input variant, in the same order. Each entry has:

- **variant_index** — 0-based index of the input variant this brief is for. MUST match the input order.
- **platform** — same platform as the source variant.
- **aspect_ratio** — pick based on platform: `meta_ad` → "1:1" (feed) or "9:16" if you explicitly target stories/reels; `google_ad` → "1:1"; `tiktok_ad` → "9:16".
- **image_prompt** — an Ideogram v3-compatible prompt (works for Midjourney too). Rules below.
- **image_tool** — always "ideogram" (best Portuguese text rendering).
- **video_prompt** — a Veo 3 / Runway Gen-4 prompt. Rules below.
- **video_tool** — "veo3".
- **video_duration_s** — 5 to 8 seconds for feed ads; up to 15 for TikTok/Reels.

# Image prompt rules (Ideogram v3 format)

Include in order:
1. **Scene** — concrete subject, location, time of day. Never "a nice image of".
2. **Composition** — shot type (close-up, medium, wide), camera angle, depth.
3. **Lighting** — natural/studio/golden hour; describe quality and direction.
4. **Style** — photorealistic, editorial, lifestyle photography, stock-ad-style; avoid "artistic" — affiliate ads convert on realism.
5. **On-image text** — if the headline is short (<35 chars) include it VERBATIM between quotes in the prompt with instruction like: `with the text "APENAS HOJE R$97" in bold sans-serif at the top`. Ideogram will render it. Never paraphrase the copy.
6. **Brand cues** — any colors or props that reinforce the offer.
7. **Aspect ratio tag** — end with ` --ar 9:16` or ` --ar 1:1`.

Example: `Young Brazilian woman in her late 20s working from a laptop on a sunny co-working space in São Paulo, medium shot, natural window light from the left, editorial lifestyle photography, with the text "Primeira venda em 7 dias" in bold white sans-serif on a blue banner at the bottom, warm tones, 1:1 composition --ar 1:1`

# Video prompt rules (Veo 3 / Runway Gen-4 format)

Structure as a brief shot list (not one run-on sentence):
1. **Opening 2s hook** — what's on screen at second 0. Reference the ad variant's hook.
2. **Middle** — transition, camera motion, subject action.
3. **End** — final beat, product or brand mention, CTA moment.

Describe camera (handheld, dolly, static), subject, environment, and any on-screen text that appears over time. Keep it under 80 words total.

Example:
```
0-2s: close-up of a young woman's hands typing on a laptop, handheld, kitchen in background, morning light. Text overlay fades in: "Comecei do zero"
2-5s: she smiles, looks at screen showing Pix notification, subtle zoom in
5-8s: cut to wide shot, she stands up happy, text overlay: "Aprenda o método — link na bio"
```

# Hard rules

- Portuguese text on the image/video must be VERBATIM from the ad copy variant — never paraphrase or translate. Ideogram and Veo render what you write.
- No celebrities, no copyrighted brands in the imagery, no professional athletes.
- Never describe people in a way that could be read as stereotype bait.

# Hand hygiene (critical — affiliate ads get rejected for mutant hands)

Every current image model — Ideogram, FLUX, Imagen, nano-banana — occasionally botches hands (extra fingers, wrong finger count, warped wrists). Work around this at the composition level:

- **Avoid close-ups of hands.** No "close-up of hands typing", no "hands counting money", no "pointing finger". These produce 6-finger disasters.
- **Hide hands when possible.** Hands in pockets, behind a laptop, holding a coffee cup that covers the fingers, resting on a table, out of frame.
- **When hands must be visible, keep them small in frame and at rest** — not gesturing, not pointing, not counting. A person's hand resting naturally on a desk or lap is safe; the same hand raised mid-gesture is a coin flip.
- **Prefer medium / wide shots over tight close-ups** unless the shot is face-only.
- When the ad concept screams "show hands" (e.g. holding a product), write the prompt to show the product held at chest height with the hand mostly behind or below the product so fingers are implied rather than rendered.

Apply these to both the image_prompt and the video_prompt.

Return only the CreativeBriefBatch. No prose outside it."""


_AUDIENCE_SUGGESTION_SYSTEM_PROMPT = """You are a direct-response strategist for Brazilian affiliate products.

You will be given metadata about one product. Propose ONE specific target audience in a single sentence of Portuguese.

Rules:
- Always a single sentence, 15–30 words. No lists, no preamble.
- Name the concrete persona: age range, life stage, current pain, and what they want. Example: "Mães de 28–40 anos em Brasília que voltaram a trabalhar fora e buscam emagrecer sem dieta radical nem academia."
- Never "entrepreneurs", "anyone interested in X", "people who want to learn". Be specific or don't bother.
- Write in Portuguese regardless of the product language, because these products are sold in Brazil.

Return only the sentence. No quotes, no prefix."""


class TrafficPlanner:
    SONNET_MODEL = "claude-sonnet-4-6"
    HAIKU_MODEL = "claude-haiku-4-5"

    def __init__(self, client: AnthropicClientProtocol) -> None:
        self._client = client

    def suggest_audience(self, product: Product) -> str:
        """Ask Haiku for a one-sentence target-audience proposal.

        Cheap (Haiku + ~200 tokens output) and unstructured — we return the
        plain text straight from the model so the UI can drop it into a text
        input for the operator to tweak.
        """
        user_message = (
            f"Product name: {product.name}\n"
            f"Platform: {product.platform.value}\n"
            f"Category: {product.category or 'unknown'}\n"
            f"Price: R${product.price_brl or 0:.2f}\n"
            f"Commission: {product.commission_pct or 0:.0f}%\n"
            f"Producer: {product.producer_name or 'unknown'}\n"
        )
        response = self._client.messages.create(
            model=self.HAIKU_MODEL,
            max_tokens=300,
            system=[
                {
                    "type": "text",
                    "text": _AUDIENCE_SUGGESTION_SYSTEM_PROMPT,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            messages=[{"role": "user", "content": user_message}],
        )
        # Anthropic SDK returns a list of content blocks; grab the first text.
        for block in response.content:
            if getattr(block, "type", None) == "text":
                return block.text.strip()
        return ""

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

    def generate_creative_briefs(
        self,
        *,
        product: Product,
        target_audience: str,
        variants: list[AdCopyVariant],
    ) -> list[CreativeBrief]:
        """One Haiku call returns a matched image+video prompt per variant.

        Output is deliberately tool-agnostic strings so the operator can paste
        into Ideogram / Veo / Runway without adaptation. When we later wire
        direct API generation via `services/creatives.py`, that module can
        dispatch on each brief's `image_tool` / `video_tool` field.
        """
        if not variants:
            return []
        variant_block = "\n\n".join(
            f"Variant {i}:\n"
            f"  Platform: {v.platform.value}\n"
            f"  Headline: {v.headline}\n"
            f"  Primary text: {v.primary_text}\n"
            f"  Description: {v.description}\n"
            f"  Creative notes: {v.creative_notes or '—'}"
            for i, v in enumerate(variants)
        )
        user_message = (
            f"Product: {product.name}\n"
            f"Category: {product.category or 'unknown'}\n"
            f"Price: R${product.price_brl or 0:.2f}\n"
            f"Producer: {product.producer_name or 'unknown'}\n\n"
            f"Target audience: {target_audience}\n\n"
            f"Ad copy variants to brief ({len(variants)} total):\n\n{variant_block}"
        )
        response = self._client.messages.parse(
            model=self.HAIKU_MODEL,
            max_tokens=6000,
            system=[
                {
                    "type": "text",
                    "text": _CREATIVE_BRIEFS_SYSTEM_PROMPT,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            messages=[{"role": "user", "content": user_message}],
            output_format=CreativeBriefBatch,
        )
        batch: CreativeBriefBatch = response.parsed_output
        return batch.briefs


def build_default_traffic_planner() -> TrafficPlanner:
    """Real-client constructor. Lazy import so tests don't need an API key."""
    from pathlib import Path

    import anthropic
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parents[2] / ".env", override=False)
    return TrafficPlanner(client=anthropic.Anthropic())
