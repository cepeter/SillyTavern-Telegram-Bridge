from __future__ import annotations

import inspect
import tempfile
import time
import unittest
from dataclasses import FrozenInstanceError
from pathlib import Path
from unittest.mock import patch

from application_test_setup import ensure_application_extensions, make_test_application_services
from settings_test_support import SettingsTestCase

import bridge.sqlite_store as _sqlite_store

ensure_application_extensions()

import bridge.callback_dispatch as callback_dispatch
import bridge.callbacks as callbacks
import bridge.request_types as request_types
import bridge.telegram as telegram
import bridge.update as update


class PanelContextOwnershipTests(SettingsTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = _sqlite_store.db_connect(
            Path(self.tmp.name) / "panel.sqlite3", app_settings=self.app_settings_builder.build()
        )
        self.db.execute(
            "INSERT INTO panel_sessions(chat_id,message_id,session_id,owner_user_id,expires_at) VALUES(?,?,?,?,?)",
            ("chat", "77", "session-a", "user-a", time.time() + 300),
        )
        self.db.commit()

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def test_close_panel_uses_explicit_database(self):
        callback = {"message": {"message_id": 77}}
        with (
            patch.object(
                callbacks,
                "telegram_request",
                return_value={},
            ),
            patch.object(
                callbacks,
                "db_connect",
                side_effect=AssertionError("hidden database discovery must not run"),
                create=True,
            ),
        ):
            callbacks.close_panel_message(
                self.db,
                "token",
                "chat",
                callback,
            )

        row = self.db.execute(
            "SELECT 1 FROM panel_sessions WHERE chat_id=? AND message_id=?",
            ("chat", "77"),
        ).fetchone()
        self.assertIsNone(row)

    def test_update_cancel_through_callback_uses_caller_database(self):
        session = telegram.ensure_session(
            self.db, "chat", self.app_settings_builder.default_model, app_settings=self.app_settings_builder.build()
        )
        telegram.bind_panel_session(
            self.db,
            "chat",
            77,
            session["session_id"],
            "user-a",
        )
        callback = {
            "id": "callback-77",
            "from": {"id": "user-a"},
            "data": "update:cancel",
            "message": {
                "message_id": 77,
                "chat": {"id": "chat"},
            },
        }

        with (
            patch.object(
                update,
                "answer_callback",
                return_value=None,
            ),
            patch.object(
                callbacks,
                "telegram_request",
                return_value={},
            ),
        ):
            callback_dispatch.process_callback(
                self.db,
                "token",
                callback,
                services=make_test_application_services(app_settings=self.app_settings_builder.build()),
            )

        row = self.db.execute(
            "SELECT 1 FROM panel_sessions WHERE chat_id=? AND message_id=?",
            ("chat", "77"),
        ).fetchone()
        self.assertIsNone(row)

    def test_request_context_is_immutable(self):
        self.assertTrue(hasattr(request_types, "RequestContext"))
        context = request_types.RequestContext(
            self.db, "session-a", "user-a", app_settings=self.app_settings_builder.build()
        )

        with self.assertRaises(FrozenInstanceError):
            context.session_id = "session-b"

    def test_panel_request_binds_explicit_session_and_actor(self):
        self.assertTrue(hasattr(telegram, "send_panel_request"))
        context = request_types.RequestContext(
            self.db, "session-explicit", "user-explicit", app_settings=self.app_settings_builder.build()
        )
        with patch.object(
            telegram,
            "telegram_request",
            return_value={"message_id": 79},
        ):
            telegram.send_panel_request(
                "token",
                "sendMessage",
                {
                    "chat_id": "chat",
                    "text": "Panel",
                    "reply_markup": {"inline_keyboard": []},
                },
                request_context=context,
            )

        row = self.db.execute(
            "SELECT session_id,owner_user_id FROM panel_sessions WHERE chat_id=? AND message_id=?",
            ("chat", "79"),
        ).fetchone()
        self.assertEqual(
            tuple(row),
            ("session-explicit", "user-explicit"),
        )

    def test_telegram_request_does_not_read_ambient_panel_context(self):
        source = inspect.getsource(telegram.telegram_request)

        self.assertNotIn("panel_session_context", source)
        self.assertNotIn("panel_actor_context", source)
        self.assertNotIn("db_connection_context", source)


if __name__ == "__main__":
    unittest.main()
