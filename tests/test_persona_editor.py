import json
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest

import bridge.runtime as rt


class PersonaEditorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.original_db = rt.DB_FILE
        self.original_persona = rt.PERSONA_FILE
        rt.DB_FILE = Path(self.tmp.name) / "bridge.sqlite3"
        rt.PERSONA_FILE = Path(self.tmp.name) / "personas.json"
        rt.PERSONA_FILE.write_text(json.dumps({
            "punto": {"name": "Punto", "description": "Original description", "tags": ["default"]},
        }), encoding="utf-8")
        with rt._DB_SCHEMA_LOCK:
            rt._DB_SCHEMA_READY = False
        self.db = rt.db_connect()
        self.session = rt.create_session(self.db, "chat", "provider/model", session_id="persona-session")
        self.calls = []
        self.old_request = rt.telegram_request
        self.old_send_text = rt.send_text
        self.old_close = rt.close_panel_message
        rt.telegram_request = lambda _token, method, payload: self.calls.append((method, payload)) or {"message_id": 500}
        rt.send_text = lambda _token, _chat, _text: [501]
        rt.close_panel_message = lambda _token, _chat, _callback: None

    def tearDown(self):
        rt.telegram_request = self.old_request
        rt.send_text = self.old_send_text
        rt.close_panel_message = self.old_close
        self.db.close()
        rt.DB_FILE = self.original_db
        rt.PERSONA_FILE = self.original_persona
        self.tmp.cleanup()

    def _start(self, mode, persona_id=""):
        callback = {"id": "callback", "message": {"message_id": 77}}
        rt.start_persona_input(self.db, "token", "chat", self.session["session_id"], mode, persona_id, callback)
        return json.loads(rt.get_meta(self.db, "persona_input:chat"))

    def test_create_persona_writes_json_and_selects_it(self):
        self._start("create")
        handled = rt.handle_pending_input(self.db, "token", "chat", self.session, "writer | Writer | I write concise notes.")
        self.assertTrue(handled)
        data = json.loads(rt.PERSONA_FILE.read_text(encoding="utf-8"))
        self.assertEqual(data["writer"]["name"], "Writer")
        self.assertEqual(data["writer"]["description"], "I write concise notes.")
        self.assertEqual(data["writer"]["tags"], [])
        stored = rt.load_session(self.db, "chat", "persona-session", "provider/model")
        self.assertEqual(stored["persona_id"], "writer")
        backups = list(Path(self.tmp.name).glob("personas.json.*.bak"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(json.loads(backups[0].read_text(encoding="utf-8"))["punto"]["description"], "Original description")
        self.assertEqual(os.stat(rt.PERSONA_FILE).st_mode & 0o777, 0o600)

    def test_edit_persona_updates_name_and_description(self):
        self._start("edit", "punto")
        handled = rt.handle_pending_input(self.db, "token", "chat", self.session, "Updated Name | Updated description")
        self.assertTrue(handled)
        data = json.loads(rt.PERSONA_FILE.read_text(encoding="utf-8"))
        self.assertEqual(data["punto"]["name"], "Updated Name")
        self.assertEqual(data["punto"]["description"], "Updated description")
        self.assertEqual(data["punto"]["tags"], ["default"])

    def test_edit_callback_shows_current_information_and_field_choices(self):
        answers = []
        handled = rt.handle_persona_callback(self.db, "token", {"id": "cb"}, lambda _t, _i, text: answers.append(text), "persona:edit", "chat", {"message_id": 77}, self.session, "persona-session", None)
        self.assertTrue(handled)
        self.assertEqual(answers, ["Review persona"])
        payload = self.calls[-1][1]
        self.assertIn("Persona information", payload["text"])
        self.assertIn("Original description", payload["text"])
        callbacks = {button["callback_data"] for row in payload["reply_markup"]["inline_keyboard"] for button in row}
        self.assertTrue({"persona:edit_name", "persona:edit_description", "persona:edit_all"}.issubset(callbacks))

    def test_edit_name_only_changes_name(self):
        self._start("edit_name", "punto")
        rt.handle_pending_input(self.db, "token", "chat", self.session, "New Name")
        data = json.loads(rt.PERSONA_FILE.read_text(encoding="utf-8"))
        self.assertEqual(data["punto"]["name"], "New Name")
        self.assertEqual(data["punto"]["description"], "Original description")

    def test_edit_description_only_changes_description(self):
        self._start("edit_description", "punto")
        rt.handle_pending_input(self.db, "token", "chat", self.session, "New Description")
        data = json.loads(rt.PERSONA_FILE.read_text(encoding="utf-8"))
        self.assertEqual(data["punto"]["name"], "Punto")
        self.assertEqual(data["punto"]["description"], "New Description")

    def test_edit_description_only_preserves_name_and_tags(self):
        self._start("edit", "punto")
        rt.handle_pending_input(self.db, "token", "chat", self.session, "Description only")
        data = json.loads(rt.PERSONA_FILE.read_text(encoding="utf-8"))
        self.assertEqual(data["punto"], {"name": "Punto", "description": "Description only", "tags": ["default"]})

    def test_invalid_create_keeps_pending_state_and_file(self):
        state = self._start("create")
        handled = rt.handle_pending_input(self.db, "token", "chat", self.session, "bad input")
        self.assertTrue(handled)
        self.assertEqual(json.loads(rt.PERSONA_FILE.read_text(encoding="utf-8"))["punto"]["description"], "Original description")
        current = json.loads(rt.get_meta(self.db, "persona_input:chat"))
        self.assertEqual(current["mode"], "create")
        self.assertEqual(current["prompt_message_ids"], state["prompt_message_ids"] + [501])
        self.assertEqual(list(Path(self.tmp.name).glob("personas.json.*.bak")), [])

    def test_duplicate_create_is_rejected(self):
        self._start("create")
        rt.handle_pending_input(self.db, "token", "chat", self.session, "punto | Other | Duplicate")
        self.assertEqual(len(json.loads(rt.PERSONA_FILE.read_text(encoding="utf-8"))), 1)
        self.assertTrue(json.loads(rt.get_meta(self.db, "persona_input:chat")))

    def test_malformed_catalog_is_not_overwritten(self):
        self._start("create")
        broken = b"{not valid json"
        rt.PERSONA_FILE.write_bytes(broken)
        handled = rt.handle_pending_input(self.db, "token", "chat", self.session, "writer | Writer | Should not replace catalog")
        self.assertTrue(handled)
        self.assertEqual(rt.PERSONA_FILE.read_bytes(), broken)
        self.assertTrue(json.loads(rt.get_meta(self.db, "persona_input:chat")))

    def test_one_corrupt_entry_is_normalized_without_blocking_edits(self):
        rt.PERSONA_FILE.write_text(json.dumps({
            "punto": {"name": "Punto", "description": "Original description", "tags": ["default"]},
            "broken": {"name": None, "tags": "invalid"},
        }), encoding="utf-8")
        self._start("create")
        handled = rt.handle_pending_input(self.db, "token", "chat", self.session, "writer | Writer | Still editable")
        self.assertTrue(handled)
        data = json.loads(rt.PERSONA_FILE.read_text(encoding="utf-8"))
        self.assertEqual(data["broken"]["name"], "broken")
        self.assertEqual(data["broken"]["description"], "")
        self.assertEqual(data["broken"]["tags"], [])
        self.assertEqual(data["writer"]["description"], "Still editable")

    def test_malformed_catalog_warning_is_visible_in_persona_panel(self):
        rt.PERSONA_FILE.write_text("{broken json", encoding="utf-8")
        original_refresh = rt.refresh_native_persona_cache
        rt.refresh_native_persona_cache = lambda *_args, **_kwargs: "skipped"
        try:
            rt.send_persona_menu("token", "chat", "")
        finally:
            rt.refresh_native_persona_cache = original_refresh
        self.assertIn("Persona catalog cannot be read", self.calls[-1][1]["text"])

    def test_native_export_failure_rolls_back_unchanged_local_edit(self):
        self._start("edit_description", "punto")
        original_configured = rt.phase3_api_configured
        original_export = rt.export_persona_to_native
        rt.phase3_api_configured = lambda: True
        rt.export_persona_to_native = lambda _persona_id: (_ for _ in ()).throw(RuntimeError("offline"))
        try:
            rt.handle_pending_input(self.db, "token", "chat", self.session, "Attempted update")
        finally:
            rt.phase3_api_configured = original_configured
            rt.export_persona_to_native = original_export
        data = json.loads(rt.PERSONA_FILE.read_text(encoding="utf-8"))
        self.assertEqual(data["punto"]["description"], "Original description")

    def test_native_export_failure_preserves_newer_concurrent_edit(self):
        self._start("edit_description", "punto")
        feedback = []
        original_configured = rt.phase3_api_configured
        original_export = rt.export_persona_to_native
        original_send_text = rt.send_text
        rt.phase3_api_configured = lambda: True
        rt.send_text = lambda _token, _chat, text: feedback.append(text) or [501]

        def concurrent_edit_then_fail(_persona_id):
            with rt.PERSONA_EDIT_LOCK:
                personas = rt._editable_personas()
                personas["punto"]["description"] = "Concurrent edit"
                rt._write_personas_atomically(personas)
            raise RuntimeError("offline")

        rt.export_persona_to_native = concurrent_edit_then_fail
        try:
            rt.handle_pending_input(self.db, "token", "chat", self.session, "Attempted update")
        finally:
            rt.phase3_api_configured = original_configured
            rt.export_persona_to_native = original_export
            rt.send_text = original_send_text
        data = json.loads(rt.PERSONA_FILE.read_text(encoding="utf-8"))
        self.assertEqual(data["punto"]["description"], "Concurrent edit")
        self.assertTrue(any("newer local persona edit was preserved" in text for text in feedback))

    def test_cancel_clears_pending_persona_input(self):
        self._start("create")
        handled = rt.handle_pending_input(self.db, "token", "chat", self.session, "/cancel")
        self.assertTrue(handled)
        self.assertEqual(rt.get_meta(self.db, "persona_input:chat"), "")
        self.assertEqual(json.loads(rt.PERSONA_FILE.read_text(encoding="utf-8"))["punto"]["description"], "Original description")

    def test_pending_persona_input_is_session_scoped(self):
        self._start("create")
        other = dict(self.session)
        other["session_id"] = "other-session"
        handled = rt.handle_pending_input(self.db, "token", "chat", other, "writer | Writer | Should not apply")
        self.assertFalse(handled)
        self.assertEqual(rt.get_meta(self.db, "persona_input:chat"), "")
        self.assertNotIn("writer", json.loads(rt.PERSONA_FILE.read_text(encoding="utf-8")))

    def test_missing_current_persona_cannot_start_edit(self):
        answers = []
        session = dict(self.session)
        session["persona_id"] = "missing"
        handled = rt.handle_persona_callback(self.db, "token", {"id": "cb"}, lambda _t, _i, text: answers.append(text), "persona:edit", "chat", {"message_id": 77}, session, "persona-session", None)
        self.assertTrue(handled)
        self.assertEqual(answers, ["Current persona not found"])
        self.assertEqual(rt.get_meta(self.db, "persona_input:chat"), "")


    def test_concurrent_catalog_edits_are_serialized(self):
        active = 0
        maximum_active = 0
        counter_lock = threading.Lock()
        start = threading.Barrier(2)
        original_editable = rt._editable_personas
        original_menu = rt.send_persona_menu

        def observed_catalog_read():
            nonlocal active, maximum_active
            with counter_lock:
                active += 1
                maximum_active = max(maximum_active, active)
            try:
                time.sleep(0.05)
                return original_editable()
            finally:
                with counter_lock:
                    active -= 1

        rt._editable_personas = observed_catalog_read
        rt.send_persona_menu = lambda *_args, **_kwargs: None
        errors = []

        def create(chat_id, persona_id):
            db = rt.db_connect()
            try:
                session = rt.create_session(db, chat_id, "provider/model", session_id=f"session-{persona_id}")
                state = {"mode": "create", "persona_id": "", "session_id": session["session_id"], "expires_at": rt.time.time() + 600}
                start.wait(timeout=2)
                rt._handle_persona_input(db, "token", chat_id, session, f"{persona_id} | {persona_id.title()} | Description {persona_id}", state, None)
            except Exception as exc:
                errors.append(exc)
            finally:
                db.close()

        threads = [threading.Thread(target=create, args=("chat-a", "alpha")), threading.Thread(target=create, args=("chat-b", "beta"))]
        try:
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=5)
        finally:
            rt._editable_personas = original_editable
            rt.send_persona_menu = original_menu
        self.assertEqual(errors, [])
        self.assertFalse(any(thread.is_alive() for thread in threads))
        self.assertEqual(maximum_active, 1)
        personas = json.loads(rt.PERSONA_FILE.read_text(encoding="utf-8"))
        self.assertTrue({"alpha", "beta"}.issubset(personas))


if __name__ == "__main__":
    unittest.main()
