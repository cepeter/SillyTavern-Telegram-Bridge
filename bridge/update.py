"""Confirmed, fast-forward-only bridge self-update workflow."""

from __future__ import annotations

import json
import logging
import os
import re
import unicodedata
import urllib.request
from pathlib import Path

from bridge.catalog import answer_callback
from bridge.media import remove_inline_keyboard
from bridge.network_security import EndpointPolicy, strict_urlopen
from bridge.self_update import UpdateOutcome, UpdatePlan, UpdateStatus, apply_update, version_tuple
from bridge.telegram import (
    send_panel_request,
    send_text,
)

UPDATE_REPO = "cepeter/SillyTavern-Telegram-Bridge"
UPDATE_CANONICAL_GIT_URL = f"https://github.com/{UPDATE_REPO}.git"


def _resolve_update_live_dir(environ) -> Path:
    bridge_home = Path(
        environ.get(
            "SILLYTAVERN_BRIDGE_HOME",
            str(Path.home() / ".local/share/sillytavern-telegram"),
        )
    )
    return Path(
        environ.get(
            "SILLYTAVERN_LIVE_BRIDGE_DIR",
            str(bridge_home / "live"),
        )
    )


UPDATE_LIVE_DIR = _resolve_update_live_dir(os.environ)


def _is_bridge_checkout(path: Path) -> bool:
    return (path / ".git").exists() and (path / "CHANGELOG.md").is_file()


def _resolve_update_repo_dir() -> Path:
    configured = os.environ.get("SILLYTAVERN_BRIDGE_SOURCE_DIR")
    candidates = [Path(configured).expanduser()] if configured else []
    candidates.extend((Path(__file__).resolve().parents[1], Path.home() / "sillytavern-telegram-bridge"))
    for candidate in candidates:
        if _is_bridge_checkout(candidate):
            return candidate
    return candidates[0] if candidates else Path(__file__).resolve().parents[1]


UPDATE_REPO_DIR = _resolve_update_repo_dir()


def _changelog_version(path: Path) -> str:
    try:
        headings = re.findall(r"^## \[([^]]+)\]", path.read_text(encoding="utf-8"), re.MULTILINE)
    except OSError:
        return "unknown"
    for heading in headings:
        if heading.casefold() != "unreleased":
            return heading
    return "unknown"


def _changelog_has_unreleased(path: Path) -> bool:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return False
    match = re.search(
        r"^## \[Unreleased\]\s*(.*?)(?=^## \[|\Z)",
        text,
        re.MULTILINE | re.DOTALL | re.IGNORECASE,
    )
    if not match:
        return False
    return any(line.strip() and not line.lstrip().startswith("### ") for line in match.group(1).splitlines())


def installed_bridge_version() -> str:
    live_version = _changelog_version(UPDATE_LIVE_DIR / "CHANGELOG.md")
    return live_version if live_version != "unknown" else _changelog_version(UPDATE_REPO_DIR / "CHANGELOG.md")


def installed_bridge_has_unreleased() -> bool:
    live_changelog = UPDATE_LIVE_DIR / "CHANGELOG.md"
    if _changelog_version(live_changelog) != "unknown":
        return _changelog_has_unreleased(live_changelog)
    return _changelog_has_unreleased(UPDATE_REPO_DIR / "CHANGELOG.md")


def latest_bridge_release() -> tuple[str, str]:
    request = urllib.request.Request(
        f"https://api.github.com/repos/{UPDATE_REPO}/releases/latest",
        headers={"Accept": "application/vnd.github+json", "User-Agent": "SillyTavernTelegramBridge"},
    )
    policy = EndpointPolicy(allowed_hosts=frozenset({"api.github.com"}), allow_loopback=False)
    with strict_urlopen(request, timeout=20, policy=policy) as response:
        raw = response.read(262145)
    if len(raw) > 262144:
        raise ValueError("release metadata exceeds size limit")
    payload = json.loads(raw)
    if not isinstance(payload, dict) or payload.get("draft") or payload.get("prerelease"):
        raise ValueError("release metadata is not a published stable release")
    tag = str(payload.get("tag_name") or "")
    if not tag.startswith("v"):
        raise ValueError("release tag must be versioned")
    version = tag[1:]
    version_tuple(version)
    body = str(payload.get("body") or "No release notes.")
    notes = "".join(char for char in body if char in "\n\t" or not unicodedata.category(char).startswith("C"))[:2000]
    return version, notes


def update_menu_text(current: str, latest: str, notes: str, unreleased: bool = False) -> str:
    if latest == "unavailable":
        return f"Bridge update\nInstalled: v{current}\nStatus: Release check unavailable\nRetry /update later."
    if latest == current:
        if unreleased:
            return (
                "Bridge update\nInstalled: v"
                f"""{current}"""
                " (unreleased local changes)\nLatest: v"
                f"""{latest}"""
                "\nStatus: Local unreleased changes\nNo release update is available; commit "
                "or release the local changes before updating."
            )
        return (
            f"Bridge update\nInstalled: v{current}\nLatest: v{latest}\nStatus: Already latest\nNo update is required."
        )
    return (
        "Bridge update\nInstalled: v"
        f"""{current}"""
        f"""{(" (unreleased local changes)" if unreleased else "")}"""
        "\nLatest: v"
        f"""{latest}"""
        "\n\nRelease notes:\n"
        f"""{notes}"""
        "\n\nChoose Confirm update only after reviewing the changes."
    )


def send_update_menu(token: str, chat_id: str, message_id: int | None = None, *, request_context) -> None:
    current = installed_bridge_version()
    unreleased = installed_bridge_has_unreleased()
    available = False
    try:
        latest, notes = latest_bridge_release()
        available = version_tuple(latest) > version_tuple(current)
    except Exception:
        logging.warning("Could not verify release metadata")
        latest, notes = "unavailable", "Could not verify GitHub release metadata. Retry later."
    actions = [{"text": "❌ Cancel", "callback_data": "update:cancel"}]
    if available:
        actions.insert(0, {"text": "✅ Confirm update", "callback_data": f"update:confirm:{latest}"})
    elif latest == current:
        actions.insert(0, {"text": "✅ Already latest", "callback_data": "update:no_change"})
    payload = {
        "chat_id": chat_id,
        "text": update_menu_text(current, latest, notes, unreleased),
        "reply_markup": {"inline_keyboard": [actions]},
    }
    method = "editMessageText" if message_id else "sendMessage"
    if message_id:
        payload["message_id"] = message_id
    send_panel_request(token, method, payload, request_context=request_context)


def _run_update(expected_version: str | None = None) -> UpdateOutcome:
    current = installed_bridge_version()
    try:
        latest, _notes = latest_bridge_release()
        if latest == current:
            return UpdateOutcome(UpdateStatus.ALREADY_LATEST, current)
        if expected_version != latest:
            return UpdateOutcome(UpdateStatus.REFUSED, code="stale_confirmation")
        if version_tuple(latest) <= version_tuple(current):
            return UpdateOutcome(UpdateStatus.REFUSED, code="version")
    except Exception:
        return UpdateOutcome(UpdateStatus.REFUSED, code="release_check")
    trust = os.environ.get("SILLYTAVERN_UPDATE_ALLOWED_SIGNERS", "").strip()
    return apply_update(
        UpdatePlan(
            source=UPDATE_REPO_DIR,
            live=UPDATE_LIVE_DIR,
            trusted_signers=Path(trust).expanduser() if trust else None,
            release_version=latest,
            unit=os.environ.get("SILLYTAVERN_UPDATE_SERVICE", "sillytavern-telegram.service"),
        )
    )


def format_update_outcome(outcome: UpdateOutcome) -> str:
    if outcome.status is UpdateStatus.ALREADY_LATEST:
        return f"Already latest (v{outcome.version}); no update was performed."
    if outcome.status is UpdateStatus.RESTART_SCHEDULED:
        return f"Verified v{outcome.version} installed; the user-service restart was requested."
    if outcome.status is UpdateStatus.RESTART_REQUIRED:
        return (
            f"Verified v{outcome.version} installed, but automatic restart failed. "
            "Restart the configured user service manually."
        )
    reasons = {
        "stale_confirmation": "the release changed or this panel expired; open /update again",
        "release_check": "release metadata could not be verified",
        "version": "the release version is invalid or not newer",
        "target": "the deployment paths or directory permissions are unsafe",
        "unmanaged_target": "the live directory is nonempty and has no valid bridge deployment marker",
        "source": "a complete source checkout was not found",
        "trust": "an external, owner-controlled SSH allowed-signers file is required",
        "signature": "the release tag was not signed by an independently trusted maintainer key",
        "tools": "Git, ssh-keygen and user systemd are required",
        "supervisor": "the configured user service is unavailable",
        "dirty": "the source checkout has uncommitted changes",
        "branch": "the source checkout must be on main",
        "changed": "the source checkout changed during verification",
        "ancestry": "the signed release cannot fast-forward the current checkout",
        "dependencies": (
            "runtime dependencies changed; install the signed release manually with its locked requirements"
        ),
        "busy": "another update is already running",
        "archive": "the release contains unsafe or unsupported files",
        "size": "the release exceeds the bounded deployment size",
    }
    detail = reasons.get(outcome.code, "the update could not complete; inspect the deployment logs")
    if outcome.source_changed:
        return (
            "Update requires operator attention: "
            + detail
            + ". The source checkout advanced; verify it before restarting."
        )
    if outcome.status is UpdateStatus.FAILED and outcome.code == "activate":
        return "Update activation failed. Inspect the source checkout and retained previous mirror before restarting."
    return "Update refused: " + detail + ". Existing source and live deployment were not replaced."


def handle_update_callback(db, token: str, callback: dict, data: str, chat_id: str) -> bool:
    if not data.startswith("update:"):
        return False
    answer_callback(token, str(callback.get("id", "")), "Update")
    if data in {"update:cancel", "update:no_change"}:
        remove_inline_keyboard(db, token, callback)
        return True
    if data == "update:confirm" or data.startswith("update:confirm:"):
        version = data.removeprefix("update:confirm:") if data.startswith("update:confirm:") else None
        remove_inline_keyboard(db, token, callback)
        try:
            outcome = _run_update(expected_version=version)
        except Exception:
            logging.error("Unexpected updater failure; deployment requires operator inspection")
            send_text(
                token,
                chat_id,
                "Update failed. Inspect the deployment before retrying; no error details were sent to Telegram.",
            )
            return True
        send_text(token, chat_id, format_update_outcome(outcome))
    return True
