"""Pure presentation data for the Director Goal panel."""
from __future__ import annotations


def director_goal_panel(goal: str) -> tuple[str, dict]:
    text = "Director objective\n\n" + (
        str(goal or "").strip() or "No hidden objective is set."
    )
    markup = {
        "inline_keyboard": [
            [{"text": "✏️ Set objective", "callback_data": "goal:set"}],
            [
                {"text": "🧹 Clear", "callback_data": "goal:clear"},
                {"text": "❌ Close", "callback_data": "goal:close"},
            ],
        ]
    }
    return text, markup
