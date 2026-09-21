from pathlib import Path
import tempfile
import unittest

import bridge.config as config
from runtime_test_facade import runtime as rt


class DirectorGoalsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_db = config.DB_FILE
        config.DB_FILE = Path(self.tmp.name) / "bridge.sqlite3"
        self.db = rt.db_connect()
        self.chat_id = "chat|topic:1"
        self.session = rt.create_session(
            self.db,
            self.chat_id,
            "primary::main",
            session_id="director-goal",
            title="Director goal",
        )
        rt.set_task_model(self.db, self.chat_id, self.session["session_id"], "utility::director")
        rt.save_group_state(
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

    def tearDown(self):
        self.db.close()
        config.DB_FILE = self.old_db
        self.tmp.cleanup()

    def test_goal_is_session_local_and_bounded(self):
        value = rt.set_director_goal(
            self.db,
            self.chat_id,
            self.session["session_id"],
            "  Reveal   the hidden door slowly.  ",
        )
        self.assertEqual(value, "Reveal the hidden door slowly.")
        self.assertEqual(
            rt.get_director_goal(self.db, self.chat_id, self.session["session_id"]),
            "Reveal the hidden door slowly.",
        )
        other = rt.create_session(
            self.db,
            self.chat_id,
            "primary::main",
            session_id="other",
            title="Other",
        )
        self.assertEqual(rt.get_director_goal(self.db, self.chat_id, other["session_id"]), "")

    def test_director_uses_utility_model_and_receives_hidden_goal(self):
        rt.set_director_goal(
            self.db,
            self.chat_id,
            self.session["session_id"],
            "Let Bob discover the hidden door without resolving what is behind it.",
        )
        old_safe = rt.safe_character_path
        old_fields = rt.card_fields_from_file
        old_generate = rt.generate_text
        rt.safe_character_path = lambda filename: Path(filename)
        rt.card_fields_from_file = lambda filename: {"name": Path(filename).stem.title()}
        calls = []

        def fake_generate(_key, model, messages, **kwargs):
            calls.append((model, messages, kwargs))
            return '{"speaker":"Bob","direction":"Bob notices a seam in the wall."}'

        rt.generate_text = fake_generate
        try:
            before = self.db.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
            plan = rt.group_director_plan(
                self.db,
                "",
                self.chat_id,
                self.session,
                "I look around.",
            )
            after = self.db.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
        finally:
            rt.safe_character_path = old_safe
            rt.card_fields_from_file = old_fields
            rt.generate_text = old_generate

        self.assertEqual(plan[0], "bob.png")
        self.assertEqual(calls[0][0], "utility::director")
        joined = "\n".join(str(message["content"]) for message in calls[0][1])
        self.assertIn("hidden door", joined)
        self.assertIn("believable character behavior", joined)
        self.assertTrue(calls[0][2]["force_non_stream"])
        self.assertEqual(before, after)

    def test_director_policy_prefers_director_task_model(self):
        rt.set_task_model(
            self.db,
            self.chat_id,
            self.session["session_id"],
            "utility::fallback",
            task="utility",
        )
        rt.set_task_model(
            self.db,
            self.chat_id,
            self.session["session_id"],
            "director::special",
            task="director",
        )

        old_safe = rt.safe_character_path
        old_fields = rt.card_fields_from_file
        old_generate = rt.generate_text
        calls = []
        rt.safe_character_path = lambda filename: Path(filename)
        rt.card_fields_from_file = lambda filename: {"name": Path(filename).stem.title()}

        def fake_generate(_key, model, messages, **kwargs):
            calls.append((model, messages, kwargs))
            return '{"speaker":"Alice","direction":"Continue."}'

        rt.generate_text = fake_generate
        try:
            rt.group_director_plan(
                self.db,
                "",
                self.chat_id,
                self.session,
                "Continue.",
            )
        finally:
            rt.safe_character_path = old_safe
            rt.card_fields_from_file = old_fields
            rt.generate_text = old_generate

        self.assertEqual(calls[0][0], "director::special")

    def test_director_policy_falls_back_to_utility_model(self):
        rt.set_task_model(
            self.db,
            self.chat_id,
            self.session["session_id"],
            "utility::fallback",
            task="utility",
        )
        rt.set_task_model(
            self.db,
            self.chat_id,
            self.session["session_id"],
            "",
            task="director",
        )

        old_safe = rt.safe_character_path
        old_fields = rt.card_fields_from_file
        old_generate = rt.generate_text
        calls = []
        rt.safe_character_path = lambda filename: Path(filename)
        rt.card_fields_from_file = lambda filename: {"name": Path(filename).stem.title()}

        def fake_generate(_key, model, messages, **kwargs):
            calls.append((model, messages, kwargs))
            return '{"speaker":"Alice","direction":"Continue."}'

        rt.generate_text = fake_generate
        try:
            rt.group_director_plan(
                self.db,
                "",
                self.chat_id,
                self.session,
                "Continue.",
            )
        finally:
            rt.safe_character_path = old_safe
            rt.card_fields_from_file = old_fields
            rt.generate_text = old_generate

        self.assertEqual(calls[0][0], "utility::fallback")

    def test_director_policy_falls_back_to_main_model(self):
        rt.set_task_model(
            self.db,
            self.chat_id,
            self.session["session_id"],
            "",
            task="director",
        )
        rt.set_task_model(
            self.db,
            self.chat_id,
            self.session["session_id"],
            "",
            task="utility",
        )

        old_safe = rt.safe_character_path
        old_fields = rt.card_fields_from_file
        old_generate = rt.generate_text
        calls = []
        rt.safe_character_path = lambda filename: Path(filename)
        rt.card_fields_from_file = lambda filename: {"name": Path(filename).stem.title()}

        def fake_generate(_key, model, messages, **kwargs):
            calls.append((model, messages, kwargs))
            return '{"speaker":"Alice","direction":"Continue."}'

        rt.generate_text = fake_generate
        try:
            rt.group_director_plan(
                self.db,
                "",
                self.chat_id,
                self.session,
                "Continue.",
            )
        finally:
            rt.safe_character_path = old_safe
            rt.card_fields_from_file = old_fields
            rt.generate_text = old_generate

        self.assertEqual(calls[0][0], "primary::main")

    def test_director_policy_applies_model_and_token_budget_without_goal(self):
        old_safe = rt.safe_character_path
        old_fields = rt.card_fields_from_file
        old_generate = rt.generate_text
        calls = []
        rt.safe_character_path = lambda filename: Path(filename)
        rt.card_fields_from_file = lambda filename: {"name": Path(filename).stem.title()}

        def fake_generate(_key, model, messages, **kwargs):
            calls.append((model, messages, kwargs))
            return '{"speaker":"Alice","direction":"Continue."}'

        rt.generate_text = fake_generate
        try:
            rt.group_director_plan(
                self.db,
                "",
                self.chat_id,
                self.session,
                "Continue.",
            )
        finally:
            rt.safe_character_path = old_safe
            rt.card_fields_from_file = old_fields
            rt.generate_text = old_generate

        joined = "\n".join(str(message["content"]) for message in calls[0][1])
        self.assertEqual(calls[0][0], "utility::director")
        self.assertEqual(calls[0][2]["settings"]["max_tokens"], 220)
        self.assertNotIn("Hidden scene objective:", joined)

    def test_generation_context_keeps_goal_hidden_but_actionable(self):
        rt.set_director_goal(
            self.db,
            self.chat_id,
            self.session["session_id"],
            "Increase tension around the unopened letter.",
        )
        old_safe = rt.safe_character_path
        old_fields = rt.card_fields_from_file
        rt.safe_character_path = lambda filename: Path(filename)
        rt.card_fields_from_file = lambda filename: {"name": Path(filename).stem.title()}
        try:
            context = rt.group_prompt_context(
                self.db,
                self.chat_id,
                self.session,
                "alice.png",
                "Keep the pace measured.",
            )
        finally:
            rt.safe_character_path = old_safe
            rt.card_fields_from_file = old_fields

        self.assertIn("unopened letter", context)
        self.assertIn("Never mention", context)

    def test_set_director_goal_joins_outer_transaction(self):
        self.db.execute("BEGIN")
        rt.set_director_goal(
            self.db,
            self.chat_id,
            self.session["session_id"],
            "Hold tension.",
        )
        self.assertTrue(self.db.in_transaction)
        self.db.rollback()
        self.assertEqual(
            rt.get_director_goal(
                self.db,
                self.chat_id,
                self.session["session_id"],
            ),
            "",
        )

    def test_clearing_goal_removes_it(self):
        rt.set_director_goal(
            self.db,
            self.chat_id,
            self.session["session_id"],
            "Resolve the argument.",
        )
        rt.set_director_goal(self.db, self.chat_id, self.session["session_id"], "")
        self.assertEqual(
            rt.get_director_goal(self.db, self.chat_id, self.session["session_id"]),
            "",
        )


if __name__ == "__main__":
    unittest.main()
