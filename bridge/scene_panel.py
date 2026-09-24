"""Pure presentation data for the Scene State panel."""
from __future__ import annotations

import json


def scene_panel(
    state: dict[str, object] | None,
    covered_until_rowid: int,
) -> tuple[str, dict]:
    body = (
        json.dumps(
            state,
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
        )
        if state
        else "No structured scene state has been established yet."
    )
    text = (
        f"Scene state (through message row {covered_until_rowid})\n\n"
        + body
    )
    markup = {
        "inline_keyboard": [
            [
                {
                    "text": "🔄 Refresh",
                    "callback_data": "scene:refresh",
                },
                {
                    "text": "🧹 Clear",
                    "callback_data": "scene:clear",
                },
            ],
            [
                {
                    "text": "⬅️ Status",
                    "callback_data": "scene:status",
                },
                {
                    "text": "❌ Close",
                    "callback_data": "scene:close",
                },
            ],
        ]
    }
    return text, markup
