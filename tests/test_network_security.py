"""Behavioral outbound-policy regressions; no external requests are made."""

from __future__ import annotations

import socket
import ssl
import urllib.request
from unittest.mock import Mock

import pytest

from bridge import network_security as network


@pytest.fixture(autouse=True)
def empty_provider_policy(monkeypatch):
    monkeypatch.delenv("SILLYTAVERN_PROVIDER_ALLOWED_HOSTS", raising=False)
    monkeypatch.delenv("SILLYTAVERN_PROVIDER_PRIVATE_HOSTS", raising=False)


def test_external_requires_explicit_allowlist():
    with pytest.raises(RuntimeError, match="ALLOWED_HOSTS"):
        network.validate_provider_endpoint("https://provider.example/v1")


def test_exact_allowlist_does_not_authorize_subdomains(monkeypatch):
    monkeypatch.setenv("SILLYTAVERN_PROVIDER_ALLOWED_HOSTS", "provider.example")
    network.validate_provider_endpoint("https://provider.example/v1")
    with pytest.raises(RuntimeError, match="ALLOWED_HOSTS"):
        network.validate_provider_endpoint("https://provider.example.attacker.test/v1")


@pytest.mark.parametrize(
    "url",
    [
        "https://user:secret@provider.example/v1",
        "https://provider.example:0/v1",
        "https://provider.example:65536/v1",
        "https://provider.example/\nsecret",
        "file:///tmp/provider",
        "https://[fe80::1%25eth0]/v1",
    ],
)
def test_malformed_or_credential_bearing_url_is_rejected(url, monkeypatch):
    monkeypatch.setenv("SILLYTAVERN_PROVIDER_ALLOWED_HOSTS", "provider.example,fe80::1")
    with pytest.raises(RuntimeError):
        network.validate_provider_endpoint(url)


@pytest.mark.parametrize("url", ["http://127.0.0.2:8000", "http://[::1]:8000", "http://localhost:8000"])
def test_real_loopback_range_is_allowed(url):
    network.validate_provider_endpoint(url)


@pytest.mark.parametrize("address", ["169.254.169.254", "0.0.0.0", "224.0.0.1", "::", "fe80::1", "ff02::1"])  # noqa: S104 -- rejected-address fixture; no socket is bound
def test_metadata_and_unroutable_addresses_never_allowed(address, monkeypatch):
    monkeypatch.setenv("SILLYTAVERN_PROVIDER_ALLOWED_HOSTS", address)
    monkeypatch.setenv("SILLYTAVERN_PROVIDER_PRIVATE_HOSTS", address)
    authority = f"[{address}]" if ":" in address else address
    with pytest.raises(RuntimeError):
        network.validate_provider_endpoint(f"https://{authority}/v1")


def test_private_literal_requires_separate_opt_in(monkeypatch):
    monkeypatch.setenv("SILLYTAVERN_PROVIDER_ALLOWED_HOSTS", "10.0.0.8")
    with pytest.raises(RuntimeError, match="PRIVATE_HOSTS"):
        network.validate_provider_endpoint("https://10.0.0.8/v1")
    monkeypatch.setenv("SILLYTAVERN_PROVIDER_PRIVATE_HOSTS", "10.0.0.8")
    network.validate_provider_endpoint("https://10.0.0.8/v1")


def test_private_dns_requires_opt_in(monkeypatch):
    monkeypatch.setenv("SILLYTAVERN_PROVIDER_ALLOWED_HOSTS", "provider.example")
    monkeypatch.setattr(
        socket, "getaddrinfo", lambda *args, **kwargs: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.8", 443))]
    )
    policy = network.EndpointPolicy.from_environment()
    with pytest.raises(RuntimeError, match="PRIVATE_HOSTS"):
        network.resolve_endpoint_addresses("provider.example", 443, policy)


def test_mixed_public_private_dns_is_rejected_before_connect(monkeypatch):
    monkeypatch.setenv("SILLYTAVERN_PROVIDER_ALLOWED_HOSTS", "provider.example")
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *args, **kwargs: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 443)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("169.254.169.254", 443)),
        ],
    )
    with pytest.raises(RuntimeError):
        network.resolve_endpoint_addresses("provider.example", 443, network.EndpointPolicy.from_environment())


def test_dns_cannot_rebind_loopback_hostname(monkeypatch):
    monkeypatch.setattr(
        socket, "getaddrinfo", lambda *args, **kwargs: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 80))]
    )
    with pytest.raises(RuntimeError, match="loopback"):
        network.resolve_endpoint_addresses("localhost", 80, network.EndpointPolicy.from_environment())


def test_dns_socket_address_is_pinned(monkeypatch):
    monkeypatch.setenv("SILLYTAVERN_PROVIDER_ALLOWED_HOSTS", "provider.example")
    resolver = Mock(return_value=[(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 443))])
    sock = Mock()
    monkeypatch.setattr(socket, "getaddrinfo", resolver)
    monkeypatch.setattr(socket, "socket", Mock(return_value=sock))
    monkeypatch.setattr(socket, "create_connection", Mock(side_effect=AssertionError("second DNS lookup")))
    connection = network.PinnedHTTPSConnection(
        "provider.example", timeout=2, policy=network.EndpointPolicy.from_environment()
    )
    context = Mock(wrap_socket=Mock(return_value=sock))
    connection._context = context
    connection.connect()
    resolver.assert_called_once()
    sock.connect.assert_called_once_with(("8.8.8.8", 443))
    context.wrap_socket.assert_called_once_with(sock, server_hostname="provider.example")


def test_tls_verification_remains_enabled(monkeypatch):
    monkeypatch.setenv("SILLYTAVERN_PROVIDER_ALLOWED_HOSTS", "provider.example")
    conn = network.PinnedHTTPSConnection(
        "provider.example", timeout=2, policy=network.EndpointPolicy.from_environment()
    )
    assert conn._context.verify_mode == ssl.CERT_REQUIRED
    assert conn._context.check_hostname is True
    conn.close()


@pytest.mark.parametrize(
    "target", ["https://other.example/v1", "https://provider.example:8443/v1", "http://provider.example/v1"]
)
def test_redirect_cannot_change_origin(target, monkeypatch):
    monkeypatch.setenv("SILLYTAVERN_PROVIDER_ALLOWED_HOSTS", "provider.example,other.example")
    handler = network.PolicyRedirectHandler(network.EndpointPolicy.from_environment())
    with pytest.raises(RuntimeError):
        handler.redirect_request(
            urllib.request.Request("https://provider.example/v1", headers={"Authorization": "secret"}),
            None,
            302,
            "found",
            {},
            target,
        )


def test_same_origin_redirect_retains_policy(monkeypatch):
    monkeypatch.setenv("SILLYTAVERN_PROVIDER_ALLOWED_HOSTS", "provider.example")
    handler = network.PolicyRedirectHandler(network.EndpointPolicy.from_environment())
    result = handler.redirect_request(
        urllib.request.Request("https://provider.example/v1"), None, 302, "found", {}, "https://provider.example/v2"
    )
    assert result.full_url == "https://provider.example/v2"


def test_inherited_proxy_ignored(monkeypatch):
    monkeypatch.setenv("HTTPS_PROXY", "http://untrusted.proxy.test:9000")
    opener = network.build_policy_opener(network.EndpointPolicy.from_environment())
    assert not any(isinstance(handler, urllib.request.ProxyHandler) and handler.proxies for handler in opener.handlers)


def test_preconfigured_request_proxy_rejected(monkeypatch):
    monkeypatch.setenv("SILLYTAVERN_PROVIDER_ALLOWED_HOSTS", "provider.example")
    monkeypatch.setattr(socket, "getaddrinfo", Mock(side_effect=AssertionError("proxy must be rejected before DNS")))
    request = urllib.request.Request("https://provider.example/v1")
    request.set_proxy("proxy.example:443", "https")
    with pytest.raises(RuntimeError, match="proxy"):
        network.strict_urlopen(request, timeout=1)


def test_pinned_connection_refuses_untrusted_host_before_dns(monkeypatch):
    resolver = Mock(side_effect=AssertionError("untrusted host must not resolve"))
    monkeypatch.setattr(socket, "getaddrinfo", resolver)
    with pytest.raises(RuntimeError, match="ALLOWED_HOSTS"):
        network.resolve_endpoint_addresses("untrusted.example", 443, network.EndpointPolicy.from_environment())
    resolver.assert_not_called()


def test_loopback_http_works_despite_environment_proxy(monkeypatch):
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from threading import Thread

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Length", "2")
            self.end_headers()
            self.wfile.write(b"OK")

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setenv("HTTP_PROXY", "http://invalid.proxy.test:9")
    try:
        request = urllib.request.Request(f"http://127.0.0.1:{server.server_port}/")
        with network.strict_urlopen(request, timeout=2) as response:
            assert response.read() == b"OK"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
    assert not thread.is_alive()


def test_policy_opener_rejects_non_http_schemes(tmp_path):
    document = tmp_path / "private.txt"
    document.write_text("test data")
    opener = network.build_policy_opener(network.EndpointPolicy.from_environment())
    with pytest.raises(RuntimeError):
        opener.open(document.as_uri())
