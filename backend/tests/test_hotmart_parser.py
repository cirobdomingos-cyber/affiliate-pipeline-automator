"""Selector-tolerance tests for the Hotmart HTML parser.

These are deliberately small — they verify the parser handles a known-good
shape and degrades gracefully on malformed input. End-to-end parsing of real
Hotmart HTML is left to V1 once we capture a stable fixture from the live
site.
"""

from __future__ import annotations

from backend.app.scrapers.hotmart import parse_marketplace_html


SAMPLE_HTML = """
<html><body>
<article class="product-card" data-test="product-card">
  <a href="/pt-br/marketplace/produtos/curso-x/X1234">
    <h3 class="product-name">Curso de Tráfego Pago</h3>
  </a>
  <span class="price" data-test="price">R$ 497,00</span>
  <span class="commission" data-test="commission">R$ 60,00</span>
  <span class="heat" data-test="heat">82</span>
  <span class="category" data-test="category">Marketing Digital</span>
  <span class="producer" data-test="producer">João Silva</span>
</article>
<article class="product-card" data-test="product-card">
  <a href="https://hotmart.com/pt-br/marketplace/produtos/curso-y/Y9876">
    <h3 class="product-name">Curso de Investimentos</h3>
  </a>
  <span class="price">R$ 997,00</span>
  <span class="category">Finanças</span>
</article>
</body></html>
"""


def test_parses_well_formed_cards():
    products = parse_marketplace_html(SAMPLE_HTML)
    assert len(products) == 2

    p1 = products[0]
    assert p1.name == "Curso de Tráfego Pago"
    assert p1.url.startswith("https://hotmart.com")
    assert p1.external_id == "X1234"
    assert p1.price_brl == 497.00
    assert p1.popularity == 82.0
    assert p1.producer_name == "João Silva"
    assert p1.niche.value == "digital_marketing"


def test_parses_card_with_missing_optional_fields():
    products = parse_marketplace_html(SAMPLE_HTML)
    p2 = products[1]
    assert p2.name == "Curso de Investimentos"
    assert p2.popularity is None
    assert p2.producer_name is None
    assert p2.niche.value == "finance"


def test_empty_html_returns_empty_list():
    assert parse_marketplace_html("<html><body>nothing here</body></html>") == []


def test_limit_caps_results():
    products = parse_marketplace_html(SAMPLE_HTML, limit=1)
    assert len(products) == 1
