from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from application_test_setup import (
    ensure_application_extensions,
    make_test_memory_service,
    make_test_persona_service,
    make_test_provider_port,
)

ensure_application_extensions()

import bridge.commands as commands
import bridge.database as database
import bridge.telegram as telegram


class NativeEditedMessageSessionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = database.db_connect(Path(self.tmp.name) / "edit.sqlite3")
        self.model = "provider::model"

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def test_edit_uses_message_owning_session_without_switching_active_session(self):
        session_a = telegram.create_session(
            self.db,
            "chat",
            self.model,
            session_id="session-a",
            title="A",
        )
        telegram.update_session(
            self.db,
            "chat",
            session_a["session_id"],
            character_file="a.png",
        )
        now = time.time()
        cursor = self.db.execute(
            "INSERT INTO messages(chat_id,session_id,role,content,telegram_message_id,created_at) VALUES(?,?,?,?,?,?)",
            ("chat", "session-a", "user", "original", "77", now),
        )
        self.db.commit()
        user_rowid = int(cursor.lastrowid)

        session_b = telegram.create_session(
            self.db,
            "chat",
            self.model,
            session_id="session-b",
            title="B",
        )
        telegram.update_session(
            self.db,
            "chat",
            session_b["session_id"],
            character_file="b.png",
        )
        self.assertEqual(
            database.get_meta(self.db, "active_session:chat", ""),
            "session-b",
        )

        captured = {}
        sent = []

        def fake_card_fields(filename):
            captured["character_file"] = filename
            return {"name": "Mira"}

        def fake_regenerate(
            _db,
            _token,
            _api_key,
            session,
            fields,
            _chat_id,
            rowid,
            new_text,
            **kwargs,
        ):
            captured.update(
                {
                    "session_id": session["session_id"],
                    "fields": fields,
                    "rowid": rowid,
                    "new_text": new_text,
                    "provider_port": kwargs["provider_port"],
                    "memory_service": kwargs["memory_service"],
                    "persona_service": kwargs["persona_service"],
                }
            )

        provider = make_test_provider_port()
        memory = make_test_memory_service()
        persona = make_test_persona_service()
        with (
            patch.object(
                commands,
                "card_fields_from_file",
                side_effect=fake_card_fields,
            ),
            patch.object(
                commands,
                "regenerate_edited_turn",
                side_effect=fake_regenerate,
            ),
            patch.object(
                commands,
                "send_text",
                side_effect=lambda _token, _chat_id, text: sent.append(text),
            ),
        ):
            commands.edit_telegram_user_message(
                self.db,
                "token",
                "key",
                "chat",
                77,
                "replacement",
                self.model,
                provider_port=provider,
                memory_service=memory,
                persona_service=persona,
            )

        self.assertEqual(captured["session_id"], "session-a")
        self.assertEqual(captured["character_file"], "a.png")
        self.assertEqual(captured["rowid"], user_rowid)
        self.assertEqual(captured["new_text"], "replacement")
        self.assertIs(captured["provider_port"], provider)
        self.assertIs(captured["memory_service"], memory)
        self.assertIs(captured["persona_service"], persona)
        self.assertEqual(sent, [])
        self.assertEqual(
            database.get_meta(self.db, "active_session:chat", ""),
            "session-b",
        )


if __name__ == "__main__":
    unittest.main()
