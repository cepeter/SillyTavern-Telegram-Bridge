from functools import partial as _partial

from application_test_setup import ensure_application_extensions, make_test_provider_port
from settings_test_support import SettingsTestCase

import bridge.model_selection as _owner_model_selection

ensure_application_extensions()

import tempfile
import unittest
from pathlib import Path

import bridge.director_goals as _m_director_goals
import bridge.group_core as _m_group_core
import bridge.groups as _m_groups
import bridge.memory_curator as _m_memory_curator
import bridge.session_naming as _m_session_naming
from bridge.group_director_service import GroupDirectorService


class DirectorGoalsTests(SettingsTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_db = self.app_settings_builder.db_file
        self.app_settings_builder.db_file = Path(self.tmp.name) / "bridge.sqlite3"
        self.db = _m_memory_curator.db_connect(app_settings=self.app_settings_builder.build())
        self._generate_text = make_test_provider_port().generate
        self.chat_id = "chat|topic:1"
        self.session = _m_session_naming.create_session(
            self.db,
            self.chat_id,
            "primary::main",
            session_id="director-goal",
            title="Director goal",
            app_settings=self.app_settings_builder.build(),
        )
        _owner_model_selection.set_task_model(self.db, self.chat_id, self.session["session_id"], "utility::director")
        _m_group_core.save_group_state(
            self.db,
            self.chat_id,
            self.session["session_id"],
            {
                "title": "Director",
                "enabled": True,
                "turn_index": 0,
                "mode": "director",
                "forced_speaker": "",
                "members": ["alice.png", "bob.png"],
                "turn_user_id": "",
                "turn_users": [],
            },
        )

    def _service(self):
        return GroupDirectorService(
            load_group_state=_m_group_core.group_state,
            safe_character=_partial(_m_groups.safe_character_path, app_settings=self.app_settings_builder.build()),
            member_labels=_partial(_m_group_core.group_member_labels, app_settings=self.app_settings_builder.build()),
            card_fields=_partial(_m_groups.card_fields_from_file, app_settings=self.app_settings_builder.build()),
            generation_settings=_m_groups.get_generation_settings,
            generate_text=self._generate_text,
            director_policy=_partial(
                _m_director_goals.director_goal_policy, app_settings=self.app_settings_builder.build()
            ),
            default_model=self.app_settings_builder.default_model,
        )

    def tearDown(self):
        self.db.close()
        self.app_settings_builder.db_file = self.old_db
        self.tmp.cleanup()

    def test_goal_is_session_local_and_bounded(self):
        value = _m_director_goals.set_director_goal(
            self.db,
            self.chat_id,
            self.session["session_id"],
            "  Reveal   the hidden door slowly.  ",
        )
        self.assertEqual(value, "Reveal the hidden door slowly.")
        self.assertEqual(
            _m_director_goals.get_director_goal(self.db, self.chat_id, self.session["session_id"]),
            "Reveal the hidden door slowly.",
        )
        other = _m_session_naming.create_session(
            self.db,
            self.chat_id,
            "primary::main",
            session_id="other",
            title="Other",
            app_settings=self.app_settings_builder.build(),
        )
        self.assertEqual(_m_director_goals.get_director_goal(self.db, self.chat_id, other["session_id"]), "")

    def test_director_uses_utility_model_and_receives_hidden_goal(self):
        _m_director_goals.set_director_goal(
            self.db,
            self.chat_id,
            self.session["session_id"],
            "Let Bob discover the hidden door without resolving what is behind it.",
        )
        old_safe = _m_groups.safe_character_path
        old_fields = _m_groups.card_fields_from_file
        old_generate = self._generate_text
        _m_groups.safe_character_path = lambda filename, *, app_settings=None: Path(filename)
        _m_groups.card_fields_from_file = lambda filename, *, app_settings=None: {"name": Path(filename).stem.title()}
        calls = []

        def fake_generate(_key, model, messages, **kwargs):
            calls.append((model, messages, kwargs))
            return '{"speaker":"Bob","direction":"Bob notices a seam in the wall."}'

        self._generate_text = fake_generate
        try:
            before = self.db.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
            plan = self._service().plan(
                self.db,
                "",
                self.chat_id,
                self.session,
                "I look around.",
            )
            after = self.db.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
        finally:
            _m_groups.safe_character_path = old_safe
            _m_groups.card_fields_from_file = old_fields
            self._generate_text = old_generate

        self.assertEqual(plan[0], "bob.png")
        self.assertEqual(calls[0][0], "utility::director")
        joined = "\n".join(str(message["content"]) for message in calls[0][1])
        self.assertIn("hidden door", joined)
        self.assertIn("believable character behavior", joined)
        self.assertTrue(calls[0][2]["force_non_stream"])
        self.assertEqual(before, after)

    def test_director_policy_prefers_director_task_model(self):
        _owner_model_selection.set_task_model(
            self.db,
            self.chat_id,
            self.session["session_id"],
            "utility::fallback",
            task="utility",
        )
        _owner_model_selection.set_task_model(
            self.db,
            self.chat_id,
            self.session["session_id"],
            "director::special",
            task="director",
        )

        old_safe = _m_groups.safe_character_path
        old_fields = _m_groups.card_fields_from_file
        old_generate = self._generate_text
        calls = []
        _m_groups.safe_character_path = lambda filename, *, app_settings=None: Path(filename)
        _m_groups.card_fields_from_file = lambda filename, *, app_settings=None: {"name": Path(filename).stem.title()}

        def fake_generate(_key, model, messages, **kwargs):
            calls.append((model, messages, kwargs))
            return '{"speaker":"Alice","direction":"Continue."}'

        self._generate_text = fake_generate
        try:
            self._service().plan(
                self.db,
                "",
                self.chat_id,
                self.session,
                "Continue.",
            )
        finally:
            _m_groups.safe_character_path = old_safe
            _m_groups.card_fields_from_file = old_fields
            self._generate_text = old_generate

        self.assertEqual(calls[0][0], "director::special")

    def test_director_policy_falls_back_to_utility_model(self):
        _owner_model_selection.set_task_model(
            self.db,
            self.chat_id,
            self.session["session_id"],
            "utility::fallback",
            task="utility",
        )
        _owner_model_selection.set_task_model(
            self.db,
            self.chat_id,
            self.session["session_id"],
            "",
            task="director",
        )

        old_safe = _m_groups.safe_character_path
        old_fields = _m_groups.card_fields_from_file
        old_generate = self._generate_text
        calls = []
        _m_groups.safe_character_path = lambda filename, *, app_settings=None: Path(filename)
        _m_groups.card_fields_from_file = lambda filename, *, app_settings=None: {"name": Path(filename).stem.title()}

        def fake_generate(_key, model, messages, **kwargs):
            calls.append((model, messages, kwargs))
            return '{"speaker":"Alice","direction":"Continue."}'

        self._generate_text = fake_generate
        try:
            self._service().plan(
                self.db,
                "",
                self.chat_id,
                self.session,
                "Continue.",
            )
        finally:
            _m_groups.safe_character_path = old_safe
            _m_groups.card_fields_from_file = old_fields
            self._generate_text = old_generate

        self.assertEqual(calls[0][0], "utility::fallback")

    def test_director_policy_falls_back_to_main_model(self):
        _owner_model_selection.set_task_model(
            self.db,
            self.chat_id,
            self.session["session_id"],
            "",
            task="director",
        )
        _owner_model_selection.set_task_model(
            self.db,
            self.chat_id,
            self.session["session_id"],
            "",
            task="utility",
        )

        old_safe = _m_groups.safe_character_path
        old_fields = _m_groups.card_fields_from_file
        old_generate = self._generate_text
        calls = []
        _m_groups.safe_character_path = lambda filename, *, app_settings=None: Path(filename)
        _m_groups.card_fields_from_file = lambda filename, *, app_settings=None: {"name": Path(filename).stem.title()}

        def fake_generate(_key, model, messages, **kwargs):
            calls.append((model, messages, kwargs))
            return '{"speaker":"Alice","direction":"Continue."}'

        self._generate_text = fake_generate
        try:
            self._service().plan(
                self.db,
                "",
                self.chat_id,
                self.session,
                "Continue.",
            )
        finally:
            _m_groups.safe_character_path = old_safe
            _m_groups.card_fields_from_file = old_fields
            self._generate_text = old_generate

        self.assertEqual(calls[0][0], "primary::main")

    def test_director_policy_applies_model_and_token_budget_without_goal(self):
        old_safe = _m_groups.safe_character_path
        old_fields = _m_groups.card_fields_from_file
        old_generate = self._generate_text
        calls = []
        _m_groups.safe_character_path = lambda filename, *, app_settings=None: Path(filename)
        _m_groups.card_fields_from_file = lambda filename, *, app_settings=None: {"name": Path(filename).stem.title()}

        def fake_generate(_key, model, messages, **kwargs):
            calls.append((model, messages, kwargs))
            return '{"speaker":"Alice","direction":"Continue."}'

        self._generate_text = fake_generate
        try:
            self._service().plan(
                self.db,
                "",
                self.chat_id,
                self.session,
                "Continue.",
            )
        finally:
            _m_groups.safe_character_path = old_safe
            _m_groups.card_fields_from_file = old_fields
            self._generate_text = old_generate

        joined = "\n".join(str(message["content"]) for message in calls[0][1])
        self.assertEqual(calls[0][0], "utility::director")
        self.assertEqual(calls[0][2]["settings"]["max_tokens"], 220)
        self.assertNotIn("Hidden scene objective:", joined)

    def test_generation_context_keeps_goal_hidden_but_actionable(self):
        _m_director_goals.set_director_goal(
            self.db,
            self.chat_id,
            self.session["session_id"],
            "Increase tension around the unopened letter.",
        )
        old_safe = _m_groups.safe_character_path
        old_fields = _m_groups.card_fields_from_file
        _m_groups.safe_character_path = lambda filename, *, app_settings=None: Path(filename)
        _m_groups.card_fields_from_file = lambda filename, *, app_settings=None: {"name": Path(filename).stem.title()}
        try:
            context = self._service().prompt_context(
                self.db,
                self.chat_id,
                self.session,
                "alice.png",
                "Keep the pace measured.",
            )
        finally:
            _m_groups.safe_character_path = old_safe
            _m_groups.card_fields_from_file = old_fields

        self.assertIn("unopened letter", context)
        self.assertIn("Never mention", context)

    def test_set_director_goal_joins_outer_transaction(self):
        self.db.execute("BEGIN")
        _m_director_goals.set_director_goal(
            self.db,
            self.chat_id,
            self.session["session_id"],
            "Hold tension.",
        )
        self.assertTrue(self.db.in_transaction)
        self.db.rollback()
        self.assertEqual(
            _m_director_goals.get_director_goal(
                self.db,
                self.chat_id,
                self.session["session_id"],
            ),
            "",
        )

    def test_clearing_goal_removes_it(self):
        _m_director_goals.set_director_goal(
            self.db,
            self.chat_id,
            self.session["session_id"],
            "Resolve the argument.",
        )
        _m_director_goals.set_director_goal(self.db, self.chat_id, self.session["session_id"], "")
        self.assertEqual(
            _m_director_goals.get_director_goal(self.db, self.chat_id, self.session["session_id"]),
            "",
        )


if __name__ == "__main__":
    unittest.main()
