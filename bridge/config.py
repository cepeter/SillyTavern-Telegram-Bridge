"""Canonical startup defaults shared by ordinary bridge modules."""

from pathlib import Path
import os


BRIDGE_HOME = Path(
    os.environ.get(
        "SILLYTAVERN_BRIDGE_HOME",
        Path.home() / ".local/share/sillytavern-telegram",
    )
)
DB_FILE = BRIDGE_HOME / "scripts" / "sillytavern_telegram.sqlite3"
DEFAULT_MODEL = os.environ.get("SILLYTAVERN_MODEL", "").strip()
DEFAULT_MAX_TOKENS = 1800
PENDING_SETTINGS_TTL_SECONDS = 600

SILLYTAVERN_DIR = Path(
    os.environ.get(
        "SILLYTAVERN_DIR",
        str(BRIDGE_HOME.parent / "SillyTavern"),
    )
)
CHARACTER_DIR = Path(
    os.environ.get(
        "SILLYTAVERN_CHARACTER_DIR",
        str(SILLYTAVERN_DIR / "data/default-user/characters"),
    )
)
DEFAULT_CHARACTER_FILE = os.environ.get(
    "SILLYTAVERN_DEFAULT_CHARACTER",
    "",
).strip()
CARD_FILE = CHARACTER_DIR / DEFAULT_CHARACTER_FILE
WORLD_DIR = Path(
    os.environ.get(
        "SILLYTAVERN_WORLD_DIR",
        str(SILLYTAVERN_DIR / "data/default-user/worlds"),
    )
)
SYSTEM_PROMPTS_DIR = Path(
    os.environ.get(
        "SILLYTAVERN_SYSTEM_PROMPTS_DIR",
        str(SILLYTAVERN_DIR / "data/default-user/sysprompt"),
    )
)
SYSTEM_PROMPTS_FILE = os.environ.get(
    "SILLYTAVERN_SYSTEM_PROMPTS_FILE",
    "",
)
DEFAULT_USER_NAME = os.environ.get(
    "SILLYTAVERN_DEFAULT_USER_NAME",
    "",
).strip()
CATALOG_MAX_ITEMS = 40
CARD_FIELD_MAX_CHARS = 20000
CARD_TOTAL_MAX_CHARS = 60000

REASONING_LEVELS = {
    "none": 0,
    "low": 1024,
    "medium": 4096,
    "high": 8192,
    "max": 16384,
}

GENERATION_DEFAULTS = {
    "temperature": 0.85,
    "max_tokens": DEFAULT_MAX_TOKENS,
    "top_p": 1.0,
    "frequency_penalty": 0.0,
    "presence_penalty": 0.0,
    "reasoning_budget": 0,
    "stop_sequences": "",
}
