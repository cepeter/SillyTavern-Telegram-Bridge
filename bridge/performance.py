"""Optional low-overhead timing instrumentation for bridge hot paths."""

from __future__ import annotations

import logging
import time
from contextlib import contextmanager

from bridge.settings import AppSettings


def performance_enabled(*, app_settings: AppSettings) -> bool:
    return app_settings.performance_log


@contextmanager
def perf_span(name: str, *, app_settings: AppSettings, **fields: object):
    if not performance_enabled(app_settings=app_settings):
        yield
        return
    started = time.perf_counter()
    try:
        yield
    finally:
        elapsed_ms = (time.perf_counter() - started) * 1000
        safe_fields = " ".join(f"{key}={str(value)[:80]}" for key, value in fields.items())
        logging.info("perf span=%s duration_ms=%.3f%s", name, elapsed_ms, f" {safe_fields}" if safe_fields else "")


def timed_call(name: str, function, *args, app_settings: AppSettings, **kwargs):
    with perf_span(name, app_settings=app_settings):
        return function(*args, **kwargs)
