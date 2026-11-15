"""Regression tests for outbound-fetch SSRF protection (app/core/net_safety.py) and the fact-check fallback verdict.

All tests are offline: DNS is answered by an injected resolver and HTTP by httpx.MockTransport. Where a guarded
client uses the real network transport, the guard must refuse the request before any connection is attempted.
"""
from __future__ import annotations

import asyncio
import uuid

import httpx
import pytest

from app.adapters.ingestion.base import AdapterError
from app.adapters.ingestion.rss import RssHttpAdapter
from app.adapters.ingestion.site_adapters import render_html
from app.core import net_safety
from app.core.net_safety import (BlockedURLError, ResponseTooLargeError, assert_public_url, get_limited,
                                 is_public_address, safe_client)
from app.services import llm
from app.services.article_page import fetch_article_page

PUBLIC_IP = "93.184.216.34"
DNS = {
    "public.example": [PUBLIC_IP],
    "internal.example": ["10.0.0.5"],
    "metadata.example": ["169.254.169.254"],
    "mixed.example": [PUBLIC_IP, "127.0.0.1"],
    "mapped.example": ["::ffff:127.0.0.1"],
}


@pytest.fixture(autouse=True)
def fake_dns():
    async def resolve(host: str, port: int) -> list[str]:
        if host not in DNS:
            raise OSError(f"NXDOMAIN {host}")
        return DNS[host]

    net_safety.set_resolver(resolve)
    yield
    net_safety.set_resolver(None)


def run(coro):
    return asyncio.run(coro)


@pytest.mark.parametrize("address", [
    "127.0.0.1", "10.1.2.3", "172.16.0.9", "192.168.1.1", "169.254.169.254", "100.64.0.1", "0.0.0.0",
    "224.0.0.1", "255.255.255.255", "::1", "fe80::1", "fc00::1", "::ffff:10.0.0.1", "2002:7f00:1::1", "::",
])
def test_non_public_addresses_are_refused(address: str) -> None:
    assert is_public_address(address) is False


@pytest.mark.parametrize("address", [PUBLIC_IP, "8.8.8.8", "2606:4700:4700::1111"])
def test_public_addresses_are_allowed(address: str) -> None:
    assert is_public_address(address) is True


@pytest.mark.parametrize("url", [
    "file:///etc/passwd", "gopher://public.example/", "ftp://public.example/x", "http:///nohost",
    "http://127.0.0.1:8000/admin", "http://[::1]/", "http://169.254.169.254/latest/meta-data/",
    "http://internal.example/", "http://metadata.example/", "http://mixed.example/", "http://mapped.example/",
])
def test_unsafe_urls_are_blocked(url: str) -> None:
    with pytest.raises(BlockedURLError):
        run(assert_public_url(url))


def test_public_url_passes_and_unknown_host_is_a_connect_error_not_a_bypass() -> None:
    run(assert_public_url("https://public.example/article"))
    with pytest.raises(httpx.ConnectError):
        run(assert_public_url("https://does-not-exist.example/"))


def test_redirect_to_private_address_is_blocked_before_it_is_requested() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        if request.url.host == "public.example":
            return httpx.Response(302, headers={"location": "http://169.254.169.254/latest/meta-data/"})
        return httpx.Response(200, text="secret")

    async def go():
        async with safe_client(transport=httpx.MockTransport(handler), follow_redirects=True) as client:
            await client.get("http://public.example/start")

    with pytest.raises(BlockedURLError):
        run(go())
    assert seen == ["http://public.example/start"]  # the metadata endpoint was never contacted


def test_redirect_chain_between_public_hosts_still_works() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/start":
            return httpx.Response(301, headers={"location": "http://public.example/final"})
        return httpx.Response(200, text="ok")

    async def go():
        async with safe_client(transport=httpx.MockTransport(handler), follow_redirects=True) as client:
            return await client.get("http://public.example/start")

    response = run(go())
    assert response.status_code == 200 and str(response.url) == "http://public.example/final"


def test_response_size_limit_by_declared_length_and_by_streamed_bytes() -> None:
    def declared(request):
        return httpx.Response(200, headers={"content-length": "999999"}, content=b"x" * 10)

    def streamed(request):
        return httpx.Response(200, content=b"y" * 5000)

    def small(request):
        return httpx.Response(200, headers={"content-type": "text/html; charset=utf-8"}, content="<p>héllo</p>".encode())

    async def get(handler, limit):
        async with safe_client(transport=httpx.MockTransport(handler)) as client:
            return await get_limited(client, "http://public.example/", max_bytes=limit)

    with pytest.raises(ResponseTooLargeError):
        run(get(declared, 1000))
    with pytest.raises(ResponseTooLargeError):
        run(get(streamed, 1000))
    ok = run(get(small, 1000))
    assert ok.status_code == 200 and ok.text == "<p>héllo</p>"


def test_fetch_article_page_refuses_internal_targets_without_any_network_call() -> None:
    for url in ("http://127.0.0.1:5432/", "http://internal.example/admin", "http://169.254.169.254/latest/meta-data/"):
        page = run(fetch_article_page(url))
        assert page.error and "BlockedURLError" in page.error, (url, page.error)
        assert page.title is None


def test_rss_adapter_refuses_internal_feed_immediately() -> None:
    with pytest.raises(AdapterError, match="refused"):
        run(RssHttpAdapter().fetch("http://internal.example/feed.xml", source_id=uuid.uuid4()))


def test_render_html_refuses_internal_url_before_launching_a_browser() -> None:
    with pytest.raises(AdapterError, match="refusing to render"):
        run(render_html("http://127.0.0.1:8000/"))


def test_fact_check_url_input_cannot_reach_the_metadata_endpoint(viewer_client) -> None:
    response = viewer_client.post("/engine/fact-check", json={"input": "http://169.254.169.254/latest/meta-data/iam"})
    assert response.status_code == 200
    body = response.json()
    assert body["external_source"]["fetch_status"] == "error"
    assert "non-public" in body["external_source"]["error"]
    assert body["external_source"]["title"] is None


def test_fact_check_input_length_is_bounded(viewer_client) -> None:
    assert viewer_client.post("/engine/fact-check", json={"input": "x" * 4001}).status_code == 422


def test_fact_check_without_llm_never_claims_likely_real_from_a_keyword_match(viewer_client, db_engine, monkeypatch) -> None:
    from app.services import fact_check
    from tests.test_article_search import _seed

    async def no_external(query: str):
        return []

    monkeypatch.setattr(fact_check, "_external_coverage", no_external)
    llm.set_provider(None)
    _seed(db_engine, [{"title": "JCB opens new excavator plant in Vadodara", "summary": "JCB expands manufacturing.",
                       "entity": "JCB", "source": "Trade Daily"}])
    body = viewer_client.post("/engine/fact-check", json={"input": "JCB opens new excavator plant"}).json()
    assert body["matched_coverage"], "the stored article should match lexically"
    assert body["llm_used"] is False
    assert body["verdict"] == "UNVERIFIED"
    assert "not corroboration" in body["reasoning"]
