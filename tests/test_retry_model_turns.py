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


    def test_failed_turn_records_resolved_session_model(self):
        now = time.time()
        self.db.execute(
            "INSERT INTO sessions("
            "chat_id,session_id,title,character_file,model_id,persona_id,"
            "world_file,author_note,system_prompt,response_language,"
            "created_at,updated_at"
            ") VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "chat",
                "session-model",
                "Model session",
                "character.png",
                "cline-pass::cline-pass/glm-5.2",
                "",
                "",
                "",
                "",
                "auto",
                now,
                now,
            ),
        )
        self.db.commit()

        database.record_failed_turn(
            self.db,
            "chat",
            61,
            "normal prompt",
            "provider-one::provider-one/model-a",
            "provider failed",
            "session-model",
        )

        failed = database.latest_failed_turn(self.db, "chat")
        self.assertIsNotNone(failed)
        self.assertEqual(
            failed[2],
            "cline-pass::cline-pass/glm-5.2",
        )

    def test_failed_turn_upsert_refreshes_resolved_session_model(self):
        now = time.time()
        self.db.execute(
            "INSERT INTO sessions("
            "chat_id,session_id,title,character_file,model_id,persona_id,"
            "world_file,author_note,system_prompt,response_language,"
            "created_at,updated_at"
            ") VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "chat",
                "session-refresh",
                "Refresh session",
                "character.png",
                "provider-one::provider-one/model-a",
                "",
                "",
                "",
                "",
                "auto",
                now,
                now,
            ),
        )
        self.db.commit()
        database.record_failed_turn(
            self.db,
            "chat",
            62,
            "normal prompt",
            "provider-one::provider-one/model-a",
            "first failure",
            "session-refresh",
        )

        self.db.execute(
            "UPDATE sessions SET model_id=? "
            "WHERE chat_id=? AND session_id=?",
            (
                "cline-pass::cline-pass/glm-5.2",
                "chat",
                "session-refresh",
            ),
        )
        self.db.commit()
        database.record_failed_turn(
            self.db,
            "chat",
            62,
            "normal prompt",
            "provider-one::provider-one/model-a",
            "retry failure",
            "session-refresh",
        )

        failed = database.latest_failed_turn(self.db, "chat")
        self.assertIsNotNone(failed)
        self.assertEqual(
            failed[2],
            "cline-pass::cline-pass/glm-5.2",
        )
        self.assertEqual(failed[3], 2)


if __name__ == "__main__":
    unittest.main()
