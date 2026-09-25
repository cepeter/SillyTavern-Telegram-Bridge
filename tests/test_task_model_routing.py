from application_test_setup import ensure_application_extensions, make_test_provider_port

ensure_application_extensions()

import tempfile
import time
import unittest
from pathlib import Path

import bridge.config as config
import bridge.groups as _m_groups
import bridge.memory_curator as _m_memory_curator
import bridge.panel_callback_routes as _m_panel_callback_routes
import bridge.session_naming as _m_session_naming


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
        provider = make_test_provider_port(
            generate_backend=lambda _key, model, _messages, **_kwargs: (
                seen_models.append(model) or "Blue key in drawer."
            )
        )
        summary = _m_groups.generate_session_summary(
            self.db,
            "chat",
            self.session,
            force=True,
            provider_port=provider,
        )

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
