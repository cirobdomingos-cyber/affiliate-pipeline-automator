"""UTM builder tests — pure functions, millisecond-fast."""

from __future__ import annotations

from urllib.parse import parse_qs, urlparse

import pytest

from backend.app.models import AffiliateLink, LinkStatus, Platform, UTMParams
from backend.app.utm import build_tracked_url, normalize_utm_value, track


class TestNormalizeUtmValue:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("Instagram Bio", "instagram-bio"),
            ("instagram-bio", "instagram-bio"),
            ("  Paid_Search  ", "paid-search"),
            ("CPC", "cpc"),
            ("Meta Ads — Campaign 1", "meta-ads-—-campaign-1"),
            ("black friday 2026", "black-friday-2026"),
            ("multiple   spaces", "multiple-spaces"),
            ("a__b__c", "a-b-c"),
        ],
    )
    def test_normalizes_common_inputs(self, raw, expected):
        assert normalize_utm_value(raw) == expected


class TestBuildTrackedUrl:
    def _utm(self, **overrides) -> UTMParams:
        base = dict(source="instagram", medium="organic", campaign="bio-link")
        base.update(overrides)
        return UTMParams(**base)

    def test_appends_utm_to_simple_url(self):
        url = build_tracked_url("https://hotmart.com/pt-br/produto/XYZ", self._utm())
        parsed = urlparse(url)
        params = parse_qs(parsed.query)
        assert params["utm_source"] == ["instagram"]
        assert params["utm_medium"] == ["organic"]
        assert params["utm_campaign"] == ["bio-link"]

    def test_preserves_existing_query_params(self):
        url = build_tracked_url(
            "https://hotmart.com/produto/XYZ?aff=abc123&src=partner",
            self._utm(),
        )
        params = parse_qs(urlparse(url).query)
        assert params["aff"] == ["abc123"]
        assert params["src"] == ["partner"]
        assert params["utm_source"] == ["instagram"]

    def test_existing_param_order_is_preserved(self):
        url = build_tracked_url(
            "https://example.com/?a=1&b=2&c=3",
            self._utm(),
        )
        query = urlparse(url).query
        # Check params appear in order: a, b, c, then utm_*
        assert query.index("a=1") < query.index("b=2") < query.index("c=3")
        assert query.index("c=3") < query.index("utm_source=instagram")

    def test_utm_params_in_fixed_order(self):
        url = build_tracked_url(
            "https://example.com/",
            UTMParams(
                source="google",
                medium="cpc",
                campaign="launch",
                term="affiliate-marketing",
                content="ad-variant-a",
            ),
        )
        query = urlparse(url).query
        assert (
            query.index("utm_source")
            < query.index("utm_medium")
            < query.index("utm_campaign")
            < query.index("utm_term")
            < query.index("utm_content")
        )

    def test_utm_overrides_existing_param_of_same_name(self):
        url = build_tracked_url(
            "https://example.com/?utm_source=old",
            self._utm(source="new"),
        )
        params = parse_qs(urlparse(url).query)
        assert params["utm_source"] == ["new"]

    def test_normalizes_utm_values(self):
        url = build_tracked_url(
            "https://example.com/",
            UTMParams(source="Instagram Bio", medium="Organic", campaign="Black Friday 2026"),
        )
        params = parse_qs(urlparse(url).query)
        assert params["utm_source"] == ["instagram-bio"]
        assert params["utm_medium"] == ["organic"]
        assert params["utm_campaign"] == ["black-friday-2026"]

    def test_preserves_fragment(self):
        url = build_tracked_url("https://example.com/page#section-2", self._utm())
        assert urlparse(url).fragment == "section-2"

    def test_optional_term_and_content_omitted_when_none(self):
        url = build_tracked_url("https://example.com/", self._utm())
        params = parse_qs(urlparse(url).query)
        assert "utm_term" not in params
        assert "utm_content" not in params

    def test_deterministic_output(self):
        url1 = build_tracked_url("https://example.com/?a=1&b=2", self._utm())
        url2 = build_tracked_url("https://example.com/?a=1&b=2", self._utm())
        assert url1 == url2

    def test_url_encoding_applied(self):
        url = build_tracked_url(
            "https://example.com/",
            UTMParams(source="instagram", medium="organic", campaign="promo&sale"),
        )
        # & inside the value must be encoded
        assert "promo%26sale" in url


class TestTrackConvenience:
    def test_returns_tracked_link_model(self):
        link = AffiliateLink(
            id="abc123",
            platform=Platform.HOTMART,
            label="Test",
            raw_url="https://hotmart.com/produto/XYZ",
            approval_status=LinkStatus.APPROVED,
        )
        utm = UTMParams(source="instagram", medium="organic", campaign="bio")
        tracked = track(link, utm)
        assert tracked.affiliate_link_id == "abc123"
        assert "utm_source=instagram" in tracked.final_url
        assert tracked.utm is utm
