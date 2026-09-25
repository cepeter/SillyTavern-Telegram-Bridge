"""Low-level storage adapter for native World Info documents."""

from __future__ import annotations

import json
import logging
import os
import tempfile
from pathlib import Path

from bridge.settings import AppSettings


def install_world_info_document(filename: str, raw: bytes, *, app_settings: AppSettings) -> Path:
    name = Path(str(filename)).name
    if name != str(filename) or Path(name).suffix != ".json" or name in {"", ".", ".."}:
        raise ValueError("World Info upload must be a JSON file with a simple filename")
    try:
        payload = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("World Info JSON is invalid") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("entries"), dict):
        raise ValueError("World Info JSON must contain an entries object")
    app_settings.world_dir.mkdir(parents=True, exist_ok=True)
    target = app_settings.world_dir / name
    if target.exists():
        raise FileExistsError(f"World Info file already exists: {name}")
    temporary = tempfile.NamedTemporaryFile(
        prefix=".world-",
        suffix=".tmp",
        dir=app_settings.world_dir,
        delete=False,
    )
    try:
        temporary.write(raw)
        temporary.flush()
        os.fsync(temporary.fileno())
        temporary.close()
        os.replace(temporary.name, target)
    except Exception:
        try:
            temporary.close()
        except Exception:
            logging.debug(
                "Could not close temporary provider catalog file",
                exc_info=True,
            )
        Path(temporary.name).unlink(missing_ok=True)
        raise
    return target
