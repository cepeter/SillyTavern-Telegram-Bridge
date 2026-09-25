"""Pure presentation data for the Curated Memory panel."""

from __future__ import annotations


def curated_memory_panel(text: str) -> tuple[str, dict]:
    body = text or "No curated durable memories yet."
    panel_text = "Curated memory\n\n" + body
    markup = {
        "inline_keyboard": [
            [{"text": "🔄 Refresh", "callback_data": "curated:refresh"}],
            [
                {"text": "⬅️ Memory", "callback_data": "curated:back"},
                {"text": "❌ Close", "callback_data": "curated:close"},
            ],
        ]
    }
    return panel_text, markup
