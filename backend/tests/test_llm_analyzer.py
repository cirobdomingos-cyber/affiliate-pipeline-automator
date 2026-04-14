"""LLM analyzer tests with a mocked Anthropic client.

Why mock the client: the analyzer is the part of the system most likely to
break (taxonomy drift, schema changes, model swaps), and it's the part we
most need fast feedback on. Tests assert:

1. The right model is called for each method (Haiku for bulk, Sonnet for one-shot).
2. cache_control is set on every call (the prompt-caching contract).
3. Pydantic schemas round-trip correctly.
4. The system prompt is the frozen module constant — no per-call interpolation
   that would silently invalidate the cache.

These assertions catch the failure modes that "I added prompt caching" usually
hides. They run in milliseconds and never touch the network.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from backend.app.llm import (
    _NICHE_FIT_SYSTEM_PROMPT,
    _SALES_PAGE_SYSTEM_PROMPT,
    LLMAnalyzer,
)
from backend.app.models import (
    NicheFit,
    NicheFitBatch,
    Platform,
    Product,
    SalesPageSignals,
)


# ---------- Fake Anthropic client ----------


@dataclass
class _FakeParseResponse:
    parsed_output: Any


@dataclass
class _FakeMessages:
    canned_outputs: list[Any]
    calls: list[dict] = field(default_factory=list)

    def parse(self, **kwargs):
        self.calls.append(kwargs)
        if not self.canned_outputs:
            raise AssertionError("FakeMessages.parse called more times than canned outputs provided")
        return _FakeParseResponse(parsed_output=self.canned_outputs.pop(0))


@dataclass
class FakeAnthropicClient:
    messages: _FakeMessages

    @classmethod
    def with_responses(cls, *responses) -> "FakeAnthropicClient":
        return cls(messages=_FakeMessages(canned_outputs=list(responses)))


# ---------- Sales-page analyzer ----------


def _signals(composite: float = 0.7) -> SalesPageSignals:
    return SalesPageSignals(
        scarcity=0.5,
        social_proof=0.8,
        guarantee_strength=0.6,
        urgency=0.4,
        audience_clarity=0.9,
        composite=composite,
        notes="Strong audience targeting, weak urgency framing.",
    )


class TestSalesPageAnalyzer:
    def test_uses_haiku_model(self):
        client = FakeAnthropicClient.with_responses(_signals())
        analyzer = LLMAnalyzer(client=client)

        analyzer.analyze_sales_page(
            product_name="Curso de Tráfego",
            page_text="Aprenda tráfego pago do zero...",
        )

        call = client.messages.calls[0]
        assert call["model"] == "claude-haiku-4-5"

    def test_sets_cache_control(self):
        client = FakeAnthropicClient.with_responses(_signals())
        analyzer = LLMAnalyzer(client=client)
        analyzer.analyze_sales_page(product_name="X", page_text="y")

        # cache_control is a block-level marker on the first (and only) system
        # text block. Block-level placement is required because messages.parse()
        # does not accept the top-level cache_control shortcut that create() does.
        system_blocks = client.messages.calls[0]["system"]
        assert isinstance(system_blocks, list)
        assert system_blocks[0]["cache_control"] == {"type": "ephemeral"}

    def test_uses_frozen_system_prompt(self):
        # Critical: any per-call string interpolation here would silently
        # invalidate the prompt cache. The system prompt block's text must be
        # the literal module constant, not a formatted copy.
        client = FakeAnthropicClient.with_responses(_signals())
        analyzer = LLMAnalyzer(client=client)
        analyzer.analyze_sales_page(product_name="X", page_text="y")

        system_blocks = client.messages.calls[0]["system"]
        assert system_blocks[0]["text"] is _SALES_PAGE_SYSTEM_PROMPT

    def test_passes_structured_output_schema(self):
        client = FakeAnthropicClient.with_responses(_signals())
        analyzer = LLMAnalyzer(client=client)
        analyzer.analyze_sales_page(product_name="X", page_text="y")

        assert client.messages.calls[0]["output_format"] is SalesPageSignals

    def test_returns_parsed_signals(self):
        canned = _signals(composite=0.85)
        client = FakeAnthropicClient.with_responses(canned)
        analyzer = LLMAnalyzer(client=client)

        result = analyzer.analyze_sales_page(product_name="X", page_text="y")

        assert isinstance(result, SalesPageSignals)
        assert result.composite == 0.85
        assert result.notes.startswith("Strong audience")

    def test_user_message_includes_product_name_and_page_text(self):
        client = FakeAnthropicClient.with_responses(_signals())
        analyzer = LLMAnalyzer(client=client)
        analyzer.analyze_sales_page(
            product_name="Curso XYZ",
            page_text="ABCDEF",
        )

        user_content = client.messages.calls[0]["messages"][0]["content"]
        assert "Curso XYZ" in user_content
        assert "ABCDEF" in user_content

    def test_user_message_does_not_leak_into_system_prompt(self):
        # Regression guard — if a refactor moved page_text into the system
        # prompt, every call would invalidate the cache. Catch it here.
        client = FakeAnthropicClient.with_responses(_signals())
        analyzer = LLMAnalyzer(client=client)
        analyzer.analyze_sales_page(product_name="UNIQUE_NAME_42", page_text="UNIQUE_TEXT_42")

        system_text = client.messages.calls[0]["system"][0]["text"]
        assert "UNIQUE_NAME_42" not in system_text
        assert "UNIQUE_TEXT_42" not in system_text


# ---------- Niche-fit analyzer ----------


def _make_product(external_id: str, name: str) -> Product:
    return Product(
        platform=Platform.HOTMART,
        external_id=external_id,
        name=name,
        url=f"https://example.com/{external_id}",
        category="Marketing Digital",
        price_brl=497.0,
        commission_pct=50.0,
        popularity=70.0,
    )


def _batch_for(products: list[Product], scores: list[float]) -> NicheFitBatch:
    return NicheFitBatch(
        rankings=[
            NicheFit(
                product_id=p.id,
                fit_score=score,
                reasoning=f"Reasoning for {p.name}",
            )
            for p, score in zip(products, scores)
        ]
    )


class TestNicheFitAnalyzer:
    def test_uses_sonnet_model(self):
        products = [_make_product("P1", "Product One")]
        client = FakeAnthropicClient.with_responses(_batch_for(products, [85.0]))
        analyzer = LLMAnalyzer(client=client)

        analyzer.rank_by_niche_fit(
            target_niche="finance",
            target_audience="beginners",
            products=products,
        )

        call = client.messages.calls[0]
        assert call["model"] == "claude-sonnet-4-6"

    def test_sets_cache_control(self):
        products = [_make_product("P1", "X")]
        client = FakeAnthropicClient.with_responses(_batch_for(products, [50.0]))
        analyzer = LLMAnalyzer(client=client)

        analyzer.rank_by_niche_fit(
            target_niche="health",
            target_audience="women 30+",
            products=products,
        )

        system_blocks = client.messages.calls[0]["system"]
        assert system_blocks[0]["cache_control"] == {"type": "ephemeral"}

    def test_uses_frozen_system_prompt(self):
        products = [_make_product("P1", "X")]
        client = FakeAnthropicClient.with_responses(_batch_for(products, [50.0]))
        analyzer = LLMAnalyzer(client=client)

        analyzer.rank_by_niche_fit(
            target_niche="health",
            target_audience="women 30+",
            products=products,
        )

        system_blocks = client.messages.calls[0]["system"]
        assert system_blocks[0]["text"] is _NICHE_FIT_SYSTEM_PROMPT

    def test_passes_structured_output_schema(self):
        products = [_make_product("P1", "X")]
        client = FakeAnthropicClient.with_responses(_batch_for(products, [50.0]))
        analyzer = LLMAnalyzer(client=client)

        analyzer.rank_by_niche_fit(
            target_niche="x", target_audience="y", products=products
        )

        assert client.messages.calls[0]["output_format"] is NicheFitBatch

    def test_returns_rankings_list(self):
        products = [
            _make_product("P1", "Product One"),
            _make_product("P2", "Product Two"),
        ]
        client = FakeAnthropicClient.with_responses(_batch_for(products, [90.0, 40.0]))
        analyzer = LLMAnalyzer(client=client)

        result = analyzer.rank_by_niche_fit(
            target_niche="finance",
            target_audience="beginners",
            products=products,
        )

        assert len(result) == 2
        assert result[0].fit_score == 90.0
        assert result[1].fit_score == 40.0

    def test_user_message_includes_all_products_and_niche(self):
        products = [
            _make_product("P1", "Curso Alpha"),
            _make_product("P2", "Curso Beta"),
        ]
        client = FakeAnthropicClient.with_responses(_batch_for(products, [50.0, 50.0]))
        analyzer = LLMAnalyzer(client=client)

        analyzer.rank_by_niche_fit(
            target_niche="UNIQUE_NICHE_42",
            target_audience="UNIQUE_AUDIENCE_42",
            products=products,
        )

        user_content = client.messages.calls[0]["messages"][0]["content"]
        assert "Curso Alpha" in user_content
        assert "Curso Beta" in user_content
        assert "UNIQUE_NICHE_42" in user_content
        assert "UNIQUE_AUDIENCE_42" in user_content

    def test_user_message_does_not_leak_into_system_prompt(self):
        products = [_make_product("P1", "MAGIC_NAME_99")]
        client = FakeAnthropicClient.with_responses(_batch_for(products, [50.0]))
        analyzer = LLMAnalyzer(client=client)

        analyzer.rank_by_niche_fit(
            target_niche="MAGIC_NICHE_99",
            target_audience="MAGIC_AUDIENCE_99",
            products=products,
        )

        system_text = client.messages.calls[0]["system"][0]["text"]
        assert "MAGIC_NAME_99" not in system_text
        assert "MAGIC_NICHE_99" not in system_text
        assert "MAGIC_AUDIENCE_99" not in system_text
