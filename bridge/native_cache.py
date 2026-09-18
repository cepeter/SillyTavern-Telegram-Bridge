"""Low-risk caches for unchanged native SillyTavern files."""
from __future__ import annotations

import copy
import json
import threading
from pathlib import Path

_NATIVE_CACHE_LOCK = threading.RLock()
_NATIVE_CACHE: dict[tuple[str, int, int], object] = {}
_TEXT_CACHE: dict[str, str] = {}


def _key(path: Path) -> tuple[str, int, int] | None:
    try:
        stat = path.stat()
    except OSError:
        return None
    return (str(path.resolve()), int(stat.st_mtime_ns), int(stat.st_size))


def cached_json(path: Path) -> object:
    key = _key(path)
    if key is None:
        raise OSError(f"native file is unavailable: {path}")
    with _NATIVE_CACHE_LOCK:
        value = _NATIVE_CACHE.get(key)
    if value is None:
        value = json.loads(path.read_text(encoding="utf-8"))
        with _NATIVE_CACHE_LOCK:
            _NATIVE_CACHE[key] = value
    return copy.deepcopy(value)


def cached_png_metadata(path: Path, loader) -> dict:
    key = _key(path)
    if key is None:
        raise OSError(f"native file is unavailable: {path}")
    with _NATIVE_CACHE_LOCK:
        value = _NATIVE_CACHE.get(key)
    if value is None:
        value = loader(path)
        with _NATIVE_CACHE_LOCK:
            _NATIVE_CACHE[key] = value
    return copy.deepcopy(value)


def clear_native_file_cache() -> None:
    with _NATIVE_CACHE_LOCK:
        _NATIVE_CACHE.clear()
        _TEXT_CACHE.clear()


def cached_text(key: str, builder) -> str:
    with _NATIVE_CACHE_LOCK:
        value = _TEXT_CACHE.get(str(key))
    if value is None:
        value = str(builder())
        with _NATIVE_CACHE_LOCK:
            _TEXT_CACHE[str(key)] = value
    return value
