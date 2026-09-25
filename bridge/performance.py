"""Optional low-overhead timing instrumentation for bridge hot paths."""

from __future__ import annotations

import logging
import os
import time
from contextlib import contextmanager


def performance_enabled() -> bool:
    return os.environ.get("SILLYTAVERN_PERF_LOG", "").casefold() in {"1", "true", "yes", "on"}


@contextmanager
def perf_span(name: str, **fields: object):
    if not performance_enabled():
        yield
        return
    started = time.perf_counter()
    try:
        yield
    finally:
        elapsed_ms = (time.perf_counter() - started) * 1000
        safe_fields = " ".join(f"{key}={str(value)[:80]}" for key, value in fields.items())
        logging.info("perf span=%s duration_ms=%.3f%s", name, elapsed_ms, f" {safe_fields}" if safe_fields else "")


def timed_call(name: str, function, *args, **kwargs):
    with perf_span(name):
        return function(*args, **kwargs)
