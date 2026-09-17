"""Optional near-real-time sync through SillyTavern's loopback HTTP API."""

import json
import logging
import os
from pathlib import Path
import sqlite3
import threading
import time
from http.cookiejar import CookieJar
from urllib.parse import urlparse
import urllib.error
import urllib.request


PHASE3_SYNC_API_URL = ""
PHASE3_SYNC_API_HANDLE = ""
PHASE3_SYNC_API_PASSWORD = ""
PHASE3_SYNC_INTERVAL_SECONDS = 2.0
PHASE3_SYNC_TIMEOUT_SECONDS = 10
_PHASE3_ALLOWED_PATHS = {"/csrf-token", "/api/users/login", "/api/ping", "/api/chats/get", "/api/chats/save", "/api/chats/group/get", "/api/chats/group/save", "/api/settings/get", "/api/settings/save"}
_PHASE3_CLIENT = None
_PHASE3_CLIENT_LOCK = threading.Lock()
_PHASE3_WORKER_LOCK = threading.Lock()
_PHASE3_WORKER = None
_PHASE3_STOP_EVENT = threading.Event()
_PHASE3_STOP_RESULTS = {"sync ID mismatch; realtime stopped", "initial divergence; realtime stopped", "conflict detected; realtime stopped"}


def _bounded_number(raw: str, default, low, high, cast):
    try:
        return min(high, max(low, cast(raw)))
    except (TypeError, ValueError):
        return default


def refresh_phase3_config() -> None:
    """Refresh Phase 3 settings after the bridge loads its environment file."""
    global PHASE3_SYNC_API_URL, PHASE3_SYNC_API_HANDLE, PHASE3_SYNC_API_PASSWORD
    global PHASE3_SYNC_INTERVAL_SECONDS, PHASE3_SYNC_TIMEOUT_SECONDS, _PHASE3_CLIENT
    PHASE3_SYNC_API_URL = os.environ.get("SILLYTAVERN_SYNC_API_URL", "").strip().rstrip("/")
    PHASE3_SYNC_API_HANDLE = os.environ.get("SILLYTAVERN_SYNC_API_HANDLE", "").strip()
    PHASE3_SYNC_API_PASSWORD = os.environ.get("SILLYTAVERN_SYNC_API_PASSWORD", "")
    PHASE3_SYNC_INTERVAL_SECONDS = _bounded_number(os.environ.get("SILLYTAVERN_SYNC_API_INTERVAL_SECONDS", "2"), 2.0, 1.0, 30.0, float)
    PHASE3_SYNC_TIMEOUT_SECONDS = _bounded_number(os.environ.get("SILLYTAVERN_SYNC_API_TIMEOUT_SECONDS", "10"), 10, 2, 30, int)
    _PHASE3_CLIENT = None


refresh_phase3_config()


class SillyTavernApiError(RuntimeError):
    """Represent a sanitized SillyTavern HTTP or protocol failure."""

    def __init__(self, message: str, status: int = 0, transient: bool = False):
        super().__init__(message)
        self.status = status
        self.transient = transient


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise SillyTavernApiError("SillyTavern API redirect refused", status=int(code))


class SillyTavernApiClient:
    """Use SillyTavern's supported chat routes with cookie and CSRF state."""

    def __init__(self, base_url: str, handle: str = "", password: str = ""):
        self.base_url = validate_phase3_api_url(base_url)
        self.handle = str(handle or "")
        self.password = str(password or "")
        self.cookies = CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), urllib.request.HTTPCookieProcessor(self.cookies), _NoRedirect())
        self.csrf_token = ""
        self.authenticated = False
        self.lock = threading.RLock()

    def _raw(self, method: str, path: str, payload: dict | None = None, use_csrf: bool = False):
        if path not in _PHASE3_ALLOWED_PATHS:
            raise SillyTavernApiError("SillyTavern API path is not allowed")
        headers = {"Accept": "application/json", "User-Agent": "SillyTavern-Telegram-Bridge/Phase3"}
        data = None
        if payload is not None:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        if use_csrf:
            if not self.csrf_token:
                raise SillyTavernApiError("SillyTavern CSRF token is unavailable", status=403)
            headers["X-CSRF-Token"] = self.csrf_token
        request = urllib.request.Request(self.base_url + path, data=data, headers=headers, method=method)
        try:
            with self.opener.open(request, timeout=PHASE3_SYNC_TIMEOUT_SECONDS) as response:
                raw = response.read(SYNC_MAX_PAYLOAD_BYTES + 1)
        except urllib.error.HTTPError as exc:
            raise SillyTavernApiError(f"SillyTavern API returned HTTP {exc.code}", status=int(exc.code), transient=int(exc.code) in {429, 500, 502, 503, 504}) from exc
        except (OSError, TimeoutError) as exc:
            raise SillyTavernApiError("SillyTavern API is unavailable", transient=True) from exc
        if len(raw) > SYNC_MAX_PAYLOAD_BYTES:
            raise SillyTavernApiError("SillyTavern API response exceeds the sync limit")
        try:
            return json.loads(raw.decode("utf-8")) if raw else {}
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise SillyTavernApiError("SillyTavern API returned invalid JSON") from exc

    def authenticate(self, force: bool = False) -> None:
        with self.lock:
            if self.authenticated and not force:
                return
            token = self._raw("GET", "/csrf-token")
            self.csrf_token = str(token.get("token") or "") if isinstance(token, dict) else ""
            if not self.csrf_token:
                raise SillyTavernApiError("SillyTavern did not provide a CSRF token", status=403)
            if self.handle:
                result = self._raw("POST", "/api/users/login", {"handle": self.handle, "password": self.password}, use_csrf=True)
                if not isinstance(result, dict) or str(result.get("handle") or "") != self.handle:
                    raise SillyTavernApiError("SillyTavern login failed", status=403)
            self._raw("POST", "/api/ping", {}, use_csrf=True)
            self.authenticated = True

    def post(self, path: str, payload: dict):
        with self.lock:
            self.authenticate()
            try:
                return self._raw("POST", path, payload, use_csrf=True)
            except SillyTavernApiError as exc:
                if exc.status not in {401, 403}:
                    raise
                self.authenticated = False
                self.authenticate(force=True)
                return self._raw("POST", path, payload, use_csrf=True)

    def get_settings(self) -> dict:
        """Read the complete SillyTavern settings document through its API."""
        result = self.post("/api/settings/get", {})
        raw = result.get("settings") if isinstance(result, dict) else None
        try:
            settings = json.loads(raw) if isinstance(raw, str) else raw
        except json.JSONDecodeError as exc:
            raise SillyTavernApiError("SillyTavern settings are invalid JSON") from exc
        if not isinstance(settings, dict):
            raise SillyTavernApiError("SillyTavern settings response has an invalid shape")
        return settings

    def save_settings(self, settings: dict) -> None:
        """Save a complete settings document through SillyTavern's atomic route."""
        result = self.post("/api/settings/save", settings)
        if not isinstance(result, dict) or result.get("result") != "ok":
            raise SillyTavernApiError("SillyTavern refused the persona settings update")

    def get_chat(self, session: dict[str, str], file_id: str, is_group: bool) -> list[dict]:
        result = self.post("/api/chats/group/get", {"id": file_id}) if is_group else self.post("/api/chats/get", {"avatar_url": Path(session["character_file"]).name, "file_name": file_id})
        if result == {}:
            return []
        if isinstance(result, dict) and isinstance(result.get("chat"), list):
            result = result["chat"]
        if not isinstance(result, list) or not all(isinstance(item, dict) for item in result):
            raise SillyTavernApiError("SillyTavern chat response has an invalid shape")
        return result

    def save_chat(self, session: dict[str, str], fields: dict[str, str], file_id: str, is_group: bool, records: list[dict]) -> None:
        payload = {"id": file_id, "chat": records, "force": False} if is_group else {"ch_name": fields["name"], "file_name": file_id, "chat": records, "avatar_url": Path(session["character_file"]).name, "force": False}
        result = self.post("/api/chats/group/save" if is_group else "/api/chats/save", payload)
        if not isinstance(result, dict) or result.get("ok") is not True:
            raise SillyTavernApiError("SillyTavern refused the chat update")


def validate_phase3_api_url(value: str) -> str:
    """Accept only an origin URL using a loopback host."""
    raw = str(value or "").strip().rstrip("/")
    parsed = urlparse(raw)
    if parsed.scheme not in {"http", "https"} or (parsed.hostname or "").casefold() not in {"127.0.0.1", "::1", "localhost"}:
        raise ValueError("Live Sync requires a loopback SillyTavern API URL")
    if parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in {"", "/"}:
        raise ValueError("Live Sync API URL must be a credential-free origin")
    return raw


def phase3_api_configured() -> bool:
    if not PHASE3_SYNC_API_URL:
        return False
    try:
        validate_phase3_api_url(PHASE3_SYNC_API_URL)
        return True
    except ValueError:
        return False


def phase3_client() -> SillyTavernApiClient:
    global _PHASE3_CLIENT
    if not phase3_api_configured():
        raise SillyTavernApiError("Live Sync API is not configured")
    with _PHASE3_CLIENT_LOCK:
        if _PHASE3_CLIENT is None or (_PHASE3_CLIENT.base_url, _PHASE3_CLIENT.handle, _PHASE3_CLIENT.password) != (PHASE3_SYNC_API_URL, PHASE3_SYNC_API_HANDLE, PHASE3_SYNC_API_PASSWORD):
            _PHASE3_CLIENT = SillyTavernApiClient(PHASE3_SYNC_API_URL, PHASE3_SYNC_API_HANDLE, PHASE3_SYNC_API_PASSWORD)
        return _PHASE3_CLIENT


def _phase3_records(db: sqlite3.Connection, chat_id: str, session: dict[str, str], fields: dict[str, str], binding: dict[str, object], rows: list[tuple]) -> list[dict]:
    return build_sync_records(db, chat_id, session, fields, str(binding["sync_id"]), rows)


def _phase3_snapshot(records: list[dict]) -> tuple[dict, list[tuple[str, str]], dict[int, tuple[list[str], int]]]:
    raw = ("\n".join(json.dumps(item, ensure_ascii=False) for item in records) + "\n").encode("utf-8")
    metadata, messages = parse_sillytavern_jsonl(raw)
    variants = {}
    message_index = 0
    for record in records[1:] if metadata else records:
        if record.get("is_system") or "mes" not in record:
            continue
        content = str(record.get("mes") or "").strip()
        if not content:
            continue
        if not record.get("is_user") and isinstance(record.get("swipes"), list):
            swipes = [str(item).strip() for item in record["swipes"] if str(item or "").strip()][:8]
            if len(swipes) > 1:
                try:
                    selected = max(0, min(int(record.get("swipe_id", 0)), len(swipes) - 1))
                except (TypeError, ValueError):
                    selected = 0
                variants[message_index] = (swipes, selected)
        message_index += 1
    return metadata, messages, variants


def _phase3_reset_failures(db: sqlite3.Connection, chat_id: str, session_id: str) -> None:
    db.execute("UPDATE sync_bindings SET realtime_failures=0,realtime_next_retry_at=0,last_error='' WHERE chat_id=? AND session_id=?", (chat_id, session_id))
    db.commit()


def _phase3_disable(db: sqlite3.Connection, chat_id: str, session_id: str, error: str) -> None:
    db.execute("UPDATE sync_bindings SET realtime_enabled=0,last_error=?,realtime_next_retry_at=0 WHERE chat_id=? AND session_id=?", (error[:1000], chat_id, session_id))
    db.commit()


def phase3_sync_now(db: sqlite3.Connection, chat_id: str, session_id: str) -> str:
    """Synchronize one binding through SillyTavern's supported chat API."""
    client = phase3_client()
    session = load_session(db, chat_id, session_id, DEFAULT_MODEL)
    binding = sync_binding(db, chat_id, session_id)
    file_id = sync_file_id(binding)
    group = group_state(db, chat_id, session_id)
    is_group = bool(group.get("enabled"))
    rows = sync_local_rows(db, chat_id, session_id)
    local_hash = sync_transcript_hash([(role, content) for _rowid, role, content, _created_at in rows])
    remote_records = client.get_chat(session, file_id, is_group)
    fields = card_fields_from_file(session["character_file"])
    if not remote_records:
        client.save_chat(session, fields, file_id, is_group, _phase3_records(db, chat_id, session, fields, binding, rows))
        set_sync_state(db, chat_id, session_id, local_hash, "bridge_to_sillytavern_api")
        _phase3_reset_failures(db, chat_id, session_id)
        return "created SillyTavern API chat"
    metadata, remote_messages, remote_variants = _phase3_snapshot(remote_records)
    remote_sync = metadata.get("bridge_sync") if isinstance(metadata.get("bridge_sync"), dict) else {}
    if remote_sync.get("sync_id") and remote_sync.get("sync_id") != binding["sync_id"]:
        set_sync_state(db, chat_id, session_id, local_hash, "", "sync_id_mismatch", "API chat sync ID does not match this session")
        _phase3_disable(db, chat_id, session_id, "sync ID mismatch")
        return "sync ID mismatch; realtime stopped"
    remote_hash = sync_transcript_hash(remote_messages)
    baseline = str(binding.get("last_hash") or "")
    if remote_hash == local_hash:
        set_sync_state(db, chat_id, session_id, local_hash, str(binding.get("last_direction") or ""))
        _phase3_reset_failures(db, chat_id, session_id)
        return "unchanged"
    if not baseline:
        set_sync_state(db, chat_id, session_id, local_hash, "", "initial_divergence", "API chat has no common checkpoint")
        _phase3_disable(db, chat_id, session_id, "initial divergence")
        return "initial divergence; realtime stopped"
    if local_hash == baseline and remote_hash != baseline:
        imported_hash = apply_sync_snapshot(db, chat_id, session, metadata, remote_messages, remote_variants)
        set_sync_state(db, chat_id, session_id, imported_hash, "sillytavern_api_to_bridge")
        _phase3_reset_failures(db, chat_id, session_id)
        return "imported SillyTavern API changes"
    if remote_hash == baseline and local_hash != baseline:
        client.save_chat(session, fields, file_id, is_group, _phase3_records(db, chat_id, session, fields, binding, rows))
        set_sync_state(db, chat_id, session_id, local_hash, "bridge_to_sillytavern_api")
        _phase3_reset_failures(db, chat_id, session_id)
        return "exported bridge changes through API"
    set_sync_state(db, chat_id, session_id, local_hash, "", "conflict", "both sides changed since the last checkpoint")
    _phase3_disable(db, chat_id, session_id, "conflict detected")
    return "conflict detected; realtime stopped"


def phase3_toggle_realtime(db: sqlite3.Connection, chat_id: str, session_id: str) -> str:
    binding = sync_binding(db, chat_id, session_id)
    if binding.get("realtime_enabled"):
        _phase3_disable(db, chat_id, session_id, "")
        return "realtime API sync disabled"
    if not phase3_api_configured():
        return "realtime API sync is not configured"
    try:
        result = phase3_sync_now(db, chat_id, session_id)
    except (SillyTavernApiError, ValueError) as exc:
        _phase3_disable(db, chat_id, session_id, str(exc))
        return f"realtime API unavailable: {exc}"
    if result in _PHASE3_STOP_RESULTS:
        return result
    db.execute("UPDATE sync_bindings SET realtime_enabled=1,realtime_failures=0,realtime_next_retry_at=0,last_error='' WHERE chat_id=? AND session_id=?", (chat_id, session_id))
    db.commit()
    return "realtime API sync enabled; " + result


def phase3_sync_status_line(db: sqlite3.Connection, chat_id: str, session_id: str) -> str:
    binding = sync_binding(db, chat_id, session_id)
    enabled = "on" if binding.get("realtime_enabled") else "off"
    configured = "configured" if phase3_api_configured() else "not configured"
    return f"Live API sync: {enabled} ({configured})"


def phase3_sync_poll(db: sqlite3.Connection) -> None:
    now = time.time()
    rows = db.execute("SELECT chat_id,session_id,realtime_failures FROM sync_bindings WHERE realtime_enabled=1 AND realtime_next_retry_at<=? LIMIT 32", (now,)).fetchall()
    for chat_id, session_id, failures in rows:
        try:
            phase3_sync_now(db, str(chat_id), str(session_id))
        except (SillyTavernApiError, ValueError) as exc:
            count = int(failures or 0) + 1
            if not getattr(exc, "transient", False) or count >= 5:
                _phase3_disable(db, str(chat_id), str(session_id), str(exc))
                continue
            delay = min(60.0, PHASE3_SYNC_INTERVAL_SECONDS * (2 ** min(count, 5)))
            db.execute("UPDATE sync_bindings SET realtime_failures=?,realtime_next_retry_at=?,last_error=? WHERE chat_id=? AND session_id=?", (count, time.time() + delay, str(exc)[:1000], chat_id, session_id))
            db.commit()
        except Exception as exc:
            logging.warning("Phase 3 binding failed for session %s", session_id, exc_info=True)
            count = int(failures or 0) + 1
            db.execute("UPDATE sync_bindings SET realtime_failures=?,realtime_next_retry_at=?,last_error=? WHERE chat_id=? AND session_id=?", (count, time.time() + min(60.0, PHASE3_SYNC_INTERVAL_SECONDS * 2), "unexpected Phase 3 binding failure", chat_id, session_id))
            db.commit()


def _phase3_worker_loop() -> None:
    while not _PHASE3_STOP_EVENT.wait(PHASE3_SYNC_INTERVAL_SECONDS):
        db = db_connect()
        try:
            phase3_sync_poll(db)
        except Exception:
            logging.warning("Phase 3 realtime sync worker failed", exc_info=True)
        finally:
            db.close()


def start_phase3_sync_worker() -> bool:
    """Start one daemon worker when the loopback API is configured."""
    global _PHASE3_WORKER
    if not phase3_api_configured():
        return False
    with _PHASE3_WORKER_LOCK:
        if _PHASE3_WORKER is not None and _PHASE3_WORKER.is_alive():
            return True
        _PHASE3_STOP_EVENT.clear()
        _PHASE3_WORKER = threading.Thread(target=_phase3_worker_loop, name="sillytavern-phase3-sync", daemon=True)
        _PHASE3_WORKER.start()
        return True
