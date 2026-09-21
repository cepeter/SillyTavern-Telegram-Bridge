from pathlib import Path
import tempfile
import unittest

import bridge.config as config
import bridge.runtime as rt


class TaskModelRoutingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_db = config.DB_FILE
        config.DB_FILE = Path(self.tmp.name) / "bridge.sqlite3"
        self.db = rt.db_connect()
        self.session = rt.create_session(
            self.db,
            "chat",
            "primary::main-model",
            session_id="task-routing",
            title="Task routing",
        )

    def tearDown(self):
        self.db.close()
        config.DB_FILE = self.old_db
        self.tmp.cleanup()

    def test_utility_route_defaults_to_main_model(self):
        self.assertEqual(
            rt.task_model_for_session(self.db, "chat", self.session, "summary"),
            "primary::main-model",
        )

    def test_summary_uses_session_utility_model(self):
        rt.set_task_model(
            self.db,
            "chat",
            self.session["session_id"],
            "cheap::summary-model",
        )
        now = rt.time.time()
        self.db.execute(
            "INSERT INTO messages(chat_id,session_id,role,content,created_at) VALUES(?,?,?,?,?)",
            ("chat", self.session["session_id"], "user", "Remember the blue key.", now),
        )
        self.db.execute(
            "INSERT INTO messages(chat_id,session_id,role,content,created_at) VALUES(?,?,?,?,?)",
            ("chat", self.session["session_id"], "assistant", "The blue key is in the drawer.", now + 0.001),
        )
        self.db.commit()

        seen_models = []
        original_generate = rt.generate_text
        rt.generate_text = lambda _key, model, _messages, **_kwargs: seen_models.append(model) or "Blue key in drawer."
        try:
            summary = rt.generate_session_summary(self.db, "chat", self.session, force=True)
        finally:
            rt.generate_text = original_generate

        self.assertEqual(summary, "Blue key in drawer.")
        self.assertEqual(seen_models, ["cheap::summary-model"])

    def test_main_clears_utility_override(self):
        rt.set_task_model(self.db, "chat", self.session["session_id"], "cheap::summary-model")
        rt.set_task_model(self.db, "chat", self.session["session_id"], "main")
        self.assertEqual(
            rt.task_model_for_session(self.db, "chat", self.session, "summary"),
            "primary::main-model",
        )


if __name__ == "__main__":
    unittest.main()
