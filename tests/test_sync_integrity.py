import unittest
from pathlib import Path

from settings_test_support import SettingsTestCase

from bridge.sync_integrity import SyncSnapshotIntegrityAdapter


class SyncSnapshotIntegrityAdapterTests(SettingsTestCase):
    def setUp(self):
        self.events = []
        self.updates = []
        self.retained = []
        self.warnings = []
        self.final_session = {
            "session_id": "session",
            "character_file": "final.png",
        }
        self.db = object()
        self.session = {
            "session_id": "session",
            "character_file": "before.png",
        }

        def apply_backend(
            db,
            chat_id,
            session,
            metadata,
            messages,
            variants,
        ):
            self.events.append(
                (
                    "backend",
                    db,
                    chat_id,
                    session,
                    metadata,
                    messages,
                    variants,
                )
            )
            return "raw-hash"

        def update_session(
            db,
            chat_id,
            session_id,
            **updates,
        ):
            self.events.append(("update", db, chat_id, session_id, updates))
            self.updates.append(updates)

        def load_session(
            db,
            chat_id,
            session_id,
            default_model,
        ):
            self.events.append(
                (
                    "load",
                    db,
                    chat_id,
                    session_id,
                    default_model,
                )
            )
            return dict(self.final_session)

        def card_fields(character_file):
            self.events.append(("card", character_file))
            return {"name": f"card:{character_file}"}

        def retain_memory(db, chat_id, session, fields):
            self.events.append(("retain", db, chat_id, session, fields))
            self.retained.append((db, chat_id, session, fields))

        def log_warning(message, *, exc_info):
            self.warnings.append((message, exc_info))

        self.adapter = SyncSnapshotIntegrityAdapter(
            apply_backend=apply_backend,
            update_session=update_session,
            load_session=load_session,
            retain_memory=retain_memory,
            card_fields=card_fields,
            default_model="provider/model",
            log_warning=log_warning,
        )

    def test_explicit_empty_persona_clears_persona_id(self):
        result = self.adapter.apply(
            self.db,
            "chat",
            self.session,
            {"persona": ""},
            [("user", "remote")],
            {},
        )

        self.assertEqual(result, "raw-hash")
        self.assertEqual(
            self.updates,
            [{"persona_id": ""}],
        )

    def test_whitespace_persona_clears_persona_id(self):
        self.adapter.apply(
            self.db,
            "chat",
            self.session,
            {"persona": "   "},
            [("user", "remote")],
            {},
        )

        self.assertEqual(
            self.updates,
            [{"persona_id": ""}],
        )

    def test_absent_persona_and_world_info_preserve_assignments(self):
        self.adapter.apply(
            self.db,
            "chat",
            self.session,
            {},
            [("user", "remote")],
            {},
        )

        self.assertEqual(self.updates, [])

    def test_empty_world_list_clears_world_file(self):
        self.adapter.apply(
            self.db,
            "chat",
            self.session,
            {"world_info": []},
            [("user", "remote")],
            {},
        )

        self.assertEqual(
            self.updates,
            [{"world_file": ""}],
        )

    def test_world_none_clears_world_file(self):
        self.adapter.apply(
            self.db,
            "chat",
            self.session,
            {"world_info": None},
            [("user", "remote")],
            {},
        )

        self.assertEqual(
            self.updates,
            [{"world_file": ""}],
        )

    def test_world_empty_string_clears_world_file(self):
        self.adapter.apply(
            self.db,
            "chat",
            self.session,
            {"world_info": ""},
            [("user", "remote")],
            {},
        )

        self.assertEqual(
            self.updates,
            [{"world_file": ""}],
        )

    def test_combined_explicit_clears_use_one_update(self):
        self.adapter.apply(
            self.db,
            "chat",
            self.session,
            {
                "persona": "",
                "world_info": [],
            },
            [("user", "remote")],
            {},
        )

        self.assertEqual(
            self.updates,
            [
                {
                    "persona_id": "",
                    "world_file": "",
                }
            ],
        )

    def test_non_empty_metadata_does_not_duplicate_backend_updates(self):
        self.adapter.apply(
            self.db,
            "chat",
            self.session,
            {
                "persona": "person.png",
                "world_info": ["world.json"],
            },
            [("user", "remote")],
            {},
        )

        self.assertEqual(self.updates, [])

    def test_backend_receives_original_arguments_and_hash_is_unchanged(self):
        metadata = {"name": "Remote"}
        messages = [("user", "one"), ("assistant", "two")]
        variants = {1: (["a", "b"], 1)}

        result = self.adapter.apply(
            self.db,
            "chat",
            self.session,
            metadata,
            messages,
            variants,
        )

        self.assertEqual(result, "raw-hash")
        backend = self.events[0]
        self.assertEqual(backend[0], "backend")
        self.assertIs(backend[1], self.db)
        self.assertEqual(backend[2], "chat")
        self.assertIs(backend[3], self.session)
        self.assertIs(backend[4], metadata)
        self.assertIs(backend[5], messages)
        self.assertIs(backend[6], variants)

    def test_refresh_uses_reloaded_final_session_and_character_file(self):
        self.adapter.apply(
            self.db,
            "chat",
            self.session,
            {},
            [("user", "remote")],
            {},
        )

        self.assertEqual(
            self.retained,
            [
                (
                    self.db,
                    "chat",
                    self.final_session,
                    {"name": "card:final.png"},
                )
            ],
        )
        self.assertIn(("card", "final.png"), self.events)
        self.assertNotIn(("card", "before.png"), self.events)

    def test_backend_error_propagates_and_stops_followup_work(self):
        expected = RuntimeError("backend failed")

        def fail_backend(*_args, **_kwargs):
            raise expected

        self.adapter = SyncSnapshotIntegrityAdapter(
            apply_backend=fail_backend,
            update_session=self.adapter.update_session,
            load_session=self.adapter.load_session,
            retain_memory=self.adapter.retain_memory,
            card_fields=self.adapter.card_fields,
            default_model=self.adapter.default_model,
            log_warning=self.adapter.log_warning,
        )

        with self.assertRaises(RuntimeError) as caught:
            self.adapter.apply(
                self.db,
                "chat",
                self.session,
                {"persona": ""},
                [("user", "remote")],
                {},
            )

        self.assertIs(caught.exception, expected)
        self.assertEqual(self.updates, [])
        self.assertEqual(self.retained, [])

    def test_explicit_clear_error_propagates_and_skips_refresh(self):
        expected = RuntimeError("clear failed")

        def fail_update(*_args, **_kwargs):
            raise expected

        self.adapter = SyncSnapshotIntegrityAdapter(
            apply_backend=self.adapter.apply_backend,
            update_session=fail_update,
            load_session=self.adapter.load_session,
            retain_memory=self.adapter.retain_memory,
            card_fields=self.adapter.card_fields,
            default_model=self.adapter.default_model,
            log_warning=self.adapter.log_warning,
        )

        with self.assertRaises(RuntimeError) as caught:
            self.adapter.apply(
                self.db,
                "chat",
                self.session,
                {"persona": ""},
                [("user", "remote")],
                {},
            )

        self.assertIs(caught.exception, expected)
        self.assertEqual(self.retained, [])

    def test_refresh_failure_is_logged_and_hash_still_returns(self):
        def fail_load(*_args, **_kwargs):
            raise RuntimeError("reload failed")

        self.adapter = SyncSnapshotIntegrityAdapter(
            apply_backend=self.adapter.apply_backend,
            update_session=self.adapter.update_session,
            load_session=fail_load,
            retain_memory=self.adapter.retain_memory,
            card_fields=self.adapter.card_fields,
            default_model=self.adapter.default_model,
            log_warning=self.adapter.log_warning,
        )

        result = self.adapter.apply(
            self.db,
            "chat",
            self.session,
            {},
            [("user", "remote")],
            {},
        )

        self.assertEqual(result, "raw-hash")
        self.assertEqual(
            self.warnings,
            [
                (
                    "Could not refresh Hindsight after Live Sync import",
                    True,
                )
            ],
        )

    def test_card_fields_failure_is_logged_and_isolated(self):
        def fail_card(_character_file):
            raise RuntimeError("card failed")

        self.adapter = SyncSnapshotIntegrityAdapter(
            apply_backend=self.adapter.apply_backend,
            update_session=self.adapter.update_session,
            load_session=self.adapter.load_session,
            retain_memory=self.adapter.retain_memory,
            card_fields=fail_card,
            default_model=self.adapter.default_model,
            log_warning=self.adapter.log_warning,
        )

        result = self.adapter.apply(
            self.db,
            "chat",
            self.session,
            {},
            [("user", "remote")],
            {},
        )

        self.assertEqual(result, "raw-hash")
        self.assertEqual(len(self.warnings), 1)
        self.assertEqual(self.retained, [])

    def test_retain_failure_is_logged_and_isolated(self):
        def fail_retain(*_args, **_kwargs):
            raise RuntimeError("retain failed")

        self.adapter = SyncSnapshotIntegrityAdapter(
            apply_backend=self.adapter.apply_backend,
            update_session=self.adapter.update_session,
            load_session=self.adapter.load_session,
            retain_memory=fail_retain,
            card_fields=self.adapter.card_fields,
            default_model=self.adapter.default_model,
            log_warning=self.adapter.log_warning,
        )

        result = self.adapter.apply(
            self.db,
            "chat",
            self.session,
            {},
            [("user", "remote")],
            {},
        )

        self.assertEqual(result, "raw-hash")
        self.assertEqual(len(self.warnings), 1)


class SyncIntegritySourceBoundaryTests(SettingsTestCase):
    def test_sync_integrity_has_no_runtime_sync_or_ui_imports(self):
        source = (Path(__file__).parents[1] / "bridge" / "sync_integrity.py").read_text(encoding="utf-8")

        for forbidden in (
            "bridge.runtime",
            "bridge.sync_core",
            "bridge.sync_api",
            "bridge.sync_safety",
            "bridge.state_integrity",
            "bridge.telegram",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()
