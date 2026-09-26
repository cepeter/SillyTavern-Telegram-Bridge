import pytest
from application_test_setup import make_test_persona_service
from test_character_mutation_safety import _card_png
from test_light_novel_storage import novel_db as novel_db

from bridge.conversation_lifecycle import conversation_state, mark_started
from bridge.session_core import create_session


@pytest.fixture
def setup(novel_db):
    from bridge.conversation_setup import ConversationSetupService

    db, session, settings = novel_db
    settings.character_dir.mkdir(parents=True, exist_ok=True)
    (settings.character_dir / "Alice.png").write_bytes(_card_png("Alice", "The scene"))
    service = ConversationSetupService(settings, make_test_persona_service())
    state = service.begin(db, "chat", session, "owner", "Alice.png")
    return db, session, service, state


def choose(setup, stage, value="", action="pick"):
    db, session, service, state = setup
    return service.choose(db, "chat", session, "owner", state["nonce"], stage, action, value)


def ready(setup, mode="normal"):
    choose(setup, "mode", mode)
    if mode == "lightnovel":
        choose(setup, "strategy", "b")
    choose(setup, "persona", "")
    choose(setup, "world", "", "off")
    choose(setup, "system_prompt", "")
    return choose(setup, "session", "story")


def test_normal_wizard_skips_strategy_and_does_not_mutate_session(setup):
    db, _, service, state = setup
    original = db.execute("SELECT * FROM sessions").fetchall()
    assert choose(setup, "mode", "normal")["stage"] == "persona"
    assert db.execute("SELECT * FROM sessions").fetchall() == original
    assert not conversation_state(db, "chat", "story").started
    assert service.load(db, "chat", "story", "owner")["nonce"] == state["nonce"]


def test_light_novel_wizard_requires_strategy(setup):
    assert choose(setup, "mode", "lightnovel")["stage"] == "strategy"
    with pytest.raises(ValueError):
        choose(setup, "persona", "")
    with pytest.raises(ValueError):
        choose(setup, "strategy", "d")
    assert choose(setup, "strategy", "c")["stage"] == "persona"


def test_setup_apply_atomic_with_mode_and_unstarted_target(setup):
    db, session, service, state = setup
    ready(setup, "lightnovel")
    result = service.apply(db, "chat", session, "owner", state["nonce"])
    assert result["session_id"] == "story"
    assert db.execute("SELECT character_file FROM sessions WHERE session_id='story'").fetchone() == ("Alice.png",)
    mode = conversation_state(db, "chat", "story")
    assert (mode.mode, mode.strategy, mode.started) == ("lightnovel", "b", False)
    with pytest.raises(ValueError):
        service.load(db, "chat", "story", "owner")


def test_setup_actor_and_source_epoch_are_bound(setup):
    from bridge.conversation_lifecycle import reset_conversation

    db, _, service, _ = setup
    with pytest.raises(ValueError):
        service.load(db, "chat", "story", "thief")
    reset_conversation(db, "chat", "story")
    with pytest.raises(ValueError):
        choose(setup, "mode", "normal")


def test_setup_started_target_rejected_without_partial_changes(setup):
    db, session, service, state = setup
    ready(setup)
    mark_started(db, "chat", "story", conversation_state(db, "chat", "story").epoch)
    original = db.execute("SELECT * FROM sessions").fetchall()
    with pytest.raises(ValueError, match="already started"):
        service.apply(db, "chat", session, "owner", state["nonce"])
    assert db.execute("SELECT * FROM sessions").fetchall() == original


def test_deleted_resource_refuses_whole_setup(setup):
    db, session, service, state = setup
    ready(setup)
    (service.app_settings.character_dir / "Alice.png").unlink()
    original = db.execute("SELECT * FROM sessions").fetchall()
    with pytest.raises(ValueError):
        service.apply(db, "chat", session, "owner", state["nonce"])
    assert db.execute("SELECT * FROM sessions").fetchall() == original


def test_setup_new_session_created_only_at_apply(setup):
    db, session, service, state = setup
    ready(setup)
    service.back(db, "chat", session, "owner", state["nonce"])
    assert choose(setup, "session", "$new")["stage"] == "session_name"
    assert db.execute("SELECT count(*) FROM sessions").fetchone()[0] == 1
    service.set_title(db, "chat", session, "owner", "A new adventure")
    result = service.apply(db, "chat", session, "owner", state["nonce"])
    assert result["title"] == "A new adventure"
    assert result["character_file"] == "Alice.png"
    assert not conversation_state(db, "chat", result["session_id"]).started


def test_setup_off_choices_and_cancel_preserve_configuration(setup):
    db, session, service, state = setup
    ready(setup)
    service.cancel(db, "chat", session, "owner", state["nonce"])
    assert conversation_state(db, "chat", "story").mode == "normal"
    assert db.execute("SELECT character_file FROM sessions WHERE session_id='story'").fetchone()[0] != "Alice.png"


def test_setup_panel_mode_buttons_are_opaque_and_scoped(setup, monkeypatch):
    from bridge import conversation_setup_panels as panels
    from bridge.callback_tokens import resolve_dynamic_callback_token
    from bridge.request_types import RequestContext
    import json

    db, session, service, state = setup
    sent = []
    monkeypatch.setattr(
        panels, "send_panel_request", lambda token, method, payload, **kw: sent.append(payload) or {"message_id": 55}
    )
    panels.send_setup_panel(
        "token",
        "chat",
        state,
        request_context=RequestContext(db, "story", "owner", app_settings=service.app_settings),
        persona_service=service.persona_service,
    )
    buttons = [button for row in sent[-1]["reply_markup"]["inline_keyboard"] for button in row]
    assert {"Normal", "Light Novel"} <= {button["text"] for button in buttons}
    picked = next(button for button in buttons if button["text"] == "Light Novel")
    token = picked["callback_data"].split(":", 1)[1]
    assert len(picked["callback_data"].encode()) <= 64
    decoded = json.loads(resolve_dynamic_callback_token(token, "conversation_setup", "chat", db=db))
    assert decoded == {"nonce": state["nonce"], "stage": "mode", "action": "pick", "value": "lightnovel"}


def test_setup_callbacks_advance_without_changing_current_session(setup, monkeypatch):
    from bridge import conversation_setup_callbacks as callbacks
    from bridge.callback_tokens import dynamic_callback_token
    from bridge.request_types import RequestContext
    import json

    db, session, service, state = setup
    data = "setup:" + dynamic_callback_token(
        "conversation_setup",
        json.dumps({"nonce": state["nonce"], "stage": "mode", "action": "pick", "value": "lightnovel"}),
        "chat",
        db=db,
    )
    panels = []
    monkeypatch.setattr(callbacks, "send_setup_panel", lambda *a, **k: panels.append(a[2]))
    handled = callbacks.handle_setup_callback(
        db,
        "token",
        {"id": "callback"},
        lambda *a: None,
        data,
        "chat",
        {"message_id": 55},
        session,
        persona_service=service.persona_service,
        request_context=RequestContext(db, "story", "owner", app_settings=service.app_settings),
    )
    assert handled
    assert panels[-1]["stage"] == "strategy"
    assert conversation_state(db, "chat", "story").mode == "normal"
