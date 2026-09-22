from __future__ import annotations

from pathlib import Path
import tempfile
import time
import unittest

import bridge.database as database


class RetryModelTurnOnlyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = database.db_connect(Path(self.tmp.name) / "retry.sqlite3")

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def test_command_failures_do_not_enter_model_retry_queue(self):
        for index, text in enumerate(
            (
                "/regen",
                "/status",
                "/retry@BridgeBot",
                "@BridgeBot /continue",
                "start",
            ),
            start=1,
        ):
            database.record_failed_turn(
                self.db,
                "chat",
                index,
                text,
                "provider::model",
                "command failed",
                "session",
            )

        count = self.db.execute(
            "SELECT COUNT(*) FROM failed_turns WHERE chat_id=?",
            ("chat",),
        ).fetchone()[0]
        self.assertEqual(count, 0)
        self.assertIsNone(database.latest_failed_turn(self.db, "chat"))

    def test_start_with_text_remains_retryable_model_turn(self):
        database.record_failed_turn(
            self.db,
            "chat",
            51,
            "start hello",
            "provider::model",
            "model failed",
            "session-b",
        )

        failed = database.latest_failed_turn(self.db, "chat")

        self.assertIsNotNone(failed)
        self.assertEqual(str(failed[0]), "51")
        self.assertEqual(failed[1], "start hello")

    def test_latest_failed_turn_skips_legacy_command_rows(self):
        database.record_failed_turn(
            self.db,
            "chat",
            41,
            "normal user prompt",
            "provider::model",
            "model failed",
            "session-a",
        )
        now = time.time()
        self.db.execute(
            "INSERT INTO failed_turns("
            "chat_id,telegram_message_id,text,model,session_id,attempts,"
            "last_error,created_at,updated_at"
            ") VALUES(?,?,?,?,?,?,?,?,?)",
            (
                "chat",
                "42",
                "/regen",
                "provider::model",
                "session-a",
                1,
                "legacy command failure",
                now,
                now + 10,
            ),
        )
        self.db.commit()

        failed = database.latest_failed_turn(self.db, "chat")

        self.assertIsNotNone(failed)
        self.assertEqual(str(failed[0]), "41")
        self.assertEqual(failed[1], "normal user prompt")


if __name__ == "__main__":
    unittest.main()
