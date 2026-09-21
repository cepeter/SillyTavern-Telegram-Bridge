"""Canonical outbound URL validation for credential-bound HTTP clients."""
from __future__ import annotations

import os
import urllib.parse
import urllib.request


def validate_provider_endpoint(
    endpoint: str,
    allowed_env: str = "SILLYTAVERN_PROVIDER_ALLOWED_HOSTS",
) -> None:
    parsed = urllib.parse.urlparse(endpoint)
    host = (parsed.hostname or "").casefold()
    loopback = host in {"localhost", "127.0.0.1", "::1"}
    if not host or parsed.scheme not in {"https", "http"}:
        raise RuntimeError("provider endpoint must use http or https")
    if parsed.scheme != "https" and not loopback:
        raise RuntimeError("external provider endpoints must use HTTPS")
    allowed = {
        item.strip().casefold()
        for item in os.environ.get(allowed_env, "").split(",")
        if item.strip()
    }
    if allowed and host not in allowed and not loopback:
        raise RuntimeError(
            f"provider host is not in {allowed_env}: {host}"
        )


def strict_urlopen(
    request,
    timeout: int,
    allowed_env: str = "SILLYTAVERN_PROVIDER_ALLOWED_HOSTS",
):
    initial = str(request.full_url)
    validate_provider_endpoint(initial, allowed_env)
    initial_host = (
        urllib.parse.urlparse(initial).hostname or ""
    ).casefold()

    class PolicyRedirectHandler(urllib.request.HTTPRedirectHandler):
        def redirect_request(
            self,
            req,
            fp,
            code,
            msg,
            headers,
            newurl,
        ):
            validate_provider_endpoint(newurl, allowed_env)
            target_host = (
                urllib.parse.urlparse(newurl).hostname or ""
            ).casefold()
            if target_host != initial_host:
                raise RuntimeError(
                    "cross-host redirects are not allowed for "
                    "credential-bound requests"
                )
            return super().redirect_request(
                req,
                fp,
                code,
                msg,
                headers,
                newurl,
            )

    opener = urllib.request.build_opener(PolicyRedirectHandler)
    return opener.open(request, timeout=timeout)
