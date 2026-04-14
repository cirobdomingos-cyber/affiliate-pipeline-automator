"""LLM analyzer — Anthropic SDK with prompt caching and structured outputs.

Architecture rules:
- The analyzer is a pure function of (client, inputs) → (typed pydantic output).
  The client is injected so tests can pass a mock and run offline.
- Two-tier model routing: Haiku 4.5 for bulk per-product extraction (cheap,
  fast, cached prefix), Sonnet 4.6 for one-shot reasoning over the whole
  catalog (smarter, used once per discovery run).
- Prompt caching is wired in via top-level `cache_control={"type": "ephemeral"}`.
  The taxonomy / extraction rules sit in the system prompt as a frozen module
  constant — no timestamps, no per-call string interpolation, no varying tool
  set. Render order is tools → system → messages, so putting the stable
  taxonomy first means subsequent calls hit the cache.
- Structured output via `client.messages.parse()` with Pydantic schemas.
  No raw JSON parsing, no regex extraction.

Caching note: Haiku 4.5's minimum cacheable prefix is 4096 tokens. The
taxonomy below is intentionally substantive but may not exceed that on its
own — when it doesn't, the API silently won't cache (no error, just
`cache_creation_input_tokens: 0`). This is the right tradeoff for V1: the
taxonomy will grow as we add niche definitions and extraction rules in V2,
and the architecture is correct from day one. Verify cache hits via
`response.usage.cache_read_input_tokens` once running against real traffic.
"""

from __future__ import annotations

import logging
from typing import Protocol

from .models import NicheFit, NicheFitBatch, Product, SalesPageSignals

logger = logging.getLogger(__name__)


# Frozen — must not contain timestamps, UUIDs, or any per-request string.
# Any byte change here invalidates the prompt cache for every downstream call.
_SALES_PAGE_SYSTEM_PROMPT = """You are an expert at analyzing Brazilian digital infoproduct sales pages.

You are given the visible text content of a sales page (Portuguese, Spanish, or English) and must extract five quality signals as scores from 0.0 to 1.0.

# The five signals

1. **scarcity** — How aggressively does the page use scarcity tactics?
   - 0.0: no scarcity language
   - 0.3: vague "limited time" mentions
   - 0.6: explicit limits ("only 50 spots", "last 12 copies")
   - 1.0: hard countdowns + named caps + repeated reminders

2. **social_proof** — How dense is the social proof?
   - 0.0: no testimonials or student counts
   - 0.3: a single testimonial or vague "thousands of students"
   - 0.6: multiple named testimonials with photos, specific student numbers
   - 1.0: video testimonials, named clients, media logos, case studies with metrics

3. **guarantee_strength** — How strong is the risk reversal?
   - 0.0: no guarantee mentioned
   - 0.3: vague "satisfaction guarantee"
   - 0.6: explicit money-back window (7/14/30 days) with clear terms
   - 1.0: long window (30+ days), unconditional, prominently displayed, "no questions asked"

4. **urgency** — How time-bound is the offer framing?
   - 0.0: no urgency
   - 0.3: generic "act now"
   - 0.6: explicit deadlines ("offer ends Friday")
   - 1.0: real countdown timer + expiring bonuses + price increase warning

5. **audience_clarity** — How clearly does the page name its target audience?
   - 0.0: generic "anyone can do this"
   - 0.3: broad demographic (e.g. "entrepreneurs")
   - 0.6: specific persona ("affiliate marketers in their first year")
   - 1.0: named pain point + named outcome + named segment ("freelance designers struggling to land $5K+ retainer clients")

# Composite

Compute `composite` as a weighted average reflecting Brazilian infoproduct conversion patterns:
- audience_clarity × 0.30
- social_proof × 0.25
- guarantee_strength × 0.20
- scarcity × 0.15
- urgency × 0.10

# Notes field

Write ONE sentence explaining the page's strongest and weakest signal. Be specific. No generic platitudes.

# Output

Return a single SalesPageSignals object. Do not include any prose outside the structured output."""


_NICHE_FIT_SYSTEM_PROMPT = """You are a senior affiliate marketing strategist for the Brazilian market.

You will be given:
1. An operator's stated niche and target audience
2. A list of candidate products to promote (each with name, category, price, commission)

Your job is to score each product's fit for the operator's niche on a 0–100 scale and explain your reasoning in one sentence.

# Scoring rubric

- **90–100**: Direct match. Product solves the exact pain point of the operator's audience at the right price point and commission level.
- **70–89**: Strong adjacent fit. Product appeals to the same audience but addresses a related (not core) need.
- **50–69**: Plausible cross-sell. Audience overlaps partially; conversion will need stronger creative work.
- **30–49**: Weak fit. Same broad market but different intent or price tier.
- **0–29**: Poor fit. Mismatched audience, intent, or price expectations.

# Reasoning rules

- One sentence per product, max 25 words.
- Name the specific match or mismatch — never "good fit" or "fits well" alone.
- Use the actual product and audience details, not boilerplate.

# Output

Return a NicheFitBatch with one NicheFit per input product. Preserve the input product_id values exactly."""


class AnthropicClientProtocol(Protocol):
    """Minimal surface the analyzer needs from the Anthropic client.

    Tests pass a fake satisfying this protocol so the suite never touches
    the network. Production passes a real `anthropic.Anthropic()`.
    """

    @property
    def messages(self):  # pragma: no cover - protocol stub
        ...


class LLMAnalyzer:
    HAIKU_MODEL = "claude-haiku-4-5"
    SONNET_MODEL = "claude-sonnet-4-6"

    def __init__(self, client: AnthropicClientProtocol) -> None:
        self._client = client

    def analyze_sales_page(
        self,
        *,
        product_name: str,
        page_text: str,
    ) -> SalesPageSignals:
        """Per-product Haiku call. Uses prompt caching on the system prompt.

        `page_text` should be visible text only — strip HTML before passing.
        Long pages should be truncated to ~8K tokens; the analyzer doesn't
        need the footer.
        """
        response = self._client.messages.parse(
            model=self.HAIKU_MODEL,
            max_tokens=2048,
            # Block-level cache_control works with both messages.create() and
            # messages.parse(); the top-level shortcut is create()-only.
            system=[
                {
                    "type": "text",
                    "text": _SALES_PAGE_SYSTEM_PROMPT,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            messages=[
                {
                    "role": "user",
                    "content": (
                        f"Product name: {product_name}\n\n"
                        f"Sales page content:\n---\n{page_text}\n---"
                    ),
                }
            ],
            output_format=SalesPageSignals,
        )
        signals: SalesPageSignals = response.parsed_output
        return signals

    def rank_by_niche_fit(
        self,
        *,
        target_niche: str,
        target_audience: str,
        products: list[Product],
    ) -> list[NicheFit]:
        """One-shot Sonnet call. Re-ranks the catalog by operator-stated niche.

        Sent as a single batch so Sonnet can reason across the whole list
        and give relative scores. The system prompt is cached so repeated
        re-rankings (e.g. operator iterating on niche wording) are cheap.
        """
        product_summaries = "\n".join(
            f"- id={p.id} | {p.name} | category={p.category or 'unknown'} | "
            f"price=R${p.price_brl or 0:.0f} | commission={p.commission_pct or 0:.0f}%"
            for p in products
        )

        user_message = (
            f"Operator's stated niche: {target_niche}\n"
            f"Operator's target audience: {target_audience}\n\n"
            f"Candidate products ({len(products)} total):\n{product_summaries}"
        )

        response = self._client.messages.parse(
            model=self.SONNET_MODEL,
            max_tokens=4096,
            system=[
                {
                    "type": "text",
                    "text": _NICHE_FIT_SYSTEM_PROMPT,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            messages=[{"role": "user", "content": user_message}],
            output_format=NicheFitBatch,
        )
        batch: NicheFitBatch = response.parsed_output
        return batch.rankings


def build_default_analyzer() -> LLMAnalyzer:
    """Real-client constructor. Imported lazily so tests don't need an API key."""
    from pathlib import Path

    import anthropic
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parents[2] / ".env", override=False)
    return LLMAnalyzer(client=anthropic.Anthropic())
