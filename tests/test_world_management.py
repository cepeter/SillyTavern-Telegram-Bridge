from application_test_setup import ensure_application_extensions, make_test_request_context, make_test_group_service

ensure_application_extensions()

import json
import tempfile
import time
import unittest
from pathlib import Path

import bridge.config as config
import bridge.callbacks as _m_callbacks
import bridge.telegram as _m_telegram
import bridge.catalog as _m_catalog
import bridge.cards as _m_cards
import bridge.memory_curator as _m_memory_curator
import bridge.panel_callback_routes as _m_panel_callback_routes
import bridge.session_naming as _m_session_naming
class WorldManagementTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.old_world = _m_catalog.WORLD_DIR
        self.old_config_world = config.WORLD_DIR
        self.old_db = config.DB_FILE
        world_dir = root / "worlds"
        _m_catalog.WORLD_DIR = world_dir
        config.WORLD_DIR = world_dir
        world_dir.mkdir()
        config.DB_FILE = root / "bridge.sqlite3"
        self.db = _m_memory_curator.db_connect()
        self.session = _m_telegram.ensure_session(self.db, "chat", _m_memory_curator.DEFAULT_MODEL)

    def tearDown(self):
        self.db.close()
        _m_catalog.WORLD_DIR = self.old_world
        config.WORLD_DIR = self.old_config_world
        config.DB_FILE = self.old_db
        self.tmp.cleanup()

    def test_upload_accepts_world_info_and_refuses_duplicate(self):
        raw = json.dumps({"entries": {"0": {"key": ["dragon"], "content": "A dragon."}}}).encode()
        installed = _m_catalog.install_world_info_document("dragons.json", raw)
        self.assertEqual(installed.name, "dragons.json")
        self.assertEqual(installed.read_bytes(), raw)
        with self.assertRaises(FileExistsError):
            _m_catalog.install_world_info_document("dragons.json", raw)
        with self.assertRaises(ValueError):
            _m_catalog.install_world_info_document("not-world.txt", raw)
        with self.assertRaises(ValueError):
            _m_catalog.install_world_info_document("bad.json", b"{}");

    def test_delete_removes_inactive_world_info_and_protects_references(self):
        path = _m_catalog.WORLD_DIR / "lore.json"
        path.write_text(json.dumps({"entries": {}}), encoding="utf-8")
        _m_panel_callback_routes.delete_world_info_file(self.db, "chat", "lore.json")
        self.assertFalse(path.exists())

        path.write_text(json.dumps({"entries": {}}), encoding="utf-8")
        self.db.execute("UPDATE sessions SET world_file=? WHERE chat_id=? AND session_id=?", ("lore.json", "chat", self.session["session_id"]))
        self.db.commit()
        with self.assertRaises(ValueError):
            _m_panel_callback_routes.delete_world_info_file(self.db, "chat", "lore.json")
        self.assertTrue(path.exists())

    def test_world_panel_has_upload_and_delete_actions(self):
        (_m_catalog.WORLD_DIR / "lore.json").write_text(json.dumps({"entries": {}}), encoding="utf-8")
        calls = []
        original_request = _m_cards.send_panel_request
        _m_cards.send_panel_request = lambda _token, method, payload, **_kwargs: calls.append((method, payload)) or {}
        try:
            _m_panel_callback_routes.send_world_menu("token", "chat", "", request_context=make_test_request_context(self.db, self.session["session_id"]))
        finally:
            _m_cards.send_panel_request = original_request
        keyboard = calls[-1][1]["reply_markup"]["inline_keyboard"]
        callbacks = [button["callback_data"] for row in keyboard for button in row]
        self.assertIn("world:upload", callbacks)
        self.assertTrue(any(value.startswith("worlddelete:") for value in callbacks))

    def test_upload_callback_stores_scoped_pending_state(self):
        callback = {"id": "cb", "data": "world:upload", "message": {"message_id": 7}}
        answers = []
        sent = []
        original_send = _m_panel_callback_routes.send_text
        original_discard = _m_panel_callback_routes.discard_panel_binding
        _m_panel_callback_routes.send_text = lambda *_args, **_kwargs: sent.append(True) or []
        _m_panel_callback_routes.discard_panel_binding = lambda *_args, **_kwargs: None
        try:
            _m_panel_callback_routes.handle_world_callback(self.db, "token", callback, lambda *_args: answers.append(True), callback["data"], "chat", callback["message"], self.session, self.session["session_id"], None, group_service=make_test_group_service(), request_context=make_test_request_context(self.db, self.session["session_id"]))
        finally:
            _m_panel_callback_routes.send_text = original_send
            _m_panel_callback_routes.discard_panel_binding = original_discard
        state = json.loads(_m_session_naming.get_meta(self.db, "world_upload:chat"))
        self.assertEqual(state["session_id"], self.session["session_id"])
        self.assertGreater(state["expires_at"], time.time())
        self.assertTrue(answers)
        self.assertTrue(sent)


if __name__ == "__main__":
    unittest.main()
