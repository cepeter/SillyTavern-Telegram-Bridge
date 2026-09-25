"""Canonical world management owner."""

from __future__ import annotations

from bridge.card_content import active_world_files, safe_world_path
from bridge.settings import AppSettings


def delete_world_info_file(db, chat_id: str, filename: str, *, app_settings: AppSettings) -> None:
    path = safe_world_path(filename, app_settings=app_settings)
    if path is None:
        raise ValueError("World Info file not found")
    for (world_value,) in db.execute("SELECT world_file FROM sessions").fetchall():
        if path.name in active_world_files(world_value, app_settings=app_settings):
            raise ValueError("World Info is active in a session; disable it before deleting")
    path.unlink()
