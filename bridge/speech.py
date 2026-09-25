"""Canonical speech owner."""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from bridge.config import STT_DEFAULT_MODEL
from bridge.limits import STT_MAX_BYTES, TTS_MAX_CHARS
from bridge.operations import begin_operation, operation_was_applied, record_operation
from bridge.settings import AppSettings
from bridge.sqlite_store import db_connect
from bridge.topic_scope import parse_topic_scope


def _resolve_media_command(configured: str, label: str) -> str:
    candidate = Path(configured).expanduser()
    resolved = str(candidate) if candidate.is_absolute() else shutil.which(configured)
    if not resolved or not os.path.isfile(resolved) or not os.access(resolved, os.X_OK):
        raise OSError(f"required {label} executable is unavailable: {configured}")
    return resolved


def send_voice(token: str, chat_id: str, path: Path, caption: str = "") -> bool:
    boundary = f"----BridgeVoice{int(time.time() * 1000000)}"
    real_chat_id, thread_id = parse_topic_scope(chat_id)
    chunks = [f'--{boundary}\r\nContent-Disposition: form-data; name="chat_id"\r\n\r\n{real_chat_id}\r\n'.encode()]
    if thread_id is not None:
        chunks.append(
            f'--{boundary}\r\nContent-Disposition: form-data; name="message_thread_id"\r\n\r\n{thread_id}\r\n'.encode()
        )
    if caption:
        chunks.append(f'--{boundary}\r\nContent-Disposition: form-data; name="caption"\r\n\r\n{caption}\r\n'.encode())
    chunks.append(
        (
            "--"
            f"""{boundary}"""
            '\r\nContent-Disposition: form-data; name="voice"; filename="'
            f"""{path.name}"""
            '"\r\nContent-Type: audio/ogg\r\n\r\n'
        ).encode()
        + path.read_bytes()
        + b"\r\n"
    )
    chunks.append(f"--{boundary}--\r\n".encode())
    request = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/sendVoice",
        data=b"".join(chunks),
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=120) as response:  # noqa: S310 -- fixed Telegram HTTPS endpoint; token is not a URL
            result = json.loads(response.read().decode("utf-8"))
        return bool(result.get("ok"))
    except (OSError, ValueError, RuntimeError, json.JSONDecodeError):
        logging.error("Telegram voice upload failed", exc_info=True)
        return False


def synthesize_voice(text: str, output_path: Path, *, app_settings: AppSettings) -> None:
    text = str(text).strip()[:TTS_MAX_CHARS]
    if not text:
        raise ValueError("TTS text is empty")
    tts_bin = _resolve_media_command(
        app_settings.environ.get("SILLYTAVERN_TTS_BIN", str(app_settings.bridge_home / "venv" / "bin" / "edge-tts")),
        "TTS",
    )
    voice = app_settings.environ.get("SILLYTAVERN_TTS_VOICE", "").strip()
    if not voice:
        raise ValueError("SILLYTAVERN_TTS_VOICE is required for voice output")
    with tempfile.TemporaryDirectory(prefix="st-tts-") as temp_dir:
        mp3 = Path(temp_dir) / "speech.mp3"
        subprocess.run(  # noqa: S603 -- fixed argv, no shell; executable is operator configured
            [tts_bin, "--voice", voice, "--text", text, "--write-media", str(mp3)],
            check=True,
            timeout=120,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        ffmpeg_bin = _resolve_media_command("ffmpeg", "ffmpeg")
        subprocess.run(  # noqa: S603 -- fixed argv, no shell; executable is operator configured
            [
                ffmpeg_bin,
                "-y",
                "-loglevel",
                "error",
                "-i",
                str(mp3),
                "-c:a",
                "libopus",
                "-b:a",
                "48k",
                str(output_path),
            ],
            check=True,
            timeout=120,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )


def send_tts(
    token: str, chat_id: str, text: str, operation_id: int | str | None = None, *, app_settings: AppSettings
) -> bool:
    operation_db = db_connect(app_settings=app_settings) if operation_id is not None else None
    try:
        if operation_db is not None:
            if operation_was_applied(operation_db, operation_id):
                return True
            if not begin_operation(operation_db, operation_id, "tts_delivery"):
                return True
        with tempfile.TemporaryDirectory(prefix="st-voice-") as temp_dir:
            voice_path = Path(temp_dir) / "reply.ogg"
            try:
                synthesize_voice(text, voice_path, app_settings=app_settings)
                delivered = send_voice(token, chat_id, voice_path)
                if delivered and operation_db is not None:
                    record_operation(operation_db, operation_id, "tts_delivery")
                    operation_db.commit()
                return delivered
            except (OSError, RuntimeError, ValueError, subprocess.SubprocessError):
                logging.error("TTS generation failed", exc_info=True)
                return False
    finally:
        if operation_db is not None:
            operation_db.close()


_STT_MODEL_CACHE = {}


_STT_MODEL_LOCK = threading.Lock()


def transcribe_audio_bytes(
    raw: bytes,
    suffix: str = ".ogg",
    model_name: str | None = None,
    language: str | None = None,
    *,
    app_settings: AppSettings,
) -> str:
    if len(raw) > STT_MAX_BYTES:
        raise ValueError("voice message exceeds 20 MB")
    from faster_whisper import WhisperModel

    model_name = model_name or app_settings.environ.get("SILLYTAVERN_STT_MODEL", STT_DEFAULT_MODEL)
    model = _STT_MODEL_CACHE.get(model_name)
    if model is None:
        with _STT_MODEL_LOCK:
            model = _STT_MODEL_CACHE.get(model_name)
            if model is None:
                model = WhisperModel(model_name, device="cpu", compute_type="int8")
                _STT_MODEL_CACHE[model_name] = model
    with tempfile.NamedTemporaryFile(prefix="st-stt-", suffix=suffix, delete=True) as audio_file:
        audio_file.write(raw)
        audio_file.flush()
        options = {"beam_size": 5, "vad_filter": True}
        if language and language.casefold() not in {"auto", "detect"}:
            options["language"] = language
        segments, _info = model.transcribe(audio_file.name, **options)
        text = " ".join(segment.text.strip() for segment in segments if segment.text.strip())
    if not text:
        raise ValueError("voice message produced no transcription")
    return text[:12000]
