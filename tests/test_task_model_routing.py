from pathlib import Path
import tempfile
import unittest

import bridge.config as config
import time
from dependency_patch import dependency_module

_m_groups = dependency_module("bridge.groups")
_m_memory_curator = dependency_module("bridge.memory_curator")
_m_panel_callback_routes = dependency_module("bridge.panel_callback_routes")
_m_session_naming = dependency_module("bridge.session_naming")


class TaskModelRoutingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_db = config.DB_FILE
        config.DB_FILE = Path(self.tmp.name) / "bridge.sqlite3"
        self.db = _m_memory_curator.db_connect()
        self.session = _m_session_naming.create_session(
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
            _m_memory_curator.task_model_for_session(self.db, "chat", self.session, "summary"),
            "primary::main-model",
        )

    def test_summary_uses_session_utility_model(self):
        _m_panel_callback_routes.set_task_model(
            self.db,
            "chat",
            self.session["session_id"],
            "cheap::summary-model",
        )
        now = time.time()
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
        original_generate = _m_memory_curator.generate_text
        _m_memory_curator.generate_text = lambda _key, model, _messages, **_kwargs: seen_models.append(model) or "Blue key in drawer."
        try:
            summary = _m_groups.generate_session_summary(self.db, "chat", self.session, force=True)
        finally:
            _m_memory_curator.generate_text = original_generate

        self.assertEqual(summary, "Blue key in drawer.")
        self.assertEqual(seen_models, ["cheap::summary-model"])

    def test_main_clears_utility_override(self):
        _m_panel_callback_routes.set_task_model(self.db, "chat", self.session["session_id"], "cheap::summary-model")
        _m_panel_callback_routes.set_task_model(self.db, "chat", self.session["session_id"], "main")
        self.assertEqual(
            _m_memory_curator.task_model_for_session(self.db, "chat", self.session, "summary"),
            "primary::main-model",
        )


if __name__ == "__main__":
    unittest.main()
