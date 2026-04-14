"""TrafficPlanner tests — mocked Anthropic client, offline.

Same contract assertions as the LLM analyzer tests:
1. Sonnet used for the one-shot organic planner, Haiku for bulk ad variants
2. cache_control set on every call
3. Frozen system prompts — no per-call interpolation
4. Pydantic schema round-trips
5. User-message inputs never leak into the system prompt
"""

from __future__ import annotations

import pytest

from backend.app.models import (
    AdCopyVariant,
    AdVariantBatch,
    ContentBrief,
    OrganicPlan,
    Platform,
    Product,
    TrafficChannel,
)
from backend.app.traffic_planner import (
    _AD_VARIANTS_SYSTEM_PROMPT,
    _ORGANIC_PLAN_SYSTEM_PROMPT,
    TrafficPlanner,
)
from backend.tests.test_llm_analyzer import FakeAnthropicClient


def _product(name: str = "Curso Teste") -> Product:
    return Product(
        platform=Platform.HOTMART,
        external_id="TEST-TP",
        name=name,
        url="https://hotmart.com/test",
        category="Marketing Digital",
        price_brl=497.0,
        commission_pct=60.0,
        producer_name="Test Producer",
    )


def _sample_plan() -> OrganicPlan:
    return OrganicPlan(
        target_audience="First-year affiliates in Brazil, 25–35, struggling to make their first commission.",
        positioning="The shortcut for beginners who've already bought two courses and still haven't made a sale.",
        key_messages=[
            "You don't need 10k followers to make your first sale",
            "Paid traffic is learnable in 30 days",
            "Commission > course price only if the funnel actually converts",
        ],
        posting_rhythm="Instagram daily, TikTok 3x/week, WhatsApp broadcast weekly",
        briefs=[
            ContentBrief(
                channel=TrafficChannel.INSTAGRAM_REEL,
                day_offset=0,
                hook="Seu primeiro comissionamento está a 30 dias — não a 10k seguidores.",
                body="A maioria dos afiliados iniciantes pensa que precisa de audiência. Errado. Tráfego pago resolve isso em uma semana se você souber segmentar. O curso X ensina exatamente isso.",
                call_to_action="Toque no link na bio e faça a aula grátis hoje.",
                hashtags=["marketingdigital", "afiliados", "trafegopago", "hotmart", "rendaextra"],
                format_notes="Vertical 15s, texto grande no hook, voiceover no body.",
            ),
        ],
    )


def _sample_variants(count: int = 3) -> AdVariantBatch:
    angles = ["pain", "aspiration", "social-proof", "objection", "curiosity"]
    variants = [
        AdCopyVariant(
            platform=TrafficChannel.META_AD,
            headline=f"Test Headline {i+1}",
            primary_text=f"Primary text variant {i+1} using the {angles[i % 5]} angle.",
            description="7-day money-back guarantee.",
            target_audience=f"Variant {i+1} audience angle",
            daily_budget_brl=50.0,
            creative_notes=f"{angles[i % 5]}-led creative direction",
        )
        for i in range(count)
    ]
    return AdVariantBatch(variants=variants)


# ---------- Organic planner ----------


class TestPlanOrganic:
    def test_uses_sonnet_model(self):
        client = FakeAnthropicClient.with_responses(_sample_plan())
        planner = TrafficPlanner(client=client)

        planner.plan_organic(
            product=_product(),
            target_audience="first-year affiliates",
            channels=[TrafficChannel.INSTAGRAM_REEL, TrafficChannel.TIKTOK],
            days=7,
        )

        assert client.messages.calls[0]["model"] == "claude-sonnet-4-6"

    def test_sets_cache_control(self):
        client = FakeAnthropicClient.with_responses(_sample_plan())
        planner = TrafficPlanner(client=client)
        planner.plan_organic(
            product=_product(),
            target_audience="x",
            channels=[TrafficChannel.INSTAGRAM_REEL],
            days=7,
        )
        assert client.messages.calls[0]["cache_control"] == {"type": "ephemeral"}

    def test_uses_frozen_system_prompt(self):
        client = FakeAnthropicClient.with_responses(_sample_plan())
        planner = TrafficPlanner(client=client)
        planner.plan_organic(
            product=_product(),
            target_audience="x",
            channels=[TrafficChannel.INSTAGRAM_REEL],
            days=7,
        )
        assert client.messages.calls[0]["system"] is _ORGANIC_PLAN_SYSTEM_PROMPT

    def test_uses_structured_output_schema(self):
        client = FakeAnthropicClient.with_responses(_sample_plan())
        planner = TrafficPlanner(client=client)
        planner.plan_organic(
            product=_product(),
            target_audience="x",
            channels=[TrafficChannel.INSTAGRAM_REEL],
            days=7,
        )
        assert client.messages.calls[0]["output_format"] is OrganicPlan

    def test_user_message_includes_product_and_channels(self):
        client = FakeAnthropicClient.with_responses(_sample_plan())
        planner = TrafficPlanner(client=client)
        planner.plan_organic(
            product=_product(name="UNIQUE_PRODUCT_42"),
            target_audience="UNIQUE_AUDIENCE_42",
            channels=[TrafficChannel.TIKTOK, TrafficChannel.INSTAGRAM_CAROUSEL],
            days=14,
        )
        user_content = client.messages.calls[0]["messages"][0]["content"]
        assert "UNIQUE_PRODUCT_42" in user_content
        assert "UNIQUE_AUDIENCE_42" in user_content
        assert "tiktok" in user_content
        assert "instagram_carousel" in user_content
        assert "14 days" in user_content

    def test_user_message_does_not_leak_into_system_prompt(self):
        client = FakeAnthropicClient.with_responses(_sample_plan())
        planner = TrafficPlanner(client=client)
        planner.plan_organic(
            product=_product(name="LEAKTEST_PRODUCT"),
            target_audience="LEAKTEST_AUDIENCE",
            channels=[TrafficChannel.INSTAGRAM_REEL],
            days=7,
        )
        system = client.messages.calls[0]["system"]
        assert "LEAKTEST_PRODUCT" not in system
        assert "LEAKTEST_AUDIENCE" not in system

    def test_returns_parsed_plan(self):
        canned = _sample_plan()
        client = FakeAnthropicClient.with_responses(canned)
        planner = TrafficPlanner(client=client)

        result = planner.plan_organic(
            product=_product(),
            target_audience="x",
            channels=[TrafficChannel.INSTAGRAM_REEL],
            days=7,
        )
        assert isinstance(result, OrganicPlan)
        assert len(result.briefs) == 1
        assert len(result.key_messages) == 3


# ---------- Ad variants ----------


class TestGenerateAdVariants:
    def test_uses_haiku_model(self):
        client = FakeAnthropicClient.with_responses(_sample_variants(3))
        planner = TrafficPlanner(client=client)
        planner.generate_ad_variants(
            product=_product(),
            target_audience="x",
            platform=TrafficChannel.META_AD,
            count=3,
        )
        assert client.messages.calls[0]["model"] == "claude-haiku-4-5"

    def test_sets_cache_control(self):
        client = FakeAnthropicClient.with_responses(_sample_variants(3))
        planner = TrafficPlanner(client=client)
        planner.generate_ad_variants(
            product=_product(),
            target_audience="x",
            platform=TrafficChannel.META_AD,
            count=3,
        )
        assert client.messages.calls[0]["cache_control"] == {"type": "ephemeral"}

    def test_uses_frozen_system_prompt(self):
        client = FakeAnthropicClient.with_responses(_sample_variants(3))
        planner = TrafficPlanner(client=client)
        planner.generate_ad_variants(
            product=_product(),
            target_audience="x",
            platform=TrafficChannel.GOOGLE_AD,
            count=3,
        )
        assert client.messages.calls[0]["system"] is _AD_VARIANTS_SYSTEM_PROMPT

    def test_uses_structured_output_schema(self):
        client = FakeAnthropicClient.with_responses(_sample_variants(3))
        planner = TrafficPlanner(client=client)
        planner.generate_ad_variants(
            product=_product(),
            target_audience="x",
            platform=TrafficChannel.META_AD,
            count=3,
        )
        assert client.messages.calls[0]["output_format"] is AdVariantBatch

    def test_user_message_includes_platform_count_and_audience(self):
        client = FakeAnthropicClient.with_responses(_sample_variants(7))
        planner = TrafficPlanner(client=client)
        planner.generate_ad_variants(
            product=_product(),
            target_audience="UNIQUE_ADS_AUDIENCE_42",
            platform=TrafficChannel.TIKTOK_AD,
            count=7,
        )
        user_content = client.messages.calls[0]["messages"][0]["content"]
        assert "tiktok_ad" in user_content
        assert "UNIQUE_ADS_AUDIENCE_42" in user_content
        assert "7" in user_content

    def test_user_message_does_not_leak_into_system_prompt(self):
        client = FakeAnthropicClient.with_responses(_sample_variants(1))
        planner = TrafficPlanner(client=client)
        planner.generate_ad_variants(
            product=_product(name="ADS_LEAK_PRODUCT"),
            target_audience="ADS_LEAK_AUDIENCE",
            platform=TrafficChannel.META_AD,
            count=1,
        )
        system = client.messages.calls[0]["system"]
        assert "ADS_LEAK_PRODUCT" not in system
        assert "ADS_LEAK_AUDIENCE" not in system

    def test_returns_variants_list(self):
        canned = _sample_variants(5)
        client = FakeAnthropicClient.with_responses(canned)
        planner = TrafficPlanner(client=client)

        result = planner.generate_ad_variants(
            product=_product(),
            target_audience="x",
            platform=TrafficChannel.META_AD,
            count=5,
        )
        assert isinstance(result, list)
        assert len(result) == 5
        assert all(isinstance(v, AdCopyVariant) for v in result)

    def test_default_count_is_five(self):
        client = FakeAnthropicClient.with_responses(_sample_variants(5))
        planner = TrafficPlanner(client=client)
        planner.generate_ad_variants(
            product=_product(),
            target_audience="x",
            platform=TrafficChannel.META_AD,
        )
        user_content = client.messages.calls[0]["messages"][0]["content"]
        assert "5" in user_content
