"""Load the bridge domain modules into one runtime namespace.

The domain files are intentionally kept independent of import order: functions
resolve shared names at call time, while this loader provides one compatibility
namespace for the existing Telegram handlers.
"""
from __future__ import annotations

from pathlib import Path

_MODULES = (
    "common.py", "database.py", "memory.py", "rag.py", "groups.py",
    "telegram.py", "help.py", "catalog.py", "media.py", "generation.py",
    "commands.py", "message_commands.py", "callbacks.py", "main.py",
    "recovery.py", "import_recovery.py", "callback_safety.py",
)
_BASE = Path(__file__).parent
for _filename in _MODULES:
    _path = _BASE / _filename
    _source = _path.read_text(encoding="utf-8")
    exec(compile(_source, str(_path), "exec"), globals(), globals())

del _BASE, _MODULES, _filename, _path, _source