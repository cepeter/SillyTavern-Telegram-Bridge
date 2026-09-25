"""Fixed application limits and generation defaults; no environment is read at import."""

DEFAULT_MAX_TOKENS = 1800
PENDING_SETTINGS_TTL_SECONDS = 600

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
