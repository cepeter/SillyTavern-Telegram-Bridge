"""Confirmed, fast-forward-only bridge self-update workflow."""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess  # nosec B404 - update commands are fixed-argv and resolved to absolute paths
import urllib.request
from pathlib import Path

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


def _resolve_command(name: str) -> str:
    """Resolve an update executable instead of trusting PATH at launch."""
    resolved = shutil.which(name)
    if not resolved:
        raise OSError(f"required update executable is unavailable: {name}")
    return resolved


def _run_command(arguments: list[str], **kwargs):
    """Run a fixed-argv update command with an absolute executable path."""
    command = [_resolve_command(arguments[0]), *arguments[1:]]
    return subprocess.run(command, **kwargs)  # nosec B603 - fixed argv, no shell


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
    return any(
        line.strip() and not line.lstrip().startswith("### ")
        for line in match.group(1).splitlines()
    )


def installed_bridge_version() -> str:
    live_version = _changelog_version(UPDATE_LIVE_DIR / "CHANGELOG.md")
    return live_version if live_version != "unknown" else _changelog_version(UPDATE_REPO_DIR / "CHANGELOG.md")


def installed_bridge_has_unreleased() -> bool:
    live_changelog = UPDATE_LIVE_DIR / "CHANGELOG.md"
    if _changelog_version(live_changelog) != "unknown":
        return _changelog_has_unreleased(live_changelog)
    return _changelog_has_unreleased(UPDATE_REPO_DIR / "CHANGELOG.md")


def latest_bridge_release() -> tuple[str, str]:
    request = urllib.request.Request(f"https://api.github.com/repos/{UPDATE_REPO}/releases/latest", headers={"Accept": "application/vnd.github+json", "User-Agent": "SillyTavernTelegramBridge"})
    with urllib.request.urlopen(request, timeout=20) as response:  # nosec B310 - fixed HTTPS GitHub API endpoint
        payload = json.loads(response.read().decode("utf-8"))
    tag = str(payload.get("tag_name") or "unknown")
    return tag.removeprefix("v"), str(payload.get("body") or "No release notes.")[:2000]


def update_menu_text(current: str, latest: str, notes: str, unreleased: bool = False) -> str:
    if latest == current:
        if unreleased:
            return f"Bridge update\nInstalled: v{current} (unreleased local changes)\nLatest: v{latest}\nStatus: Local unreleased changes\nNo release update is available; commit or release the local changes before updating."
        return f"Bridge update\nInstalled: v{current}\nLatest: v{latest}\nStatus: Already latest\nNo update is required."
    return f"Bridge update\nInstalled: v{current}{' (unreleased local changes)' if unreleased else ''}\nLatest: v{latest}\n\nRelease notes:\n{notes}\n\nChoose Confirm update only after reviewing the changes."


def send_update_menu(token: str, chat_id: str, message_id: int | None = None, *, request_context) -> None:
    current = installed_bridge_version()
    unreleased = installed_bridge_has_unreleased()
    try:
        latest, notes = latest_bridge_release()
    except Exception as exc:
        latest, notes = "unavailable", f"Could not check GitHub: {exc}"
    rows = [[{"text": "✅ Already latest", "callback_data": "update:no_change"}, {"text": "❌ Cancel", "callback_data": "update:cancel"}]] if latest == current else [[{"text": "✅ Confirm update", "callback_data": "update:confirm"}, {"text": "❌ Cancel", "callback_data": "update:cancel"}]]
    payload = {"chat_id": chat_id, "text": update_menu_text(current, latest, notes, unreleased), "reply_markup": {"inline_keyboard": rows}}
    method = "editMessageText" if message_id else "sendMessage"
    if message_id:
        payload["message_id"] = message_id
    send_panel_request(token, method, payload, request_context=request_context)


def _run_update() -> str:
    current = installed_bridge_version()
    try:
        latest, _notes = latest_bridge_release()
    except Exception as exc:
        return f"Update refused: could not verify latest release ({exc})."
    if latest == current:
        if installed_bridge_has_unreleased():
            return f"Already latest (v{current}); local unreleased changes were not overwritten."
        return f"Already latest (v{current}); no update was performed."
    if not _is_bridge_checkout(UPDATE_REPO_DIR):
        return f"Update refused: source checkout not found at {UPDATE_REPO_DIR}. Set SILLYTAVERN_BRIDGE_SOURCE_DIR."  # nosec B608 - diagnostic text only
    if _run_command(["git", "status", "--porcelain"], cwd=UPDATE_REPO_DIR, capture_output=True, text=True, timeout=20).stdout.strip():
        return "Update refused: local repository has uncommitted changes."
    release_ref = f"v{latest}"
    fetched_ref = f"refs/bridge-release/{release_ref}"
    try:
        _run_command(
            [
                "git",
                "fetch",
                "--force",
                "--no-tags",
                UPDATE_CANONICAL_GIT_URL,
                f"refs/tags/{release_ref}:{fetched_ref}",
            ],
            cwd=UPDATE_REPO_DIR,
            check=True,
            timeout=120,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        _run_command(
            ["git", "merge", "--ff-only", fetched_ref],
            cwd=UPDATE_REPO_DIR,
            check=True,
            timeout=120,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or "").strip()
        detail = re.sub(r"(https?://)[^/@\s]+@", r"\1***@", detail)
        return f"Update refused: git {' '.join(exc.cmd[1:])} failed (exit {exc.returncode}{': ' + detail if detail else ''})."
    UPDATE_LIVE_DIR.joinpath("bridge").mkdir(parents=True, exist_ok=True)
    _run_command(["rsync", "-a", "--delete", f"{UPDATE_REPO_DIR}/bridge/", f"{UPDATE_LIVE_DIR}/bridge/"], check=True, timeout=120)
    _run_command(["cp", str(UPDATE_REPO_DIR / "sillytavern_telegram_bridge.py"), str(UPDATE_LIVE_DIR / "sillytavern_telegram_bridge.py")], check=True, timeout=20)
    _run_command(["cp", str(UPDATE_REPO_DIR / "CHANGELOG.md"), str(UPDATE_LIVE_DIR / "CHANGELOG.md")], check=True, timeout=20)
    _run_command(["systemctl", "--user", "restart", "sillytavern-telegram.service"], check=True, timeout=120)
    return f"Bridge updated to v{installed_bridge_version()} and restarted."


def handle_update_callback(db, token: str, callback: dict, data: str, chat_id: str) -> bool:
    if not data.startswith("update:"):
        return False
    answer_callback(token, str(callback.get("id", "")), "Update")
    if data == "update:cancel":
        remove_inline_keyboard(db, token, callback)
        return True
    if data == "update:no_change":
        remove_inline_keyboard(db, token, callback)
        return True
    if data == "update:confirm":
        try:
            result = _run_update()
        except Exception as exc:
            result = f"Update failed safely: {exc}"
        send_text(token, chat_id, result)
        remove_inline_keyboard(db, token, callback)
        return True
    return True


# Explicit late imports replace transitional dependency injection.
from bridge.catalog import answer_callback
from bridge.media import remove_inline_keyboard
from bridge.telegram import (
    send_panel_request,
    send_text,
)
