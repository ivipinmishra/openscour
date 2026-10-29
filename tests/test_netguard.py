import ipaddress
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import httpx
import pytest

from openscour.server.netguard import guarded_transport, is_public_address


@pytest.mark.parametrize(
    "address,public",
    [
        ("93.184.215.14", True),
        ("2606:4700::1111", True),
        ("127.0.0.1", False),
        ("10.1.2.3", False),
        ("172.16.0.1", False),
        ("192.168.1.1", False),
        ("169.254.169.254", False),  # cloud metadata service
        ("100.64.0.1", False),  # carrier-grade NAT
        ("0.0.0.0", False),
        ("224.0.0.1", False),  # multicast
        ("::1", False),
        ("fd00:ec2::254", False),  # AWS metadata over IPv6
        ("fe80::1", False),
        ("::ffff:127.0.0.1", False),  # IPv4-mapped loopback
    ],
)
def test_is_public_address(address, public):
    assert is_public_address(ipaddress.ip_address(address)) is public


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:9/",
        "http://localhost:9/",
        "http://169.254.169.254/latest/meta-data/",
        "http://[::1]:9/",
        "http://2130706433:9/",  # 127.0.0.1 written as a number
    ],
)
async def test_guarded_client_refuses_private_destinations(url):
    async with httpx.AsyncClient(transport=guarded_transport(), trust_env=False) as client:
        with pytest.raises(httpx.ConnectError, match="blocked"):
            await client.get(url, timeout=5)


class _Redirect(BaseHTTPRequestHandler):
    target = ""

    def do_GET(self):
        self.send_response(302)
        self.send_header("Location", self.target)
        self.end_headers()

    def log_message(self, *args):
        pass


async def test_redirect_to_a_private_address_is_refused():
    """The check runs on every connection, so a redirect can't reach a blocked address.

    The test server's own port stands in for a public site; every other local
    port is treated as private.
    """
    server = HTTPServer(("127.0.0.1", 0), _Redirect)
    allowed_port = server.server_address[1]
    _Redirect.target = "http://127.0.0.1:9/secret"
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        policy = lambda ip, port: port == allowed_port  # noqa: E731
        transport = guarded_transport(policy=policy)
        async with httpx.AsyncClient(transport=transport, trust_env=False, follow_redirects=True) as client:
            with pytest.raises(httpx.ConnectError, match="blocked"):
                await client.get(f"http://127.0.0.1:{allowed_port}/", timeout=5)
            # Without following redirects, the first hop itself is allowed.
            first = await client.get(f"http://127.0.0.1:{allowed_port}/", timeout=5, follow_redirects=False)
            assert first.status_code == 302
    finally:
        server.shutdown()
