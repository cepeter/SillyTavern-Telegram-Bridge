"""Transcribed voice enters the explicitly injected conversation owner."""

from types import SimpleNamespace

from application_test_setup import make_test_session_service
from settings_test_support import make_test_settings

import bridge.voice_jobs as _owner_voice_jobs


def test_transcript_uses_injected_service_and_preserves_request_identity(monkeypatch):
    delivered = []

    class ConversationRecorder:
        def process_message(self, *args, **kwargs):
            delivered.append((args, kwargs))

    services = SimpleNamespace(
        config=make_test_settings(),
        conversation=ConversationRecorder(),
        session=make_test_session_service(app_settings=make_test_settings()),
    )
    db = object()
    fields = {"name": "character"}
    monkeypatch.setattr(_owner_voice_jobs, "require_started", lambda *a: True)
    monkeypatch.setattr(_owner_voice_jobs, "get_meta", lambda _db, _key, default: default)
    monkeypatch.setattr(_owner_voice_jobs, "download_telegram_file", lambda *_args: b"audio")
    monkeypatch.setattr(_owner_voice_jobs, "transcribe_audio_bytes", lambda *_args, app_settings=None: "spoken message")
    _owner_voice_jobs.process_voice_message(
        db,
        "token",
        "key",
        "model",
        fields,
        "chat",
        {"file_id": "voice-file", "file_size": 5, "file_name": "voice.ogg"},
        42,
        queued_session_id="queued-session",
        actor_id="actor",
        services=services,
    )
    assert delivered == [
        (
            (db, "token", "key", "model", fields, "chat", "spoken message", 42),
            {"queued_session_id": "queued-session", "actor_id": "actor"},
        )
    ]
