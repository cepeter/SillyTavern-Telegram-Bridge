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


SYNC_MAX_BYTES = 10 * 1024 * 1024

HINDSIGHT_DEFAULT_URL = "http://127.0.0.1:8890"
HINDSIGHT_RECALL_MAX_TOKENS = 1200
HINDSIGHT_CONTEXT_MAX_CHARS = 6000
HINDSIGHT_RETAIN_MAX_MESSAGES = 100

SUMMARY_TRIGGER_MESSAGES = 32
SUMMARY_RECENT_MESSAGES = 24
SUMMARY_MAX_CHARS = 12000
SUMMARY_MAX_OUTPUT_TOKENS = 1200
SUMMARY_UPDATE_INTERVAL = 8

RAG_MAX_FILE_BYTES = 10 * 1024 * 1024
RAG_CHUNK_CHARS = 1800
RAG_CHUNK_OVERLAP = 220
RAG_MAX_CONTEXT_CHARS = 6000
RAG_SUPPORTED_SUFFIXES = {
    ".txt",
    ".md",
    ".markdown",
    ".json",
    ".yaml",
    ".yml",
    ".csv",
    ".html",
    ".htm",
    ".xml",
    ".docx",
    ".pdf",
}
RAG_EMBEDDING_URL = os.environ.get(
    "SILLYTAVERN_RAG_EMBEDDING_URL",
    "http://127.0.0.1:8891/v1/embeddings",
)
RAG_EMBEDDING_MODEL = os.environ.get(
    "SILLYTAVERN_RAG_EMBEDDING_MODEL",
    "text-embedding-3-small",
)
RAG_EMBEDDING_DIMENSIONS = int(
    os.environ.get("SILLYTAVERN_RAG_EMBEDDING_DIMENSIONS", "1536")
)
RAG_MAX_EXTRACTED_CHARS = int(
    os.environ.get("SILLYTAVERN_RAG_MAX_EXTRACTED_CHARS", "1000000")
)
RAG_MAX_PDF_PAGES = int(
    os.environ.get("SILLYTAVERN_RAG_MAX_PDF_PAGES", "200")
)
RAG_PDF_PARSE_TIMEOUT_SECONDS = int(
    os.environ.get("SILLYTAVERN_RAG_PDF_PARSE_TIMEOUT_SECONDS", "45")
)

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
