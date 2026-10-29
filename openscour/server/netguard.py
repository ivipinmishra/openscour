"""Server-side request forgery (SSRF) protection for page fetching.

/extract and /search fetch URLs chosen by callers or found on the web. Without
a guard, a URL such as http://169.254.169.254/ (cloud instance credentials) or
http://localhost:8888 would make the server read its own private network.

The check runs where connections are opened, on the IP address actually being
connected to. That covers every redirect hop and hostnames whose DNS points at
a private address, which checking the URL string alone would miss.
"""

from __future__ import annotations

import asyncio
import ipaddress
import socket
import typing

import httpcore
import httpx

IPAddress = ipaddress.IPv4Address | ipaddress.IPv6Address
# Decides whether a resolved (address, port) may be connected to.
AddressPolicy = typing.Callable[[IPAddress, int], bool]


class BlockedAddressError(httpcore.ConnectError):
    """The destination resolves to an address that pages may not be fetched from."""


def is_public_address(ip: IPAddress) -> bool:
    """True for globally routable unicast addresses.

    Rejects loopback, private (10/8, 172.16/12, 192.168/16, fc00::/7), link-local
    (169.254/16, which includes cloud metadata services), carrier-grade NAT,
    multicast, reserved and unspecified addresses.
    """
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return ip.is_global and not ip.is_multicast


def public_only(ip: IPAddress, port: int) -> bool:
    return is_public_address(ip)


async def resolve_allowed(host: str, port: int, policy: AddressPolicy = public_only) -> list[str]:
    """Resolve host and return its addresses, or raise if any of them is not public.

    All addresses must be public, so a hostname can't mix a public address with
    a private one and hope the private one gets picked.
    """
    try:
        infos = await asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror as e:
        raise httpcore.ConnectError(f"could not resolve {host}: {e}") from e
    addresses = list(dict.fromkeys(info[4][0] for info in infos))
    for address in addresses:
        if not policy(ipaddress.ip_address(address.split("%")[0]), port):
            raise BlockedAddressError(f"blocked: {host} resolves to non-public address {address}")
    return addresses


class GuardedBackend(httpcore.AsyncNetworkBackend):
    """Network backend that only opens TCP connections to public addresses."""

    def __init__(self, policy: AddressPolicy = public_only) -> None:
        self._inner = httpcore.AnyIOBackend()
        self._policy = policy

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: typing.Iterable[httpcore.SOCKET_OPTION] | None = None,
    ) -> httpcore.AsyncNetworkStream:
        addresses = await resolve_allowed(host, port, self._policy)
        last_error: Exception | None = None
        # Connect to the address that was checked, not to the name: resolving the
        # name again could return a different (private) address.
        for address in addresses:
            try:
                return await self._inner.connect_tcp(address, port, timeout, local_address, socket_options)
            except httpcore.ConnectError as e:
                last_error = e
        raise last_error or httpcore.ConnectError(f"could not connect to {host}")

    async def connect_unix_socket(self, *args: typing.Any, **kwargs: typing.Any) -> httpcore.AsyncNetworkStream:
        raise BlockedAddressError("blocked: unix sockets are not allowed")

    async def sleep(self, seconds: float) -> None:
        await self._inner.sleep(seconds)


def guarded_transport(max_connections: int = 50, policy: AddressPolicy = public_only) -> httpx.AsyncHTTPTransport:
    """An httpx transport whose connections can only reach public addresses.

    httpx doesn't accept a network backend directly, so the transport's
    connection pool is replaced with one that uses GuardedBackend. TLS still
    verifies certificates against the original hostname.
    """
    transport = httpx.AsyncHTTPTransport()
    transport._pool = httpcore.AsyncConnectionPool(
        ssl_context=httpx.create_ssl_context(),
        max_connections=max_connections,
        network_backend=GuardedBackend(policy),
    )
    return transport
