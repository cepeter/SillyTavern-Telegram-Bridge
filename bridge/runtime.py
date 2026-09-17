"""Load the bridge domain modules into one runtime namespace.

The domain files are intentionally kept independent of import order: functions
resolve shared names at call time, while this loader provides one compatibility
namespace for the existing Telegram handlers.
"""
from __future__ import annotations

from pathlib import Path

_MODULES = (
    "common.py", "cards.py", "schema.py", "database.py", "memory.py", "rag.py", "groups.py",
    "telegram.py", "language.py", "help_details.py", "help.py", "input_flows.py", "catalog.py", "media.py", "generation.py",
    "commands.py", "command_routes.py", "message_commands.py", "callbacks.py", "panel_callback_routes.py", "main.py",
    "recovery.py", "sync_core.py", "sync_api.py", "persona_sync.py", "character_identity.py", "session_naming.py", "sync_safety.py", "callback_safety.py",
    "scheduler_safety.py",
)
_BASE = Path(__file__).parent
for _filename in _MODULES:
    _path = _BASE / _filename
    _source = _path.read_text(encoding="utf-8")
    exec(compile(_source, str(_path), "exec"), globals(), globals())

del _BASE, _MODULES, _filename, _path, _source
