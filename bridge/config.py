"""Fixed application limits and generation defaults; no environment is read at import."""

import bridge.limits as _limits

REASONING_LEVELS = {
    "none": 0,
    "low": 1024,
    "medium": 4096,
    "high": 8192,
    "max": 16384,
}

GENERATION_DEFAULTS = {
    "temperature": 0.85,
    "max_tokens": _limits.DEFAULT_MAX_TOKENS,
    "top_p": 1.0,
    "frequency_penalty": 0.0,
    "presence_penalty": 0.0,
    "reasoning_budget": 0,
    "stop_sequences": "",
}


STT_DEFAULT_MODEL = "base"


DEFAULT_PROVIDER_URL = ""


MODEL_CHOICES = []
