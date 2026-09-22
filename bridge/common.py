#!/usr/bin/env python3
"""Telegram bridge for SillyTavern character cards.

This keeps the configured character card data and per-Telegram-user chat
history locally, then sends the assembled conversation to an
OpenAI-compatible backend.
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import concurrent.futures
from collections import deque
import hashlib
import html
import io
import json
import logging
from logging.handlers import RotatingFileHandler
import math
import os
import re
import random
import signal
import sqlite3
import struct
import subprocess  # nosec B404 - shared runtime modules use fixed local subprocess arguments
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from defusedxml import ElementTree as ET
from pathlib import Path

from bridge.config import (
    BRIDGE_HOME,
    CARD_FIELD_MAX_CHARS,
    CARD_FILE,
    CARD_TOTAL_MAX_CHARS,
    CATALOG_MAX_ITEMS,
    CHARACTER_BACKUP_DIR as _CHARACTER_BACKUP_DIR,
    CHARACTER_DIR,
    DB_FILE,
    DEFAULT_CHARACTER_FILE,
    DEFAULT_MAX_TOKENS,
    DEFAULT_MODEL,
    DEFAULT_USER_NAME,
    GENERATION_DEFAULTS,
    HINDSIGHT_CONTEXT_MAX_CHARS,
    HINDSIGHT_DEFAULT_URL,
    HINDSIGHT_RECALL_MAX_TOKENS,
    HINDSIGHT_RETAIN_MAX_MESSAGES,
    LOG_FILE as _LOG_FILE,
    MODEL_CACHE_FILE as _MODEL_CACHE_FILE,
    PROVIDER_CONFIG_FILE as _PROVIDER_CONFIG_FILE,
    RAG_CHUNK_CHARS,
    RAG_CHUNK_OVERLAP,
    RAG_EMBEDDING_DIMENSIONS,
    RAG_EMBEDDING_MODEL,
    RAG_EMBEDDING_URL,
    RAG_MAX_CONTEXT_CHARS,
    RAG_MAX_EXTRACTED_CHARS,
    RAG_MAX_FILE_BYTES,
    RAG_MAX_PDF_PAGES,
    RAG_PDF_PARSE_TIMEOUT_SECONDS,
    RAG_SUPPORTED_SUFFIXES,
    SUMMARY_MAX_CHARS,
    SUMMARY_MAX_OUTPUT_TOKENS,
    SUMMARY_RECENT_MESSAGES,
    SUMMARY_TRIGGER_MESSAGES,
    SUMMARY_UPDATE_INTERVAL,
    SYNC_MAX_BYTES,
    PENDING_SETTINGS_TTL_SECONDS,
    REASONING_LEVELS,
    SILLYTAVERN_DIR,
    SYSTEM_PROMPTS_DIR,
    WORLD_DIR,
)
from bridge.environment import environment_file
from bridge.runtime_context import (
    db_connection_context,
    panel_actor_context,
    panel_session_context,
    set_db_connection_context,
    set_panel_actor_context,
    set_panel_session_context,
)
from bridge.runtime_defaults import (
    PROCESSED_UPDATE_RETENTION_SECONDS,
)

TOPIC_SCOPE_SEPARATOR = "|topic:"


def topic_scope_id(chat_id: str, message_thread_id: int | str | None = None) -> str:
    chat_id = str(chat_id)
    if message_thread_id in (None, ""):
        return chat_id
    return f"{chat_id}{TOPIC_SCOPE_SEPARATOR}{int(message_thread_id)}"


def parse_topic_scope(scope_id: str) -> tuple[str, int | None]:
    value = str(scope_id)
    if TOPIC_SCOPE_SEPARATOR not in value:
        return value, None
    chat_id, thread_id = value.rsplit(TOPIC_SCOPE_SEPARATOR, 1)
    try:
        return chat_id, int(thread_id)
    except ValueError:
        return value, None


def topic_scope_from_message(chat_id: str, message: dict | None) -> str:
    return topic_scope_id(chat_id, (message or {}).get("message_thread_id"))


MODEL_REFRESH_SECONDS = int(os.environ.get("SILLYTAVERN_MODEL_REFRESH_SECONDS", "3600"))
IMAGE_MAX_BYTES = 8 * 1024 * 1024
TTS_MAX_CHARS = 4000
STT_MAX_BYTES = 20 * 1024 * 1024
STT_DEFAULT_MODEL = "base"
DEFAULT_ALLOWED_USER = os.environ.get("SILLYTAVERN_TELEGRAM_ALLOWED_USERS", "")
DEFAULT_PROVIDER_URL = ""
MAX_HISTORY_MESSAGES = 24
MAX_TELEGRAM_LENGTH = 4000
MODEL_CHOICES = []

def configure_logging(log_file: Path = _LOG_FILE) -> None:
    """Install the bridge rotating file handler and set root logging to INFO."""
    target = Path(log_file).expanduser().resolve()
    root = logging.getLogger()

    if any(
        isinstance(handler, RotatingFileHandler)
        and Path(handler.baseFilename).resolve() == target
        for handler in root.handlers
    ):
        return

    target.parent.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(
        str(target),
        maxBytes=10 * 1024 * 1024,
        backupCount=5,
    )
    handler.setFormatter(
        logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
    )
    root.addHandler(handler)
    root.setLevel(logging.INFO)

    try:
        target.chmod(0o600)
    except OSError:
        logging.warning(
            "Could not protect runtime log file %s",
            target,
            exc_info=True,
        )


_BACKGROUND_MAX_QUEUED_PER_CHAT = 256
_BACKGROUND_MAX_SCOPED_QUEUES = 1024
BACKGROUND_MAX_JOBS = 8
_GENERATION_SLOTS = threading.BoundedSemaphore(6)
_UTILITY_SLOTS = threading.BoundedSemaphore(4)
_GENERATION_EXECUTOR: concurrent.futures.ThreadPoolExecutor | None = None
_UTILITY_EXECUTOR: concurrent.futures.ThreadPoolExecutor | None = None
_GENERATION_LABELS = {"generation", "command", "retry", "regen", "continue", "edit", "summarize"}
_CHAT_LOCKS: dict[str, threading.Lock] = {}
_CHAT_LOCKS_GUARD = threading.Lock()
_BACKGROUND_STATE_LOCK = threading.Lock()
_EXECUTOR_LOCK = threading.Lock()
_BACKGROUND_FUTURES: set[concurrent.futures.Future] = set()
_BACKGROUND_ACCEPTING = True


def chat_job_lock(chat_id: str) -> threading.Lock:
    with _CHAT_LOCKS_GUARD:
        return _CHAT_LOCKS.setdefault(str(chat_id), threading.Lock())


def _executor_for(label: str) -> concurrent.futures.ThreadPoolExecutor:
    global _GENERATION_EXECUTOR, _UTILITY_EXECUTOR

    with _EXECUTOR_LOCK:
        if label in _GENERATION_LABELS:
            if _GENERATION_EXECUTOR is None:
                _GENERATION_EXECUTOR = concurrent.futures.ThreadPoolExecutor(
                    max_workers=3,
                    thread_name_prefix="st-generation",
                )
            return _GENERATION_EXECUTOR

        if _UTILITY_EXECUTOR is None:
            _UTILITY_EXECUTOR = concurrent.futures.ThreadPoolExecutor(
                max_workers=2,
                thread_name_prefix="st-utility",
            )
        return _UTILITY_EXECUTOR


def _admission_slot(label: str) -> threading.BoundedSemaphore:
    return _GENERATION_SLOTS if label in _GENERATION_LABELS else _UTILITY_SLOTS


def background_jobs_accepting() -> bool:
    with _BACKGROUND_STATE_LOCK:
        return bool(_BACKGROUND_ACCEPTING)


def _submit_tracked_future(label: str, function, *args, **kwargs):
    with _BACKGROUND_STATE_LOCK:
        if not _BACKGROUND_ACCEPTING:
            return None
        try:
            future = _executor_for(label).submit(function, *args, **kwargs)
        except RuntimeError:
            logging.info("Background executor is shutting down; rejected %s job", label)
            return None
        _BACKGROUND_FUTURES.add(future)

    def forget(done):
        with _BACKGROUND_STATE_LOCK:
            _BACKGROUND_FUTURES.discard(done)

    future.add_done_callback(forget)
    return future


def drain_background_jobs(timeout: float = 20.0) -> bool:
    """Wait for already-submitted work without accepting new jobs."""
    deadline = time.monotonic() + max(0.0, float(timeout))
    while True:
        with _BACKGROUND_STATE_LOCK:
            futures = tuple(_BACKGROUND_FUTURES)
        if not futures or all(future.done() for future in futures):
            return True
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        concurrent.futures.wait(futures, timeout=remaining)


def submit_background(label: str, function, *args, **kwargs) -> bool:
    if not background_jobs_accepting():
        logging.info("Background shutdown in progress; rejected %s job", label)
        return False
    slot = _admission_slot(label)
    if not slot.acquire(blocking=False):
        logging.warning("Background queue full; dropping %s job", label)
        return False
    future = _submit_tracked_future(label, function, *args, **kwargs)
    if future is None:
        slot.release()
        return False

    def complete(done):
        slot.release()
        error = done.exception()
        if error:
            logging.error("Background %s job failed: %s", label, error, exc_info=(type(error), error, error.__traceback__))
        _dispatch_waiting_chat_jobs()

    future.add_done_callback(complete)
    return True


_DURABLE_BACKLOG_DISPATCHER = None


def register_durable_backlog_dispatcher(callback) -> None:
    global _DURABLE_BACKLOG_DISPATCHER
    _DURABLE_BACKLOG_DISPATCHER = callback


def begin_background_shutdown() -> None:
    """Stop accepting work; durable chat jobs remain recoverable in SQLite."""
    global _BACKGROUND_ACCEPTING, _DURABLE_BACKLOG_DISPATCHER
    with _BACKGROUND_STATE_LOCK:
        _BACKGROUND_ACCEPTING = False
    _DURABLE_BACKLOG_DISPATCHER = None


def shutdown_background_executors(timeout: float = 20.0) -> bool:
    global _GENERATION_EXECUTOR, _UTILITY_EXECUTOR

    begin_background_shutdown()
    drained = drain_background_jobs(timeout)

    with _EXECUTOR_LOCK:
        generation = _GENERATION_EXECUTOR
        utility = _UTILITY_EXECUTOR
        _GENERATION_EXECUTOR = None
        _UTILITY_EXECUTOR = None

    if generation is not None:
        generation.shutdown(wait=drained, cancel_futures=not drained)
    if utility is not None:
        utility.shutdown(wait=drained, cancel_futures=not drained)
    return drained


_CHAT_QUEUES: dict[str, deque[tuple[str, object, tuple, dict]]] = {}
_CHAT_ACTIVE: set[str] = set()
_CHAT_IN_FLIGHT: set[str] = set()


def _dispatch_waiting_chat_jobs() -> None:
    if not background_jobs_accepting():
        return
    with _CHAT_LOCKS_GUARD:
        chat_ids = list(_CHAT_QUEUES)
    for chat_id in chat_ids:
        _start_next_chat_job(chat_id)
    if _DURABLE_BACKLOG_DISPATCHER is not None:
        try:
            _DURABLE_BACKLOG_DISPATCHER()
        except Exception:
            logging.warning("Durable backlog dispatcher failed", exc_info=True)


def _start_next_chat_job(chat_id: str) -> None:
    if not background_jobs_accepting():
        return
    chat_id = str(chat_id)
    with _CHAT_LOCKS_GUARD:
        if chat_id in _CHAT_IN_FLIGHT:
            return
        queue = _CHAT_QUEUES.get(chat_id, deque())
        if not queue:
            _CHAT_ACTIVE.discard(chat_id)
            _CHAT_QUEUES.pop(chat_id, None)
            return
        slot = _admission_slot(queue[0][0])
        if not slot.acquire(blocking=False):
            return
        label, function, args, kwargs = queue.popleft()
        _CHAT_IN_FLIGHT.add(chat_id)
    future = _submit_tracked_future(label, function, *args, **kwargs)
    if future is None:
        with _CHAT_LOCKS_GUARD:
            _CHAT_IN_FLIGHT.discard(chat_id)
            _CHAT_QUEUES.setdefault(chat_id, deque()).appendleft((label, function, args, kwargs))
        slot.release()
        return
    def complete(done):
        slot.release()
        with _CHAT_LOCKS_GUARD:
            _CHAT_IN_FLIGHT.discard(chat_id)
        error = done.exception()
        if error:
            logging.error("Ordered background %s job failed: %s", label, error, exc_info=(type(error), error, error.__traceback__))
        _start_next_chat_job(chat_id)
        _dispatch_waiting_chat_jobs()
    future.add_done_callback(complete)


def submit_chat_background(label: str, chat_id: str, function, *args, **kwargs) -> bool:
    if not background_jobs_accepting():
        logging.info("Background shutdown in progress; durable %s job remains in SQLite", label)
        return False
    chat_id = str(chat_id)
    with _CHAT_LOCKS_GUARD:
        if chat_id not in _CHAT_QUEUES and len(_CHAT_QUEUES) >= _BACKGROUND_MAX_SCOPED_QUEUES:
            logging.warning("Global ordered queue limit reached; durable job remains in SQLite")
            return False
        queue = _CHAT_QUEUES.setdefault(chat_id, deque())
        if len(queue) >= _BACKGROUND_MAX_QUEUED_PER_CHAT:
            logging.warning("Ordered queue full for %s; durable job remains in SQLite", chat_id)
            return False
        queue.append((label, function, args, kwargs))
        should_start = chat_id not in _CHAT_ACTIVE
        if should_start:
            _CHAT_ACTIVE.add(chat_id)
    if should_start:
        _start_next_chat_job(chat_id)
    return True

def enforce_runtime_permissions() -> None:
    private_dirs = {DB_FILE.parent, _LOG_FILE.parent, BRIDGE_HOME / "backups", _CHARACTER_BACKUP_DIR}
    enforce_prompt_permissions = os.environ.get("SILLYTAVERN_ENFORCE_PROMPT_PERMISSIONS", "false").casefold() == "true"
    if SYSTEM_PROMPTS_DIR.exists() and (enforce_prompt_permissions or SYSTEM_PROMPTS_DIR.is_relative_to(BRIDGE_HOME.parent)):
        private_dirs.add(SYSTEM_PROMPTS_DIR)
    for directory in private_dirs:
        try:
            directory.mkdir(parents=True, exist_ok=True)
            directory.chmod(0o700)
        except OSError:
            logging.warning("Could not protect runtime directory %s", directory, exc_info=True)
    private_files = {environment_file(), DB_FILE, _LOG_FILE, _PROVIDER_CONFIG_FILE, _MODEL_CACHE_FILE}
    if SYSTEM_PROMPTS_DIR.exists() and (enforce_prompt_permissions or SYSTEM_PROMPTS_DIR.is_relative_to(BRIDGE_HOME.parent)):
        private_files.update(SYSTEM_PROMPTS_DIR.glob("*.txt"))
        private_files.update(SYSTEM_PROMPTS_DIR.glob("*.json"))
    private_files.update(DB_FILE.parent.glob(DB_FILE.name + "-*"))
    for path in private_files:
        try:
            if path.is_file() and not path.is_symlink():
                path.chmod(0o600)
        except OSError:
            logging.warning("Could not protect runtime file %s", path, exc_info=True)


def delete_pending_input_prompts(token: str, chat_id: str, state: dict) -> None:
    """Remove prompt messages created for a pending text-input transition."""
    # Local import avoids making the foundational runtime-support module depend
    # on the Telegram adapter during ordinary module import.
    from bridge.telegram import telegram_request

    message_ids = state.get("prompt_message_ids") or []
    if isinstance(message_ids, (int, str)):
        message_ids = [message_ids]
    for message_id in message_ids:
        try:
            telegram_request(token, "deleteMessage", {"chat_id": chat_id, "message_id": int(message_id)})
        except Exception:
            logging.info("Pending input prompt already unavailable", exc_info=True)
