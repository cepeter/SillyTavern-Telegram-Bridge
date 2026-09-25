from __future__ import annotations

import ast
from pathlib import Path

from application_test_setup import make_native_test_persona_service, make_test_provider_port

ROOT = Path(__file__).parents[1]
BRIDGE = ROOT / "bridge"


def imported_modules(filename: str) -> set[str]:
    tree = ast.parse((BRIDGE / filename).read_text(encoding="utf-8"))
    result: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            result.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            result.add(node.module)
    return result


def test_persona_sync_does_not_import_input_flows():
    assert "bridge.input_flows" not in imported_modules("persona_sync.py")


def test_input_flows_does_not_own_persona_edit_lock():
    import bridge.input_flows as input_flows

    assert not hasattr(input_flows, "PERSONA_EDIT_LOCK")


def test_native_persona_store_and_service_share_canonical_lock():
    import bridge.persona_sync as persona_sync

    service = make_native_test_persona_service()
    assert persona_sync._PERSONA_STORE.edit_lock() is persona_sync.PERSONA_EDIT_LOCK
    assert service.persona_edit_lock() is persona_sync.PERSONA_EDIT_LOCK


def test_session_title_contract_has_pure_owner():
    import importlib

    import bridge.session_naming as session_naming

    owner = BRIDGE / "session_titles.py"
    assert owner.is_file()
    session_titles = importlib.import_module("bridge.session_titles")
    assert session_titles.SESSION_TITLE_MAX_CHARS == 80
    assert not hasattr(session_naming, "SESSION_TITLE_MAX_CHARS")
    assert not hasattr(session_naming, "normalize_session_title")
    assert "bridge.session_naming" not in imported_modules("telegram.py")


def test_session_title_contract_behavior_is_unchanged():
    import importlib

    import pytest

    owner = BRIDGE / "session_titles.py"
    assert owner.is_file()
    normalize = importlib.import_module("bridge.session_titles").normalize_session_title
    assert normalize(" A   name ") == "A name"
    with pytest.raises(ValueError, match="Session name must contain 1–80"):
        normalize("x" * 81)
    with pytest.raises(ValueError, match="cannot start with /"):
        normalize("/bad")


def test_telegram_has_no_application_command_backedge():
    import bridge.telegram as telegram

    assert "bridge.commands" not in imported_modules("telegram.py")
    assert not hasattr(telegram, "process_telegram_image")


def test_telegram_runtime_requires_download_transport():
    from bridge.composition import TelegramRuntime

    assert "download_file" in TelegramRuntime.__dataclass_fields__
    assert TelegramRuntime.__dataclass_fields__["download_file"].default.__class__.__name__ == "_MISSING_TYPE"


def _image_services(download_file, sent):
    from types import SimpleNamespace

    return SimpleNamespace(
        config=SimpleNamespace(
            bot_token="token",
            api_key="configured-key",
            default_model="queue-model",
        ),
        db_factory=lambda: __import__("sqlite3").connect(":memory:"),
        jobs=SimpleNamespace(
            start=lambda *_args: True,
            complete=lambda *_args: True,
            fail=lambda *_args: True,
        ),
        telegram=SimpleNamespace(
            download_file=download_file,
            send_text=lambda *args, **_kwargs: sent.append(args),
        ),
        group=object(),
        provider=make_test_provider_port(),
        memory=object(),
        persona=object(),
        group_director=object(),
    )


def test_image_worker_rejects_oversize_before_transport(monkeypatch):
    import bridge.worker_orchestration as workers
    from bridge.common import IMAGE_MAX_BYTES

    delegated = []
    downloads = []
    sent = []
    services = _image_services(
        lambda *args, **kwargs: downloads.append((args, kwargs)) or b"image",
        sent,
    )
    monkeypatch.setattr(workers, "committed_assistant_for_message", lambda *_args: None)
    monkeypatch.setattr(
        workers,
        "process_telegram_image",
        lambda *args, **kwargs: delegated.append((args, kwargs)),
        raising=False,
    )

    workers.process_image_job(
        services,
        "chat",
        "file-id",
        "caption",
        IMAGE_MAX_BYTES + 1,
        55,
    )

    assert delegated == []
    assert downloads == []
    assert sent == [("token", "chat", "Image is too large. The limit is 8 MB.")]


def test_image_worker_uses_injected_download_and_forwards_identity(monkeypatch):
    import bridge.worker_orchestration as workers

    downloads = []
    delivered = []
    sent = []
    memory = object()
    persona = object()
    director = object()
    services = _image_services(
        lambda *args, **kwargs: downloads.append((args, kwargs)) or b"raw-image",
        sent,
    )
    services.memory = memory
    services.persona = persona
    services.group_director = director
    session = {
        "session_id": "queued-session",
        "character_file": "mira.png",
        "model_id": "resolved-model",
    }
    monkeypatch.setattr(workers, "committed_assistant_for_message", lambda *_args: None)
    monkeypatch.setattr(workers, "load_session", lambda *_args: session)
    monkeypatch.setattr(
        workers,
        "card_fields_from_file",
        lambda filename: {"name": "Mira", "source": filename},
        raising=False,
    )
    monkeypatch.setattr(
        workers,
        "process_image_message",
        lambda *args, **kwargs: delivered.append((args, kwargs)),
        raising=False,
    )
    monkeypatch.setattr(
        workers,
        "process_telegram_image",
        lambda *_args, **_kwargs: None,
        raising=False,
    )

    workers.process_image_job(
        services,
        "chat",
        "file-id",
        "caption",
        123,
        55,
        queued_session_id="queued-session",
        model_override="stored-queue-model",
    )

    assert downloads == [(("token", "file-id", 8 * 1024 * 1024), {})]
    assert len(delivered) == 1
    args, kwargs = delivered[0]
    assert args[1:8] == (
        "token",
        "configured-key",
        session,
        {"name": "Mira", "source": "mira.png"},
        "chat",
        "caption",
        b"raw-image",
    )
    assert kwargs["telegram_message_id"] == 55
    assert kwargs["memory_service"] is memory
    assert kwargs["persona_service"] is persona
    assert kwargs["group_director_service"] is director


def test_committed_image_recovery_returns_before_download(monkeypatch):
    import bridge.worker_orchestration as workers

    downloads = []
    replies = []
    services = _image_services(
        lambda *args, **kwargs: downloads.append((args, kwargs)) or b"raw",
        [],
    )
    monkeypatch.setattr(
        workers,
        "committed_assistant_for_message",
        lambda *_args: (7, "already committed", "[]"),
    )
    monkeypatch.setattr(
        workers,
        "load_session",
        lambda *_args: {"session_id": "queued-session"},
    )
    monkeypatch.setattr(
        workers,
        "send_reply",
        lambda *args, **kwargs: replies.append((args, kwargs)),
    )
    monkeypatch.setattr(workers, "clear_failed_turn", lambda *_args: None)

    workers.process_image_job(
        services,
        "chat",
        "file-id",
        "caption",
        12,
        55,
        queued_session_id="queued-session",
    )

    assert downloads == []
    assert len(replies) == 1


def _assert_document_injection_signature():
    import inspect

    import bridge.telegram as telegram

    params = inspect.signature(telegram.import_telegram_document).parameters
    assert "api_key" in params
    assert "process_image" in params


def _run_document_import(monkeypatch, document, *, parse_card, add_document=None):
    import bridge.telegram as telegram

    _assert_document_injection_signature()
    image_calls = []
    sent = []
    monkeypatch.setattr(telegram, "_consume_world_upload", lambda *_args: False)
    monkeypatch.setattr(
        telegram,
        "download_telegram_file",
        lambda *_args, **_kwargs: b"raw-document",
    )
    if isinstance(parse_card, BaseException):
        monkeypatch.setattr(
            telegram,
            "parse_png_chara_bytes",
            lambda *_args: (_ for _ in ()).throw(parse_card),
        )
    else:
        monkeypatch.setattr(telegram, "parse_png_chara_bytes", lambda *_args: parse_card)
    monkeypatch.setattr(
        telegram,
        "ensure_session",
        lambda *_args: {
            "session_id": "session",
            "character_file": "mira.png",
            "model_id": "model",
        },
    )
    monkeypatch.setattr(
        telegram,
        "card_fields_from_file",
        lambda filename: {"name": "Mira", "source": filename},
    )
    monkeypatch.setattr(telegram, "import_character_card", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(telegram, "send_text", lambda *args, **_kwargs: sent.append(args))
    if add_document is not None:
        monkeypatch.setattr(telegram, "add_data_bank_document", add_document)
        monkeypatch.setattr(telegram, "rag_mode", lambda *_args: "on")

    telegram.import_telegram_document(
        object(),
        "token",
        "chat",
        document,
        "queue-model",
        api_key="sentinel-key",
        process_image=lambda *args, **kwargs: image_calls.append((args, kwargs)),
        telegram_message_id=77,
        memory_service="memory",
        persona_service="persona",
        group_director_service="director",
    )
    return image_calls, sent


def test_document_non_character_png_uses_explicit_api_key_and_image_collaborator(monkeypatch):
    calls, _sent = _run_document_import(
        monkeypatch,
        {"file_name": "photo.png", "file_id": "file", "caption": "caption"},
        parse_card=ValueError("not a card"),
    )

    assert len(calls) == 1
    args, kwargs = calls[0]
    assert args[1] == "token"
    assert args[2] == "sentinel-key"
    assert args[5] == "chat"
    assert args[6] == "caption"
    assert args[7] == b"raw-document"
    assert kwargs["telegram_message_id"] == 77
    assert kwargs["memory_service"] == "memory"
    assert kwargs["persona_service"] == "persona"
    assert kwargs["group_director_service"] == "director"


def test_document_character_card_png_does_not_call_image_collaborator(monkeypatch):
    calls, _sent = _run_document_import(
        monkeypatch,
        {"file_name": "character.png", "file_id": "file"},
        parse_card={"name": "character"},
    )
    assert calls == []


def test_document_data_bank_branch_does_not_call_image_collaborator(monkeypatch):
    calls, _sent = _run_document_import(
        monkeypatch,
        {"file_name": "notes.txt", "file_id": "file"},
        parse_card={},
        add_document=lambda *_args: ("added", 2),
    )
    assert calls == []


def test_document_job_passes_configured_api_key_and_canonical_image_collaborator(monkeypatch):
    import sqlite3
    from types import SimpleNamespace

    import bridge.help as help_module

    captured = {}
    services = SimpleNamespace(
        config=SimpleNamespace(
            bot_token="token",
            api_key="configured-key",
            default_model="model",
        ),
        db_factory=lambda: sqlite3.connect(":memory:"),
        jobs=SimpleNamespace(
            start=lambda *_args: True,
            complete=lambda *_args: True,
            fail=lambda *_args: True,
        ),
        telegram=SimpleNamespace(send_text=lambda *_args, **_kwargs: None),
        provider=make_test_provider_port(),
        memory="memory",
        persona="persona",
        group_director="director",
    )
    monkeypatch.setattr(
        help_module,
        "import_telegram_document",
        lambda *args, **kwargs: captured.update(kwargs),
    )

    help_module.process_document_job(
        services,
        "chat",
        {"file_name": "photo.png"},
        77,
    )

    assert captured["api_key"] == "configured-key"
    process_image = captured["process_image"]
    assert process_image.func is help_module.process_image_message
    assert process_image.keywords["provider_port"] is services.provider


def test_document_image_collaborator_has_explicit_callable_contract():
    import inspect

    import bridge.telegram as telegram

    annotation = inspect.signature(telegram.import_telegram_document).parameters["process_image"].annotation
    assert str(annotation) == "Callable[..., None]"
