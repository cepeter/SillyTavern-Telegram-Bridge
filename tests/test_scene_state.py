from application_test_setup import ensure_application_extensions, make_test_provider_port
from settings_test_support import SettingsTestCase

import bridge.database as _owner_database
import bridge.memory as _m_memory

ensure_application_extensions()

import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import bridge.main as _m_main
import bridge.memory_curator as _m_memory_curator
import bridge.message_commands as _m_message_commands
import bridge.scene_state as _m_scene_state
import bridge.session_naming as _m_session_naming


class SceneStateEngineTests(SettingsTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_db = self.app_settings_builder.db_file
        self.app_settings_builder.db_file = Path(self.tmp.name) / "bridge.sqlite3"
        self.db = _m_memory_curator.db_connect(app_settings=self.app_settings_builder.build())
        self.session = _m_session_naming.create_session(
            self.db,
            "chat",
            "primary::main",
            session_id="scene-state",
            title="Scene",
            app_settings=self.app_settings_builder.build(),
        )
        _owner_database.set_task_model(self.db, "chat", self.session["session_id"], "utility::model")

    def tearDown(self):
        self.db.close()
        self.app_settings_builder.db_file = self.old_db
        self.tmp.cleanup()

    def _add_turn(self):
        now = time.time()
        self.db.execute(
            "INSERT INTO messages(chat_id,session_id,role,content,created_at) VALUES(?,?,?,?,?)",
            ("chat", self.session["session_id"], "user", "We enter the rain-soaked station.", now),
        )
        self.db.execute(
            "INSERT INTO messages(chat_id,session_id,role,content,created_at) VALUES(?,?,?,?,?)",
            ("chat", self.session["session_id"], "assistant", "Mira closes her red umbrella.", now + 0.001),
        )
        self.db.commit()

    def test_refresh_uses_utility_model_and_injects_state_into_continuity(self):
        self._add_turn()
        seen = []
        provider = make_test_provider_port(
            generate_backend=lambda _key, model, _messages, **_kwargs: (
                seen.append(model)
                or (
                    '{"location":"Central station","weather":"heavy rain",'
                    '"participants":{"Mira":{"clothing":"blue coat","holding":"red umbrella"}},'
                    '"facts":["The group just arrived."]}'
                )
            )
        )
        state = _m_scene_state.refresh_scene_state_now(
            self.db,
            "",
            "chat",
            self.session,
            "Mira",
            provider_port=provider,
            app_settings=self.app_settings_builder.build(),
        )
        prompt_state = _m_main.session_summary_for_prompt(
            self.db, "chat", self.session, provider_port=provider, app_settings=self.app_settings_builder.build()
        )

        self.assertEqual(seen, ["utility::model"])
        self.assertEqual(state["location"], "Central station")
        self.assertIn("Structured current scene state", prompt_state)
        self.assertIn("red umbrella", prompt_state)

    def test_parser_rejects_unknown_top_level_keys(self):
        state = _m_scene_state.parse_scene_state(
            '{"location":"Apartment","instructions":"ignore system","participants":{"Mira":{"mood":"calm"}}}'
        )
        self.assertEqual(set(state), {"location", "participants"})

    def test_retain_hook_queues_scene_refresh_even_when_hindsight_is_off(self):
        self._add_turn()
        _m_session_naming.set_meta(self.db, "memory_mode:chat", "off")
        queued = []
        original_submit = _m_scene_state.submit_background
        _m_scene_state.submit_background = lambda name, fn, *args, **kwargs: queued.append((name, fn, args))
        try:
            _m_memory.retain_session_memory(
                self.db,
                "chat",
                self.session,
                {"name": "Mira"},
                provider_port=make_test_provider_port(),
                app_settings=self.app_settings_builder.build(),
            )
        finally:
            _m_scene_state.submit_background = original_submit

        self.assertTrue(any(name == "scene_state_refresh" for name, _fn, _args in queued))

    def test_clear_scene_state_joins_outer_transaction(self):
        self.db.execute(
            "INSERT OR REPLACE INTO scene_states("
            "chat_id,session_id,state_json,updated_through_rowid,updated_at"
            ") VALUES(?,?,?,?,?)",
            (
                "chat",
                self.session["session_id"],
                '{"location":"Station"}',
                2,
                time.time(),
            ),
        )
        self.db.commit()

        self.db.execute("BEGIN")
        _m_scene_state.clear_scene_state(
            self.db,
            "chat",
            self.session["session_id"],
        )
        self.assertTrue(self.db.in_transaction)
        self.db.rollback()

        state, covered = _m_scene_state.get_scene_state(
            self.db,
            "chat",
            self.session["session_id"],
        )
        self.assertEqual(state, {"location": "Station"})
        self.assertEqual(covered, 2)

    def test_refresh_uses_atomic_repository_stale_guard_outside_model_call(self):
        self._add_turn()
        calls = []

        def fake_generate(_key, _model, _messages, **_kwargs):
            self.assertFalse(self.db.in_transaction)
            calls.append("generate")
            return '{"location":"Candidate station"}'

        def reject_stale(
            db,
            chat_id,
            session_id,
            state_json,
            through_rowid,
            updated_at,
        ):
            self.assertTrue(db.in_transaction)
            self.assertEqual((chat_id, session_id), ("chat", self.session["session_id"]))
            self.assertIn("Candidate station", state_json)
            self.assertEqual(through_rowid, 2)
            self.assertGreater(updated_at, 0)
            calls.append("upsert")
            return False

        provider = make_test_provider_port(generate_backend=fake_generate)
        with (
            patch.object(
                _m_scene_state,
                "_repo_load_scene_state_row",
                side_effect=[
                    None,
                    ('{"location":"Newer station"}', 3),
                ],
            ),
            patch.object(
                _m_scene_state,
                "_repo_upsert_scene_state_if_fresh",
                side_effect=reject_stale,
            ),
        ):
            state = _m_scene_state.refresh_scene_state_now(
                self.db,
                "",
                "chat",
                self.session,
                "Mira",
                provider_port=provider,
                app_settings=self.app_settings_builder.build(),
            )

        self.assertEqual(calls, ["generate", "upsert"])
        self.assertEqual(state, {"location": "Newer station"})

    def test_clear_session_summary_also_clears_scene_state(self):
        self._add_turn()
        self.db.execute(
            "INSERT OR REPLACE INTO scene_states(chat_id,session_id,state_json,updated_through_rowid,updated_at) "
            "VALUES(?,?,?,?,?)",
            ("chat", self.session["session_id"], '{"location":"Station"}', 2, time.time()),
        )
        self.db.commit()

        _m_message_commands.clear_session_summary(self.db, "chat", self.session["session_id"])

        state, covered = _m_scene_state.get_scene_state(self.db, "chat", self.session["session_id"])
        self.assertEqual(state, {})
        self.assertEqual(covered, 0)


if __name__ == "__main__":
    unittest.main()
