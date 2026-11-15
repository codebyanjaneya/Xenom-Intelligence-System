"""Outbound HTTP safety for URLs that come from users, feeds or configured sources (SSRF protection).

Every request sent through a :class:`GuardedTransport` is checked before it leaves the process:

* only ``http`` / ``https`` with a hostname;
* the hostname is resolved and EVERY resolved address must be globally routable. Loopback, private (RFC 1918),
  link-local (incl. the 169.254.169.254 cloud metadata endpoint), CGNAT, multicast, reserved and unspecified
  addresses are refused, including IPv4-mapped / 6to4-style IPv6 forms of them;
* redirects are followed by httpx as new requests through the same transport, so every hop is re-validated.

``get_limited`` additionally caps the response body so a hostile or broken server cannot exhaust memory.

Blocked requests raise :class:`BlockedURLError`, a subclass of ``httpx.RequestError``, so existing network-error
handling treats them as a failed fetch (reported, never silent) without new code paths.

Known limitation: the address is resolved here and again by the connection layer, so a DNS-rebinding server with a
near-zero TTL could still answer differently between the two lookups. Pinning the connection to the checked address
would close that gap; it is not done here because it breaks TLS SNI/certificate checks with plain httpx.
"""
from __future__ import annotations

import asyncio
import ipaddress
import socket
from collections.abc import Awaitable, Callable
from urllib.parse import urlsplit

import httpx

__all__ = [
    "BlockedURLError",
    "ResponseTooLargeError",
    "GuardedTransport",
    "assert_public_url",
    "get_limited",
    "is_public_address",
    "safe_client",
    "set_resolver",
    "DEFAULT_MAX_BYTES",
]

DEFAULT_MAX_BYTES = 5 * 1024 * 1024  # 5 MiB: generous for an HTML article page or a feed
DEFAULT_MAX_REDIRECTS = 5
_ALLOWED_SCHEMES = {"http", "https"}

Resolver = Callable[[str, int], Awaitable[list[str]]]


class BlockedURLError(httpx.RequestError):
    """The URL (or a redirect target) points at a non-public address or uses a forbidden scheme."""


class ResponseTooLargeError(httpx.RequestError):
    """The response body exceeded the configured size limit."""


async def _system_resolver(host: str, port: int) -> list[str]:
    loop = asyncio.get_running_loop()
    infos = await loop.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return sorted({info[4][0] for info in infos})


_resolver: Resolver = _system_resolver


def set_resolver(resolver: Resolver | None) -> None:
    """Dependency-injection point for tests (offline DNS). ``None`` restores the system resolver."""
    global _resolver
    _resolver = resolver or _system_resolver


def is_public_address(value: str) -> bool:
    """True only for globally routable unicast addresses."""
    try:
        ip = ipaddress.ip_address(value.split("%", 1)[0])  # drop an IPv6 zone id
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address):
        if ip.ipv4_mapped is not None:
            return is_public_address(str(ip.ipv4_mapped))
        if ip.sixtofour is not None:
            return is_public_address(str(ip.sixtofour))
        if ip.teredo is not None:
            return False
    return ip.is_global and not ip.is_multicast and not ip.is_reserved and not ip.is_unspecified


async def assert_public_url(url: str, request: httpx.Request | None = None) -> None:
    """Raise :class:`BlockedURLError` unless ``url`` is http(s) and every address its host resolves to is public."""
    if request is None and _parsable(url):
        request = httpx.Request("GET", url)
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError as exc:
        raise BlockedURLError(f"malformed URL: {exc}", request=request) from exc
    scheme = (parts.scheme or "").lower()
    if scheme not in _ALLOWED_SCHEMES:
        raise BlockedURLError(f"scheme '{scheme or 'none'}' is not allowed", request=request)
    host = parts.hostname
    if not host:
        raise BlockedURLError("URL has no host", request=request)
    port = port or (443 if scheme == "https" else 80)
    try:
        ipaddress.ip_address(host.split("%", 1)[0])
        addresses = [host]
    except ValueError:
        try:
            addresses = await _resolver(host, port)
        except (OSError, UnicodeError) as exc:
            raise httpx.ConnectError(f"could not resolve host {host}", request=request) from exc
    if not addresses:
        raise httpx.ConnectError(f"could not resolve host {host}", request=request)
    blocked = [a for a in addresses if not is_public_address(a)]
    if blocked:
        raise BlockedURLError(f"host {host} resolves to a non-public address", request=request)


def _parsable(url: str) -> bool:
    try:
        httpx.URL(url)
        return True
    except Exception:  # noqa: BLE001 - only used to decide whether a Request object can be built
        return False


class GuardedTransport(httpx.AsyncBaseTransport):
    """Validates the destination of every request (including each redirect hop) before delegating."""

    def __init__(self, inner: httpx.AsyncBaseTransport | None = None) -> None:
        self._inner = inner or httpx.AsyncHTTPTransport()

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        await assert_public_url(str(request.url), request)
        return await self._inner.handle_async_request(request)

    async def aclose(self) -> None:
        await self._inner.aclose()


def safe_client(*, transport: httpx.AsyncBaseTransport | None = None, **kwargs) -> httpx.AsyncClient:
    """An ``httpx.AsyncClient`` whose every request goes through :class:`GuardedTransport`.

    ``transport`` (if given) is wrapped, not replaced, so the guard also applies to an injected transport."""
    kwargs.setdefault("max_redirects", DEFAULT_MAX_REDIRECTS)
    return httpx.AsyncClient(transport=GuardedTransport(transport), **kwargs)


async def get_limited(client: httpx.AsyncClient, url: str, *, max_bytes: int = DEFAULT_MAX_BYTES, **kwargs) -> httpx.Response:
    """GET ``url`` and buffer at most ``max_bytes`` of (decoded) body; raise :class:`ResponseTooLargeError` beyond that.

    The returned response is fully read and carries the final URL (after redirects) as ``response.url``."""
    async with client.stream("GET", url, **kwargs) as response:
        declared = response.headers.get("content-length")
        if declared and declared.isdigit() and int(declared) > max_bytes:
            raise ResponseTooLargeError(f"response declares {declared} bytes (limit {max_bytes})", request=response.request)
        chunks: list[bytes] = []
        total = 0
        async for chunk in response.aiter_bytes():
            total += len(chunk)
            if total > max_bytes:
                raise ResponseTooLargeError(f"response exceeded {max_bytes} bytes", request=response.request)
            chunks.append(chunk)
    # Body is already decoded: drop transfer headers so the new Response does not try to decode it again.
    headers = [(k, v) for k, v in response.headers.multi_items() if k.lower() not in {"content-encoding", "content-length", "transfer-encoding"}]
    return httpx.Response(response.status_code, headers=headers, content=b"".join(chunks), request=response.request)
