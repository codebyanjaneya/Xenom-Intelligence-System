"""Publisher page parsing, image priority/validation and Google News URL decoding (offline)."""

from __future__ import annotations

import asyncio
import base64
import json
from io import BytesIO

import httpx
import pytest
from PIL import Image

from app.services import google_news_decoder as gnd
from app.services.article_page import (
    is_acceptable_image_url,
    parse_article_html,
    pick_valid_image,
    validate_image,
)

ARTICLE_HTML = """
<html><head>
<title>Fallback title</title>
<link rel="canonical" href="/news/jcb-india-plant">
<meta property="og:title" content="OG headline">
<meta property="og:image" content="/img/og-hero.jpg">
<meta name="twitter:image" content="https://cdn.example.com/tw-hero.jpg">
<meta property="og:description" content="Short description.">
<script type="application/ld+json">
{"@context":"https://schema.org","@graph":[
 {"@type":"WebSite","name":"Example"},
 {"@type":"NewsArticle","headline":"JCB India opens new plant","datePublished":"2026-09-18T10:30:00+05:30",
  "author":{"@type":"Person","name":"A. Reporter"},
  "image":["https://cdn.example.com/ld-hero.jpg"]}]}
</script></head>
<body><nav>menu</nav><article>
<p>Advertisement</p><p>JCB India opened a new plant in Pune.</p><p>JCB India opened a new plant in Pune.</p>
<p>It will employ 500 people.</p><aside>related</aside><script>var x=1</script></article><footer>foot</footer></body></html>
"""


def test_page_fields_from_jsonld_and_meta() -> None:
    page = parse_article_html(ARTICLE_HTML, "https://example.com/x?utm=1", "https://example.com/x")
    assert page.title == "JCB India opens new plant"
    assert page.canonical_url == "https://example.com/news/jcb-india-plant"
    assert page.published_at.isoformat() == "2026-09-18T05:00:00+00:00"
    assert page.author == "A. Reporter"
    assert page.description == "Short description."
    assert page.body_text == "JCB India opened a new plant in Pune. It will employ 500 people."


def test_image_candidates_follow_spec_priority_jsonld_then_og_then_twitter() -> None:
    page = parse_article_html(ARTICLE_HTML, "https://example.com/x")
    assert [s for s, _ in page.image_candidates] == ["jsonld", "og:image", "twitter:image"]
    assert page.image_candidates[1][1] == "https://example.com/img/og-hero.jpg"


@pytest.mark.parametrize(
    "value,expected",
    [
        ("2026-09-19T7:25:22+05:30", "2026-09-19T01:55:22+00:00"),       # ET: single-digit hour
        ("2026-04-30T15:55:23&#x2B;05:30", "2026-04-30T10:25:23+00:00"),  # Devdiscourse: HTML-escaped '+'
        ("Thu, 17 Sep 2026 07:03:00 GMT", "2026-09-17T07:03:00+00:00"),
        ("garbage", None),
    ],
)
def test_real_world_date_formats_parse(value, expected) -> None:
    from app.services.article_page import _parse_dt

    got = _parse_dt(value)
    assert (got.isoformat() if got else None) == expected


def test_a_malformed_jsonld_date_does_not_hide_a_good_meta_date() -> None:
    html = ('<html><head><title>Some headline here today</title><meta property="article:published_time" content="2026-05-01T10:00:00+00:00">'
            '<script type="application/ld+json">{"@type":"NewsArticle","headline":"H","datePublished":"not a date"}</script></head><body><p>x</p></body></html>')
    assert parse_article_html(html, "https://a.com/x").published_at.isoformat() == "2026-05-01T10:00:00+00:00"


def test_page_without_structured_data_falls_back_to_og_and_title_tag() -> None:
    html = '<html><head><title>Plain title here</title><meta property="og:image" content="https://a.com/p.jpg"></head><body><p>Body.</p></body></html>'
    page = parse_article_html(html, "https://a.com/x")
    assert page.title == "Plain title here" and page.image_candidates == [("og:image", "https://a.com/p.jpg")]
    assert page.published_at is None


@pytest.mark.parametrize(
    "url,ok",
    [
        ("https://cdn.example.com/2026/09/hero.jpg", True),
        ("https://lh3.googleusercontent.com/J6_coFbogxhRI9=s0-w300", False),
        ("https://news.google.com/api/attachments/x", False),
        ("https://www.gstatic.com/images/branding/product.png", False),
        ("https://example.com/favicon.ico", False),
        ("https://example.com/assets/site-logo.png", False),
        ("https://s3-symbol-logo.tradingview.com/action-construct--600.png", False),
        ("https://example.com/pixel.gif", False),
        ("https://example.com/sprite.svg", False),
        ("https://example.com/img/placeholder.jpg", False),
        ("data:image/png;base64,AAAA", False),
        ("", False),
    ],
)
def test_image_url_rules_reject_generic_and_google_assets(url: str, ok: bool) -> None:
    assert is_acceptable_image_url(url) is ok


def _png(w: int, h: int) -> bytes:
    buf = BytesIO()
    Image.effect_noise((w, h), 80).convert("RGB").save(buf, "PNG")
    return buf.getvalue()


def _client(routes: dict[str, httpx.Response]) -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        return routes.get(str(request.url), httpx.Response(404))

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def test_image_validation_checks_reachability_type_size_and_dimensions() -> None:
    big = _png(600, 400)
    routes = {
        "https://a.com/ok.png": httpx.Response(200, content=big, headers={"content-type": "image/png"}),
        "https://a.com/html.png": httpx.Response(200, content=b"<html>", headers={"content-type": "text/html"}),
        "https://a.com/tiny.png": httpx.Response(200, content=b"x" * 100, headers={"content-type": "image/png"}),
        "https://a.com/small.png": httpx.Response(200, content=_png(40, 40) + b"0" * 5000, headers={"content-type": "image/png"}),
    }

    async def run():
        async with _client(routes) as c:
            return [
                await validate_image(u, c)
                for u in ("https://a.com/ok.png", "https://a.com/html.png", "https://a.com/tiny.png",
                          "https://a.com/small.png", "https://a.com/missing.png")
            ]

    results = asyncio.run(run())
    assert results[0] == (True, "ok")
    assert results[1][1].startswith("content-type")
    assert results[2][1].startswith("too-small")
    assert results[3][1].startswith("dimensions")
    assert results[4][1] == "http-404"


def test_pick_valid_image_skips_broken_candidates_and_keeps_priority() -> None:
    big = _png(600, 400)
    routes = {
        "https://a.com/og.png": httpx.Response(200, content=big, headers={"content-type": "image/png"}),
        "https://a.com/tw.png": httpx.Response(200, content=big, headers={"content-type": "image/png"}),
    }

    async def run():
        async with _client(routes) as c:
            return await pick_valid_image(
                [("jsonld", "https://a.com/dead.png"), ("og:image", "https://a.com/og.png"), ("twitter:image", "https://a.com/tw.png")], c
            )

    url, source, log = asyncio.run(run())
    assert (url, source) == ("https://a.com/og.png", "og:image")
    assert log == ["jsonld:http-404"]


def test_no_valid_candidate_means_no_image_not_a_placeholder() -> None:
    async def run():
        async with _client({}) as c:
            return await pick_valid_image([("og:image", "https://a.com/x.png")], c)

    assert asyncio.run(run())[0] is None


# ---- Google News wrapper decoding ------------------------------------------------------------------------------------
def _legacy_id(url: str) -> str:
    raw = b"\x08\x13\x22" + bytes([len(url)]) + url.encode() + b"\xd2\x01\x00"
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def test_legacy_ids_decode_offline() -> None:
    gid = _legacy_id("https://www.example-news.com/story/123")
    assert gnd.decode_legacy(gid) == "https://www.example-news.com/story/123"


def test_modern_ids_are_not_decoded_offline() -> None:
    assert gnd.decode_legacy("CBMipgFBVV95cUxQbmZYbjY1dVRSeVRDdDl0M3BjZHR5cFVHYkVDMm9CUm5aUUphRTdhblp4") is None


def test_article_id_and_wrapper_detection() -> None:
    url = "https://news.google.com/rss/articles/CBMiABC123?oc=5"
    assert gnd.is_google_news_url(url) and gnd.article_id(url) == "CBMiABC123"
    assert not gnd.is_google_news_url("https://www.example.com/a")
    assert asyncio.run(gnd.resolve_google_news_url("https://www.example.com/a")) == "https://www.example.com/a"


def test_modern_resolution_uses_signature_then_batchexecute() -> None:
    gnd._CACHE.clear()
    seen: dict[str, str] = {}
    page = '<html><body><c-wiz><div jscontroller="x" data-n-a-sg="SIG123" data-n-a-ts="1789000000"></div></c-wiz></body></html>'
    inner = json.dumps(["garturlreq", "https://publisher.example/real-story"])
    reply = ")]}'\n\n" + json.dumps([["wrb.fr", "Fbv4je", inner, None]])

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, text=page)
        seen["body"] = request.content.decode()
        return httpx.Response(200, text=reply)

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
            return await gnd.resolve_google_news_url("https://news.google.com/rss/articles/CBMiAU_yqLFakeModernId?oc=5", c)

    assert asyncio.run(run()) == "https://publisher.example/real-story"
    assert "SIG123" in seen["body"] and "1789000000" in seen["body"] and "CBMiAU_yqLFakeModernId" in seen["body"]


def test_failed_resolution_returns_none_never_a_google_url() -> None:
    gnd._CACHE.clear()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429)

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
            return await gnd.resolve_google_news_url("https://news.google.com/rss/articles/CBMiAU_yqLBlocked", c)

    assert asyncio.run(run()) is None
