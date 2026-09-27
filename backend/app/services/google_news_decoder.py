"""Resolve Google News RSS wrapper links to the real publisher URL.

Google News RSS ``<link>`` values look like
``https://news.google.com/rss/articles/CBMi...?oc=5``.

* Legacy ids embed the publisher URL in base64 -> decoded offline.
* Current ids (``...AU_yqL...``) need the same lookup a browser performs when the
  link is opened: read the signature/timestamp from the article page, then ask
  the ``batchexecute`` endpoint for the redirect target.

Only accepted articles are resolved (a few per run) and results are cached, so
the request volume stays small. Failure returns ``None`` -- callers must NOT
fall back to a Google-hosted URL or image.
"""

from __future__ import annotations

import base64
import json
import logging
import re
from urllib.parse import quote, urlparse

import httpx
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept-Encoding": "gzip, deflate",
}
_CACHE: dict[str, str | None] = {}


def is_google_news_url(url: str | None) -> bool:
    host = (urlparse(url or "").hostname or "").lower()
    return host == "news.google.com" or host.endswith(".news.google.com")


def article_id(url: str) -> str | None:
    match = re.search(r"/(?:rss/)?(?:articles|read)/([^/?#]+)", url)
    return match.group(1) if match else None


def decode_legacy(gid: str) -> str | None:
    """Old-style ids carry the URL directly (no ``AU_yqL`` token)."""
    try:
        raw = base64.urlsafe_b64decode(gid + "=" * (-len(gid) % 4))
    except Exception:
        return None
    text = raw.decode("latin-1", errors="ignore")
    if text.startswith("\x08\x13\x22"):
        text = text[3:]
    if text.endswith("\xd2\x01\x00"):
        text = text[:-3]
    if "AU_yqL" in text:
        return None
    match = re.search(r"https?://[^\s\x00-\x1f\x7f-\xff]+", text)
    return match.group(0) if match else None


async def _signature(client: httpx.AsyncClient, gid: str) -> tuple[str, str] | None:
    for url in (f"https://news.google.com/rss/articles/{gid}", f"https://news.google.com/articles/{gid}"):
        response = await client.get(url)
        if response.status_code != 200:
            continue
        soup = BeautifulSoup(response.text, "html.parser")
        node = soup.select_one("c-wiz > div[jscontroller]") or soup.select_one("[data-n-a-sg]")
        if node and node.get("data-n-a-sg") and node.get("data-n-a-ts"):
            return node["data-n-a-sg"], node["data-n-a-ts"]
    return None


async def _batchexecute(client: httpx.AsyncClient, gid: str, signature: str, timestamp: str) -> str | None:
    inner = json.dumps(
        [
            "garturlreq",
            [["X", "X", ["X", "X"], None, None, 1, 1, "US:en", None, 1, None, None, None, None, None, 0, 1],
             "X", "X", 1, [1, 1, 1], 1, 1, None, 0, 0, None, 0],
            gid, int(timestamp), signature,
        ]
    )
    payload = json.dumps([[["Fbv4je", inner]]])
    response = await client.post(
        "https://news.google.com/_/DotsSplashUi/data/batchexecute",
        content="f.req=" + quote(payload),
        headers={"Content-Type": "application/x-www-form-urlencoded;charset=UTF-8"},
    )
    if response.status_code != 200:
        raise httpx.HTTPStatusError(f"batchexecute HTTP {response.status_code}", request=response.request, response=response)
    body = response.text.split("\n\n", 1)[-1]
    outer = json.loads(body)
    decoded = json.loads(outer[0][2])[1]
    return decoded if isinstance(decoded, str) and decoded.startswith("http") else None


async def resolve_google_news_url(url: str, client: httpx.AsyncClient | None = None) -> str | None:
    """Real publisher URL for a Google News link, or ``None`` if it cannot be resolved."""
    if not is_google_news_url(url):
        return url
    gid = article_id(url)
    if not gid:
        return None
    if gid in _CACHE:
        return _CACHE[gid]

    legacy = decode_legacy(gid)
    if legacy and not is_google_news_url(legacy):
        _CACHE[gid] = legacy
        return legacy

    own = client is None
    client = client or httpx.AsyncClient(timeout=15, follow_redirects=True, headers=_HEADERS)
    try:
        sig = await _signature(client, gid)
        if not sig:
            logger.warning("google_news_decode: no signature for %s", gid[:24])
            return None
        result = await _batchexecute(client, gid, *sig)
        if result and not is_google_news_url(result):
            _CACHE[gid] = result
            return result
        return None
    except Exception as exc:
        logger.warning("google_news_decode failed for %s: %s: %s", gid[:24], type(exc).__name__, str(exc)[:100])
        return None
    finally:
        if own:
            await client.aclose()
