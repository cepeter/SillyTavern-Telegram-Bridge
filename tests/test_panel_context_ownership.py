from __future__ import annotations

from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

import bridge.callbacks as callbacks
import bridge.database as database


class PanelContextOwnershipTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = database.db_connect(Path(self.tmp.name) / "panel.sqlite3")
        self.db.execute(
            "INSERT INTO panel_sessions("
            "chat_id,message_id,session_id,owner_user_id,expires_at"
            ") VALUES(?,?,?,?,?)",
            ("chat", "77", "session-a", "user-a", time.time() + 300),
        )
        self.db.commit()

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def test_close_panel_uses_explicit_database(self):
        callback = {"message": {"message_id": 77}}
        with patch.object(
            callbacks,
            "telegram_request",
            return_value={},
        ), patch.object(
            callbacks,
            "db_connect",
            side_effect=AssertionError("hidden database discovery must not run"),
            create=True,
        ):
            callbacks.close_panel_message(
                self.db,
                "token",
                "chat",
                callback,
            )

        row = self.db.execute(
            "SELECT 1 FROM panel_sessions "
            "WHERE chat_id=? AND message_id=?",
            ("chat", "77"),
        ).fetchone()
        self.assertIsNone(row)


if __name__ == "__main__":
    unittest.main()
