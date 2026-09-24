"""Loopback SillyTavern HTTP API infrastructure."""
from __future__ import annotations

import json
import os
from http.cookiejar import CookieJar
from pathlib import Path
import threading
from urllib.parse import urlparse
import urllib.error
import urllib.request

from bridge.config import SYNC_MAX_BYTES


LIVE_SYNC_API_URL = ""
LIVE_SYNC_API_HANDLE = ""
LIVE_SYNC_API_PASSWORD = None
LIVE_SYNC_TIMEOUT_SECONDS = 10

_ALLOWED_PATHS = {
    "/csrf-token",
    "/api/users/login",
    "/api/ping",
    "/api/chats/get",
    "/api/chats/save",
    "/api/chats/group/get",
    "/api/chats/group/save",
    "/api/settings/get",
    "/api/settings/save",
}
_CLIENT = None
_CLIENT_LOCK = threading.Lock()


def _bounded_number(raw: str, default, low, high, cast):
    try:
        return min(high, max(low, cast(raw)))
    except (TypeError, ValueError):
        return default


def refresh_sillytavern_api_config() -> None:
    """Refresh loopback API configuration from the current environment."""
    global LIVE_SYNC_API_URL, LIVE_SYNC_API_HANDLE, LIVE_SYNC_API_PASSWORD
    global LIVE_SYNC_TIMEOUT_SECONDS, _CLIENT

    LIVE_SYNC_API_URL = (
        os.environ.get("SILLYTAVERN_SYNC_API_URL", "").strip().rstrip("/")
    )
    LIVE_SYNC_API_HANDLE = (
        os.environ.get("SILLYTAVERN_SYNC_API_HANDLE", "").strip()
    )
    LIVE_SYNC_API_PASSWORD = os.environ.get("SILLYTAVERN_SYNC_API_PASSWORD")
    LIVE_SYNC_TIMEOUT_SECONDS = _bounded_number(
        os.environ.get("SILLYTAVERN_SYNC_API_TIMEOUT_SECONDS", "10"),
        10,
        2,
        30,
        int,
    )
    _CLIENT = None


class SillyTavernApiError(RuntimeError):
    """Represent a sanitized SillyTavern HTTP or protocol failure."""

    def __init__(
        self,
        message: str,
        status: int = 0,
        transient: bool = False,
    ):
        super().__init__(message)
        self.status = status
        self.transient = transient


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise SillyTavernApiError(
            "SillyTavern API redirect refused",
            status=int(code),
        )


def validate_live_sync_api_url(value: str) -> str:
    """Accept only an origin URL using a loopback host."""
    raw = str(value or "").strip().rstrip("/")
    parsed = urlparse(raw)
    if (
        parsed.scheme not in {"http", "https"}
        or (parsed.hostname or "").casefold()
        not in {"127.0.0.1", "::1", "localhost"}
    ):
        raise ValueError("Live Sync requires a loopback SillyTavern API URL")
    if (
        parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
    ):
        raise ValueError(
            "Live Sync API URL must be a credential-free origin"
        )
    return raw


class SillyTavernApiClient:
    """Use SillyTavern's supported chat routes with cookie and CSRF state."""

    def __init__(
        self,
        base_url: str,
        handle: str = "",
        password: str | None = None,
    ):
        self.base_url = validate_live_sync_api_url(base_url)
        self.handle = str(handle or "")
        self.password = str(password or "")
        self.cookies = CookieJar()
        self.opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}),
            urllib.request.HTTPCookieProcessor(self.cookies),
            _NoRedirect(),
        )
        self.csrf_token: str | None = None
        self.authenticated = False
        self.lock = threading.RLock()

    def _raw(
        self,
        method: str,
        path: str,
        payload: dict | None = None,
        use_csrf: bool = False,
    ):
        if path not in _ALLOWED_PATHS:
            raise SillyTavernApiError(
                "SillyTavern API path is not allowed"
            )
        headers = {
            "Accept": "application/json",
            "User-Agent": "SillyTavern-Telegram-Bridge/LiveSync",
        }
        data = None
        if payload is not None:
            data = json.dumps(
                payload,
                ensure_ascii=False,
            ).encode("utf-8")
            headers["Content-Type"] = "application/json"
        if use_csrf:
            if not self.csrf_token:
                raise SillyTavernApiError(
                    "SillyTavern CSRF token is unavailable",
                    status=403,
                )
            headers["X-CSRF-Token"] = self.csrf_token
        request = urllib.request.Request(
            self.base_url + path,
            data=data,
            headers=headers,
            method=method,
        )
        try:
            with self.opener.open(
                request,
                timeout=LIVE_SYNC_TIMEOUT_SECONDS,
            ) as response:
                raw = response.read(SYNC_MAX_BYTES + 1)
        except urllib.error.HTTPError as exc:
            raise SillyTavernApiError(
                f"SillyTavern API returned HTTP {exc.code}",
                status=int(exc.code),
                transient=int(exc.code)
                in {429, 500, 502, 503, 504},
            ) from exc
        except (OSError, TimeoutError) as exc:
            raise SillyTavernApiError(
                "SillyTavern API is unavailable",
                transient=True,
            ) from exc
        if len(raw) > SYNC_MAX_BYTES:
            raise SillyTavernApiError(
                "SillyTavern API response exceeds the sync limit"
            )
        try:
            return json.loads(raw.decode("utf-8")) if raw else {}
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise SillyTavernApiError(
                "SillyTavern API returned invalid JSON"
            ) from exc

    def authenticate(self, force: bool = False) -> None:
        with self.lock:
            if self.authenticated and not force:
                return
            token = self._raw("GET", "/csrf-token")
            self.csrf_token = (
                str(token.get("token") or "")
                if isinstance(token, dict)
                else ""
            )
            if not self.csrf_token:
                raise SillyTavernApiError(
                    "SillyTavern did not provide a CSRF token",
                    status=403,
                )
            if self.handle:
                result = self._raw(
                    "POST",
                    "/api/users/login",
                    {
                        "handle": self.handle,
                        "password": self.password,
                    },
                    use_csrf=True,
                )
                if (
                    not isinstance(result, dict)
                    or str(result.get("handle") or "")
                    != self.handle
                ):
                    raise SillyTavernApiError(
                        "SillyTavern login failed",
                        status=403,
                    )
            self._raw("POST", "/api/ping", {}, use_csrf=True)
            self.authenticated = True

    def post(self, path: str, payload: dict):
        with self.lock:
            self.authenticate()
            try:
                return self._raw(
                    "POST",
                    path,
                    payload,
                    use_csrf=True,
                )
            except SillyTavernApiError as exc:
                if exc.status not in {401, 403}:
                    raise
                self.authenticated = False
                self.authenticate(force=True)
                return self._raw(
                    "POST",
                    path,
                    payload,
                    use_csrf=True,
                )

    def get_settings(self) -> dict:
        result = self.post("/api/settings/get", {})
        raw = result.get("settings") if isinstance(result, dict) else None
        try:
            settings = json.loads(raw) if isinstance(raw, str) else raw
        except json.JSONDecodeError as exc:
            raise SillyTavernApiError(
                "SillyTavern settings are invalid JSON"
            ) from exc
        if not isinstance(settings, dict):
            raise SillyTavernApiError(
                "SillyTavern settings response has an invalid shape"
            )
        return settings

    def save_settings(self, settings: dict) -> None:
        result = self.post("/api/settings/save", settings)
        if (
            not isinstance(result, dict)
            or result.get("result") != "ok"
        ):
            raise SillyTavernApiError(
                "SillyTavern refused the persona settings update"
            )

    def get_chat(
        self,
        session: dict[str, str],
        file_id: str,
        is_group: bool,
    ) -> list[dict]:
        if is_group:
            result = self.post(
                "/api/chats/group/get",
                {"id": file_id},
            )
        else:
            result = self.post(
                "/api/chats/get",
                {
                    "avatar_url": Path(
                        session["character_file"]
                    ).name,
                    "file_name": file_id,
                },
            )
        if result == {}:
            return []
        if (
            isinstance(result, dict)
            and isinstance(result.get("chat"), list)
        ):
            result = result["chat"]
        if (
            not isinstance(result, list)
            or not all(isinstance(item, dict) for item in result)
        ):
            raise SillyTavernApiError(
                "SillyTavern chat response has an invalid shape"
            )
        return result

    def save_chat(
        self,
        session: dict[str, str],
        fields: dict[str, str],
        file_id: str,
        is_group: bool,
        records: list[dict],
    ) -> None:
        if is_group:
            payload = {
                "id": file_id,
                "chat": records,
                "force": False,
            }
            path = "/api/chats/group/save"
        else:
            payload = {
                "ch_name": fields["name"],
                "file_name": file_id,
                "chat": records,
                "avatar_url": Path(
                    session["character_file"]
                ).name,
                "force": False,
            }
            path = "/api/chats/save"
        result = self.post(path, payload)
        if (
            not isinstance(result, dict)
            or result.get("ok") is not True
        ):
            raise SillyTavernApiError(
                "SillyTavern refused the chat update"
            )


def live_sync_api_configured() -> bool:
    if not LIVE_SYNC_API_URL:
        return False
    try:
        validate_live_sync_api_url(LIVE_SYNC_API_URL)
        return True
    except ValueError:
        return False


def live_sync_client() -> SillyTavernApiClient:
    global _CLIENT
    if not live_sync_api_configured():
        raise SillyTavernApiError(
            "Live Sync API is not configured"
        )
    with _CLIENT_LOCK:
        identity = (
            LIVE_SYNC_API_URL,
            LIVE_SYNC_API_HANDLE,
            LIVE_SYNC_API_PASSWORD,
        )
        cached_identity = (
            (
                _CLIENT.base_url,
                _CLIENT.handle,
                _CLIENT.password,
            )
            if _CLIENT is not None
            else None
        )
        if cached_identity != identity:
            _CLIENT = SillyTavernApiClient(*identity)
        return _CLIENT


refresh_sillytavern_api_config()
