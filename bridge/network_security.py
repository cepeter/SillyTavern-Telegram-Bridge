"""Explicit egress trust with DNS-pinned sockets and verified hostname TLS.

Provider catalogs describe endpoints, not authorization. External hosts require
an independent allowlist. Private LAN/tailnet hosts need a second explicit grant.
Loopback services remain supported; metadata and unroutable addresses never do.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import http.client
import ipaddress
import os
import re
import socket
from typing import Any
import urllib.parse
import urllib.request


class EndpointPolicyError(RuntimeError):
    """An outbound destination violates the configured trust boundary."""


def _address(host: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return None
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
        return address.ipv4_mapped
    return address


def _host(value: str) -> str:
    if not value or any(char.isspace() for char in value) or any(char in value for char in "/@%\\*?#"):
        raise EndpointPolicyError("invalid hostname in endpoint policy")
    value = value.casefold().rstrip(".")
    address = _address(value)
    if address is not None:
        return address.compressed
    if ":" in value or re.fullmatch(r"[0-9.]+", value):
        raise EndpointPolicyError("ambiguous or invalid IP address in endpoint")
    try:
        return value.encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise EndpointPolicyError("invalid hostname in endpoint policy") from exc


def _is_loopback(host: str) -> bool:
    address = _address(host)
    return host == "localhost" or bool(address is not None and address.is_loopback)


@dataclass(frozen=True)
class EndpointPolicy:
    """Per-request policy, snapshotted before any credentials are sent."""

    allowed_hosts: frozenset[str] = frozenset()
    private_hosts: frozenset[str] = frozenset()
    allow_loopback: bool = True
    label: str = "SILLYTAVERN_PROVIDER_ALLOWED_HOSTS"
    private_label: str = "SILLYTAVERN_PROVIDER_PRIVATE_HOSTS"

    @classmethod
    def from_environment(
        cls,
        allowed_env: str = "SILLYTAVERN_PROVIDER_ALLOWED_HOSTS",
        environ: Mapping[str, str] | None = None,
    ) -> EndpointPolicy:
        source = os.environ if environ is None else environ
        private_env = allowed_env.removesuffix("_ALLOWED_HOSTS") + "_PRIVATE_HOSTS"

        def values(name: str) -> frozenset[str]:
            return frozenset(_host(item.strip()) for item in source.get(name, "").split(",") if item.strip())

        return cls(values(allowed_env), values(private_env), label=allowed_env, private_label=private_env)

    def validate(self, endpoint: str) -> tuple[str, str, int]:
        if not isinstance(endpoint, str) or any(ord(char) <= 32 or ord(char) == 127 for char in endpoint):
            raise EndpointPolicyError("endpoint contains whitespace or control characters")
        try:
            parsed = urllib.parse.urlsplit(endpoint)
            if parsed.scheme not in {"https", "http"} or not parsed.hostname:
                raise EndpointPolicyError("provider endpoint must use http or https")
            if parsed.username is not None or parsed.password is not None:
                raise EndpointPolicyError("credentials must not appear in endpoint URLs")
            host = _host(parsed.hostname)
            port = parsed.port if parsed.port is not None else (443 if parsed.scheme == "https" else 80)
        except ValueError as exc:
            raise EndpointPolicyError("invalid endpoint authority or port") from exc
        if not 1 <= port <= 65535:
            raise EndpointPolicyError("invalid endpoint port")
        loopback = self.allow_loopback and _is_loopback(host)
        if parsed.scheme != "https" and not loopback:
            raise EndpointPolicyError("external provider endpoints must use HTTPS")
        if not loopback and host not in self.allowed_hosts:
            raise EndpointPolicyError(f"provider host must be explicitly listed in {self.label}: {host}")
        address = _address(host)
        if address is not None:
            self.validate_address(host, address)
        return parsed.scheme, host, port

    def validate_address(self, host: str, address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> None:
        if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
            address = address.ipv4_mapped
        if _is_loopback(host):
            if self.allow_loopback and address.is_loopback:
                return
            raise EndpointPolicyError("loopback hostname resolved outside loopback")
        if address.is_loopback:
            raise EndpointPolicyError("external hostname resolved to loopback")
        if address.is_unspecified or address.is_multicast or address.is_link_local or address.is_reserved:
            raise EndpointPolicyError("metadata, link-local, reserved and unroutable destinations are forbidden")
        if not address.is_global and host not in self.private_hosts:
            raise EndpointPolicyError(f"non-public destination requires explicit {self.private_label}: {host}")


def validate_provider_endpoint(endpoint: str, allowed_env: str = "SILLYTAVERN_PROVIDER_ALLOWED_HOSTS") -> None:
    """Validate URL syntax and explicit host trust without a DNS side effect."""
    EndpointPolicy.from_environment(allowed_env).validate(endpoint)


def resolve_endpoint_addresses(host: str, port: int, policy: EndpointPolicy) -> list[tuple[Any, ...]]:
    """Resolve once, validate every address, then return exact socket addresses."""
    canonical = _host(host)
    if not (policy.allow_loopback and _is_loopback(canonical)) and canonical not in policy.allowed_hosts:
        raise EndpointPolicyError(f"provider host must be explicitly listed in {policy.label}: {canonical}")
    results = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    if not results or len(results) > 32:
        raise EndpointPolicyError("endpoint DNS returned no usable bounded address set")
    for family, _kind, _proto, _name, sockaddr in results:
        if family not in {socket.AF_INET, socket.AF_INET6}:
            raise EndpointPolicyError("unsupported endpoint address family")
        policy.validate_address(canonical, ipaddress.ip_address(sockaddr[0]))
    return results


def _connect_pinned(
    address: tuple[str, int],
    timeout: float | None,
    source_address: tuple[str, int] | None,
    policy: EndpointPolicy,
) -> socket.socket:
    addresses = resolve_endpoint_addresses(address[0], address[1], policy)
    failure: OSError | None = None
    for family, kind, proto, _name, sockaddr in addresses:
        sock = socket.socket(family, kind, proto)
        try:
            sock.settimeout(timeout)
            if source_address is not None:
                sock.bind(source_address)
            # sockaddr is numeric from the validated lookup: no second lookup.
            sock.connect(sockaddr)
            return sock
        except OSError as exc:
            failure = exc
            sock.close()
    if failure is not None:
        raise failure
    raise EndpointPolicyError("endpoint DNS returned no connectable addresses")


class PinnedHTTPConnection(http.client.HTTPConnection):
    def __init__(self, host: str, *, policy: EndpointPolicy, **kwargs: Any) -> None:
        super().__init__(host, **kwargs)
        self._create_connection = lambda address, timeout, source_address: _connect_pinned(
            address, timeout, source_address, policy
        )


class PinnedHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, host: str, *, policy: EndpointPolicy, **kwargs: Any) -> None:
        super().__init__(host, **kwargs)
        # HTTPSConnection still performs its standard verified TLS handshake
        # with self.host as the SNI/certificate hostname after this socket opens.
        self._create_connection = lambda address, timeout, source_address: _connect_pinned(
            address, timeout, source_address, policy
        )


class PolicyRedirectHandler(urllib.request.HTTPRedirectHandler):
    def __init__(self, policy: EndpointPolicy) -> None:
        self.policy = policy
        super().__init__()

    def redirect_request(self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str) -> Any:
        before = self.policy.validate(req.full_url)
        after = self.policy.validate(newurl)
        if before != after:
            raise EndpointPolicyError("cross-origin redirects are not allowed for credential-bound requests")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class _PolicyHTTPHandler(urllib.request.HTTPHandler):
    def __init__(self, policy: EndpointPolicy) -> None:
        self.policy = policy
        super().__init__()

    def http_open(self, req: Any) -> Any:
        self.policy.validate(req.full_url)
        if req.has_proxy() or getattr(req, "_tunnel_host", None):
            raise EndpointPolicyError("implicit or request-level proxy routing is forbidden")
        return self.do_open(lambda host, **kwargs: PinnedHTTPConnection(host, policy=self.policy, **kwargs), req)


class _PolicyHTTPSHandler(urllib.request.HTTPSHandler):
    def __init__(self, policy: EndpointPolicy) -> None:
        self.policy = policy
        super().__init__()

    def https_open(self, req: Any) -> Any:
        self.policy.validate(req.full_url)
        if req.has_proxy() or getattr(req, "_tunnel_host", None):
            raise EndpointPolicyError("implicit or request-level proxy routing is forbidden")
        return self.do_open(lambda host, **kwargs: PinnedHTTPSConnection(host, policy=self.policy, **kwargs), req)


class _PolicyURLGuard(urllib.request.BaseHandler):
    def __init__(self, policy: EndpointPolicy) -> None:
        self.policy = policy

    def default_open(self, req: Any) -> None:
        # build_opener also supplies file/data/FTP handlers. Guard every scheme
        # before handler dispatch, including callers that reuse this opener.
        self.policy.validate(req.full_url)
        if req.has_proxy() or getattr(req, "_tunnel_host", None):
            raise EndpointPolicyError("implicit or request-level proxy routing is forbidden")
        return None


def build_policy_opener(policy: EndpointPolicy) -> urllib.request.OpenerDirector:
    """Keep the opener local and disable environment/OS proxy inheritance."""
    return urllib.request.build_opener(
        urllib.request.ProxyHandler({}),
        _PolicyURLGuard(policy),
        _PolicyHTTPHandler(policy),
        _PolicyHTTPSHandler(policy),
        PolicyRedirectHandler(policy),
    )


def strict_urlopen(
    request: urllib.request.Request,
    timeout: float,
    allowed_env: str = "SILLYTAVERN_PROVIDER_ALLOWED_HOSTS",
    *,
    policy: EndpointPolicy | None = None,
) -> Any:
    active_policy = policy if policy is not None else EndpointPolicy.from_environment(allowed_env)
    active_policy.validate(request.full_url)
    if request.has_proxy() or getattr(request, "_tunnel_host", None):
        raise EndpointPolicyError("implicit or request-level proxy routing is forbidden")
    if timeout <= 0:
        raise ValueError("request timeout must be positive")
    return build_policy_opener(active_policy).open(request, timeout=timeout)
