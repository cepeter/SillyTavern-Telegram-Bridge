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
    make_test_rag_service,
)
from settings_test_support import SettingsTestCase

import bridge.edit_messages as _owner_edit_messages
import bridge.metadata as _owner_metadata
import bridge.session_core as _owner_session_core
import bridge.sqlite_store as _sqlite_store

ensure_application_extensions()


class NativeEditedMessageSessionTests(SettingsTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = _sqlite_store.db_connect(
            Path(self.tmp.name) / "edit.sqlite3", app_settings=self.app_settings_builder.build()
        )
        self.model = "provider::model"

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def test_edit_uses_message_owning_session_without_switching_active_session(self):
        session_a = _owner_session_core.create_session(
            self.db,
            "chat",
            self.model,
            session_id="session-a",
            title="A",
            app_settings=self.app_settings_builder.build(),
        )
        _owner_session_core.update_session(
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

        session_b = _owner_session_core.create_session(
            self.db,
            "chat",
            self.model,
            session_id="session-b",
            title="B",
            app_settings=self.app_settings_builder.build(),
        )
        _owner_session_core.update_session(
            self.db,
            "chat",
            session_b["session_id"],
            character_file="b.png",
        )
        self.assertEqual(
            _owner_metadata.get_meta(self.db, "active_session:chat", ""),
            "session-b",
        )

        captured = {}
        sent = []

        def fake_card_fields(filename, *, app_settings=None):
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
            *,
            app_settings=None,
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
                _owner_edit_messages,
                "card_fields_from_file",
                side_effect=fake_card_fields,
            ),
            patch.object(
                _owner_edit_messages,
                "regenerate_edited_turn",
                side_effect=fake_regenerate,
            ),
            patch.object(
                _owner_edit_messages,
                "send_text",
                side_effect=lambda _token, _chat_id, text: sent.append(text),
            ),
        ):
            _owner_edit_messages.edit_telegram_user_message(
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
                app_settings=self.app_settings_builder.build(),
                rag_service=make_test_rag_service(),
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
            _owner_metadata.get_meta(self.db, "active_session:chat", ""),
            "session-b",
        )


if __name__ == "__main__":
    unittest.main()
