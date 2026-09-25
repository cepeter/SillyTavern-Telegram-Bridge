"""Canonical feature panels owner."""

from __future__ import annotations

from bridge.cards import send_panel_message
from bridge.curated_memory_panel import curated_memory_panel
from bridge.director_goal_panel import director_goal_panel
from bridge.director_goals import get_director_goal
from bridge.memory import get_session_summary
from bridge.memory_curator import curated_memory_text
from bridge.scene_panel import scene_panel
from bridge.scene_state import get_scene_state


def send_scene_menu(token, chat_id, db, session, message_id=None, *, request_context):
    state, covered = get_scene_state(db, chat_id, session["session_id"])
    text, markup = scene_panel(state, covered)
    send_panel_message(
        token,
        chat_id,
        text,
        markup,
        message_id,
        request_context=request_context,
    )


def send_director_goal_menu(token, chat_id, db, session, message_id=None, *, request_context):
    goal = get_director_goal(db, chat_id, session["session_id"])
    text, markup = director_goal_panel(goal)
    send_panel_message(
        token,
        chat_id,
        text,
        markup,
        message_id,
        request_context=request_context,
    )


def send_curated_memory_menu(token, chat_id, db, session, message_id=None, *, request_context):
    text = curated_memory_text(db, chat_id, session["session_id"])
    panel_text, markup = curated_memory_panel(text)
    send_panel_message(
        token,
        chat_id,
        panel_text,
        markup,
        message_id,
        request_context=request_context,
    )


def send_summary_menu(token, chat_id, db, session, message_id=None, *, request_context):
    summary, covered = get_session_summary(db, chat_id, session["session_id"])
    state = f"Existing summary: {len(summary)} chars" if summary else "No summary exists yet."
    text = (
        "Session summary\n\n"
        + state
        + (
            "\nCovered through message row: "
            f"""{covered or "none"}"""
            "\n\nRegenerating uses the utility model and may take a while."
        )
    )
    markup = {
        "inline_keyboard": [
            [{"text": "✅ Regenerate summary", "callback_data": "summary:confirm"}],
            [{"text": "❌ Cancel", "callback_data": "summary:cancel"}],
        ]
    }
    send_panel_message(token, chat_id, text, markup, message_id, request_context=request_context)
