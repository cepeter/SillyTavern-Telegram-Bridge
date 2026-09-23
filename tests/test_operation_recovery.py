from application_test_setup import ensure_application_extensions, make_test_application_services, make_test_memory_service, make_test_persona_service

ensure_application_extensions()

from pathlib import Path
from types import SimpleNamespace
import json
import tempfile
import unittest
from unittest.mock import Mock, patch

import time
import bridge.callbacks as _m_callbacks
import bridge.commands as _m_commands
import bridge.memory_curator as _m_memory_curator
import bridge.message_commands as _m_message_commands
import bridge.panel_callback_routes as _m_panel_callback_routes
import bridge.session_naming as _m_session_naming
import bridge.sync_api as _m_sync_api
import bridge.sync_core as _m_sync_core
import bridge.card_content as _m_card_content
import bridge.generation as _m_generation
import bridge.media as _m_media
import bridge.telegram as _m_telegram
from bridge.operation_recovery import OperationRecovery


class DurableRecoveryCharacterizationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "recovery.sqlite3"
        self.db = _m_memory_curator.db_connect(self.path)
        self.session = _m_telegram.ensure_session(
            self.db,
            "chat",
            "provider::model",
        )
        self.fields = {"name": "Mira"}

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def _operation(self, operation_id, state, kind="command"):
        now = time.time()
        self.db.execute(
            "INSERT OR REPLACE INTO operations("
            "operation_id,kind,state,created_at,updated_at"
            ") VALUES(?,?,?,?,?)",
            (str(operation_id), kind, state, now, now),
        )
        self.db.commit()

    def _turns(self, user="question", assistant="answer"):
        now = time.time()
        user_cursor = self.db.execute(
            "INSERT INTO messages("
            "chat_id,session_id,role,content,created_at"
            ") VALUES(?,?,?,?,?)",
            (
                "chat",
                self.session["session_id"],
                "user",
                user,
                now,
            ),
        )
        assistant_cursor = self.db.execute(
            "INSERT INTO messages("
            "chat_id,session_id,role,content,created_at"
            ") VALUES(?,?,?,?,?)",
            (
                "chat",
                self.session["session_id"],
                "assistant",
                assistant,
                now + 0.001,
            ),
        )
        self.db.commit()
        return int(user_cursor.lastrowid), int(assistant_cursor.lastrowid)

    def _payload(self, operation_id, payload):
        _m_session_naming.set_meta(
            self.db,
            f"operation_payload:{operation_id}",
            json.dumps(payload, separators=(",", ":")),
        )

    def test_regen_local_committed_redelivers_selected_variant_without_generation(self):
        operation_id = 601
        user_rowid, assistant_rowid = self._turns(
            "question",
            "committed regen",
        )
        for response in ("v1", "v2", "committed regen"):
            _m_sync_core.save_response_variant(
                self.db,
                "chat",
                self.session["session_id"],
                "question",
                response,
                user_rowid=user_rowid,
            )
        self._operation(operation_id, "local_committed", "regen")
        self._payload(
            operation_id,
            {"old_message_ids": ["41", "42"], "user_rowid": user_rowid},
        )

        with patch.object(
            _m_generation,
            "generate_text",
            side_effect=AssertionError("provider must not run"),
        ), patch.object(_m_generation, "delete_outgoing_message_row",
        ) as delete_current, patch.object(_m_generation, "telegram_request",
            return_value={},
        ) as telegram, patch.object(_m_generation, "send_reply",
        ) as send_reply:
            _m_message_commands.regenerate_last(
                self.db,
                "token",
                "key",
                self.session,
                self.fields,
                "chat",
                operation_id=operation_id,
             memory_service=make_test_memory_service(), persona_service=make_test_persona_service())

        delete_current.assert_called_once_with(
            self.db,
            "token",
            "chat",
            assistant_rowid,
        )
        deleted = [
            call.args[2]["message_id"]
            for call in telegram.call_args_list
            if call.args[1] == "deleteMessage"
        ]
        self.assertEqual(deleted, [41, 42])
        self.assertIn(
            "Regenerated response (variant 3)",
            send_reply.call_args.args[2],
        )
        self.assertEqual(_m_message_commands.operation_phase(self.db, operation_id), "applied")
        self.assertEqual(
            _m_session_naming.get_meta(self.db, f"operation_payload:{operation_id}", ""),
            "",
        )

    def test_regen_delivery_failure_keeps_local_committed_and_payload(self):
        operation_id = 602
        user_rowid, _assistant_rowid = self._turns(
            "question",
            "committed regen",
        )
        _m_sync_core.save_response_variant(
            self.db,
            "chat",
            self.session["session_id"],
            "question",
            "committed regen",
            user_rowid=user_rowid,
        )
        self._operation(operation_id, "local_committed", "regen")
        self._payload(
            operation_id,
            {"old_message_ids": [], "user_rowid": user_rowid},
        )

        with patch.object(
            _m_generation,
            "generate_text",
            side_effect=AssertionError("provider must not run"),
        ), patch.object(
            _m_media,
            "delete_outgoing_message_row",
        ), patch.object(
            _m_media,
            "send_reply",
            side_effect=RuntimeError("Telegram sendMessage failed"),
        ):
            with self.assertRaisesRegex(
                RuntimeError,
                "Telegram sendMessage failed",
            ):
                _m_message_commands.regenerate_last(
                    self.db,
                    "token",
                    "key",
                    self.session,
                    self.fields,
                    "chat",
                    operation_id=operation_id,
                 memory_service=make_test_memory_service(), persona_service=make_test_persona_service())

        self.assertEqual(
            _m_message_commands.operation_phase(self.db, operation_id),
            "local_committed",
        )
        self.assertNotEqual(
            _m_session_naming.get_meta(self.db, f"operation_payload:{operation_id}", ""),
            "",
        )

    def test_regen_incomplete_recovery_raises_without_finishing(self):
        operation_id = 603
        self._operation(operation_id, "local_committed", "regen")

        with self.assertRaisesRegex(
            RuntimeError,
            "regen recovery state is incomplete",
        ):
            _m_message_commands.regenerate_last(
                self.db,
                "token",
                "key",
                self.session,
                self.fields,
                "chat",
                operation_id=operation_id,
             memory_service=make_test_memory_service(), persona_service=make_test_persona_service())

        self.assertEqual(
            _m_message_commands.operation_phase(self.db, operation_id),
            "local_committed",
        )

    def test_continue_local_committed_redelivers_without_generation(self):
        operation_id = 607
        self._turns("question", "committed continuation")
        self._operation(operation_id, "local_committed", "continue")
        self._payload(
            operation_id,
            {
                "old_message_ids": ["51"],
                "assistant_rowid": 1,
            },
        )

        with patch.object(
            _m_generation,
            "generate_text",
            side_effect=AssertionError("provider must not run"),
        ), patch.object(_m_generation, "delete_outgoing_message_row",
        ), patch.object(_m_generation, "telegram_request",
            return_value={},
        ), patch.object(_m_generation, "send_reply",
        ) as send_reply:
            _m_message_commands.continue_last(
                self.db,
                "token",
                "key",
                self.session,
                self.fields,
                "chat",
                operation_id=operation_id,
             memory_service=make_test_memory_service(), persona_service=make_test_persona_service())

        self.assertIn(
            "↪️ Continued response",
            send_reply.call_args.args[2],
        )
        self.assertIn(
            "committed continuation",
            send_reply.call_args.args[2],
        )
        self.assertEqual(
            _m_message_commands.operation_phase(self.db, operation_id),
            "applied",
        )

    def test_edit_local_committed_redelivers_without_generation(self):
        operation_id = 608
        user_rowid, _assistant_rowid = self._turns(
            "edited user",
            "committed edit",
        )
        self._operation(operation_id, "local_committed", "edit")
        self._payload(
            operation_id,
            {"old_message_ids": ["61"], "user_rowid": user_rowid},
        )

        with patch.object(_m_commands, "generate_text",
            side_effect=AssertionError("provider must not run"),
        ), patch.object(_m_commands, "delete_outgoing_message_row",
        ), patch.object(_m_commands, "telegram_request",
            return_value={},
        ), patch.object(_m_commands, "send_reply",
        ) as send_reply:
            _m_commands.regenerate_edited_turn(
                self.db,
                "token",
                "key",
                self.session,
                self.fields,
                "chat",
                user_rowid,
                "replacement",
                operation_id=operation_id,
             memory_service=make_test_memory_service(), persona_service=make_test_persona_service())

        self.assertIn(
            "✏️ Edited message regenerated.",
            send_reply.call_args.args[2],
        )
        self.assertIn(
            "committed edit",
            send_reply.call_args.args[2],
        )
        self.assertEqual(
            _m_message_commands.operation_phase(self.db, operation_id),
            "applied",
        )

    def test_continue_incomplete_recovery_raises_without_finishing(self):
        operation_id = 609
        self._operation(operation_id, "local_committed", "continue")

        with self.assertRaisesRegex(
            RuntimeError,
            "continue recovery state is incomplete",
        ):
            _m_message_commands.continue_last(
                self.db,
                "token",
                "key",
                self.session,
                self.fields,
                "chat",
                operation_id=operation_id,
             memory_service=make_test_memory_service(), persona_service=make_test_persona_service())

        self.assertEqual(
            _m_message_commands.operation_phase(self.db, operation_id),
            "local_committed",
        )

    def test_edit_incomplete_recovery_raises_without_finishing(self):
        operation_id = 610
        self._operation(operation_id, "local_committed", "edit")

        with self.assertRaisesRegex(
            RuntimeError,
            "edit recovery state is incomplete",
        ):
            _m_commands.regenerate_edited_turn(
                self.db,
                "token",
                "key",
                self.session,
                self.fields,
                "chat",
                1,
                "replacement",
                operation_id=operation_id,
             memory_service=make_test_memory_service(), persona_service=make_test_persona_service())

        self.assertEqual(
            _m_message_commands.operation_phase(self.db, operation_id),
            "local_committed",
        )

    def test_process_message_routes_local_committed_regen_before_generic_delivery(self):
        operation_id = 604
        self._operation(operation_id, "local_committed", "regen")
        memory = object()
        services = make_test_application_services(memory=memory)

        with patch.object(_m_message_commands, "load_session",
            return_value=self.session,
        ), patch.object(_m_message_commands, "card_fields_from_file",
            return_value=self.fields,
        ), patch.object(_m_message_commands, "regenerate_last",
        ) as regen, patch.object(_m_message_commands, "send_reply",
            side_effect=AssertionError("generic recovery ran first"),
        ):
            _m_message_commands.process_message(
                self.db,
                "token",
                "key",
                "provider::model",
                self.fields,
                "chat",
                "/regen",
                queued_session_id=self.session["session_id"],
                operation_id=operation_id,
                services=services,
            )

        regen.assert_called_once()
        self.assertIs(regen.call_args.kwargs["memory_service"], memory)
        self.assertIs(regen.call_args.kwargs["persona_service"], services.persona)

    def test_process_message_routes_local_committed_continue_before_generic_delivery(self):
        operation_id = 611
        self._operation(operation_id, "local_committed", "continue")
        memory = object()
        services = make_test_application_services(memory=memory)

        with patch.object(_m_message_commands, "load_session",
            return_value=self.session,
        ), patch.object(_m_message_commands, "card_fields_from_file",
            return_value=self.fields,
        ), patch.object(_m_message_commands, "continue_last",
        ) as continuation, patch.object(_m_message_commands, "send_reply",
            side_effect=AssertionError("generic recovery ran first"),
        ):
            _m_message_commands.process_message(
                self.db,
                "token",
                "key",
                "provider::model",
                self.fields,
                "chat",
                "/continue",
                queued_session_id=self.session["session_id"],
                operation_id=operation_id,
                services=services,
            )

        continuation.assert_called_once()
        self.assertIs(continuation.call_args.kwargs["memory_service"], memory)
        self.assertIs(continuation.call_args.kwargs["persona_service"], services.persona)

    def test_process_message_routes_local_committed_edit_before_generic_delivery(self):
        operation_id = 612
        self._operation(operation_id, "local_committed", "edit")
        memory = object()
        services = make_test_application_services(memory=memory)

        with patch.object(_m_message_commands, "load_session",
            return_value=self.session,
        ), patch.object(_m_message_commands, "card_fields_from_file",
            return_value=self.fields,
        ), patch.object(_m_message_commands, "edit_last_user",
        ) as edit, patch.object(_m_message_commands, "send_reply",
            side_effect=AssertionError("generic recovery ran first"),
        ):
            _m_message_commands.process_message(
                self.db,
                "token",
                "key",
                "provider::model",
                self.fields,
                "chat",
                "/edit replacement",
                queued_session_id=self.session["session_id"],
                operation_id=operation_id,
                services=services,
            )

        edit.assert_called_once()
        self.assertEqual(edit.call_args.args[6], "replacement")
        self.assertIs(edit.call_args.kwargs["memory_service"], memory)
        self.assertIs(edit.call_args.kwargs["persona_service"], services.persona)

    def test_reset_memory_purged_resume_skips_second_remote_purge(self):
        operation_id = 605
        self._turns()
        self._operation(operation_id, "memory_purged", "reset")
        memory = Mock()
        memory.purge_session.side_effect = AssertionError(
            "remote purge repeated"
        )

        _m_panel_callback_routes.reset_session(
            self.db,
            "token",
            "chat",
            self.session,
            operation_id=operation_id,
            memory_service=memory,
        )

        count = self.db.execute(
            "SELECT COUNT(*) FROM messages "
            "WHERE chat_id=? AND session_id=?",
            ("chat", self.session["session_id"]),
        ).fetchone()[0]
        self.assertEqual(count, 0)
        self.assertEqual(_m_message_commands.operation_phase(self.db, operation_id), "applied")

    def test_reset_local_committed_resume_only_marks_applied(self):
        operation_id = 606
        self._turns()
        self._operation(operation_id, "local_committed", "reset")
        memory = Mock()

        _m_panel_callback_routes.reset_session(
            self.db,
            "token",
            "chat",
            self.session,
            operation_id=operation_id,
            memory_service=memory,
        )

        count = self.db.execute(
            "SELECT COUNT(*) FROM messages "
            "WHERE chat_id=? AND session_id=?",
            ("chat", self.session["session_id"]),
        ).fetchone()[0]
        self.assertEqual(count, 2)
        memory.purge_session.assert_not_called()
        self.assertEqual(_m_message_commands.operation_phase(self.db, operation_id), "applied")


class DurableRecoveryOwnershipTests(unittest.TestCase):
    def test_recovery_compatibility_file_is_absent(self):
        recovery = (
            Path(__file__).parents[1] / "bridge" / "recovery.py"
        )
        self.assertFalse(recovery.exists())

    def test_database_begin_operation_uses_serialized_short_write(self):
        source = (
            Path(__file__).parents[1] / "bridge" / "database.py"
        ).read_text(encoding="utf-8")
        start = source.index("def begin_operation")
        end = source.index(
            "\ndef operation_was_applied",
            start,
        )
        chunk = source[start:end]
        self.assertIn("run_write_txn(db, write)", chunk)

    def test_generation_owns_regen_and_continue_recovery_adapter(self):
        source = (
            Path(__file__).parents[1] / "bridge" / "generation.py"
        ).read_text(encoding="utf-8")
        self.assertIn(
            "from bridge.operation_recovery import",
            source,
        )
        self.assertIn(
            "_GENERATION_OPERATION_RECOVERY",
            source,
        )
        self.assertIn(
            "def regenerate_last(",
            source,
        )
        self.assertIn(
            "def continue_last(",
            source,
        )

    def test_commands_owns_edited_turn_recovery_adapter(self):
        source = (
            Path(__file__).parents[1] / "bridge" / "commands.py"
        ).read_text(encoding="utf-8")
        self.assertIn(
            "from bridge.operation_recovery import",
            source,
        )
        self.assertIn(
            "_COMMAND_OPERATION_RECOVERY",
            source,
        )
        self.assertIn(
            "def regenerate_edited_turn(",
            source,
        )

    def test_operation_command_normalizes_bot_addressed_command(self):
        cases = {
            "/regen@BridgeBot": "/regen",
            "/continue@BridgeBot": "/continue",
            "@someone /regen": "/regen",
            "@someone /continue": "/continue",
        }
        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(
                    _m_message_commands._operation_command(raw),
                    expected,
                )

    def test_operation_recovery_is_not_exec_loaded(self):
        self.assertFalse(
            (Path(__file__).parents[1] / "bridge" / "runtime_loader.py").exists()
        )


class OperationRecoveryUnitTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "adapter.sqlite3"
        self.db = _m_memory_curator.db_connect(self.path)
        self.telegram = Mock(return_value={})
        self.log_info = Mock()
        self.adapter = OperationRecovery(
            operation_phase=_m_message_commands.operation_phase,
            begin_operation=_m_panel_callback_routes.begin_operation,
            record_operation=_m_panel_callback_routes.record_operation,
            run_write_txn=_m_sync_api.run_write_txn,
            get_meta=_m_session_naming.get_meta,
            telegram_request=self.telegram,
            delete_outgoing_message_row=Mock(),
            log_info=self.log_info,
        )

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def test_payload_missing_malformed_and_non_object_are_empty(self):
        self.assertEqual(self.adapter.get_payload(self.db, 701), {})
        _m_session_naming.set_meta(self.db, "operation_payload:701", "{bad")
        self.assertEqual(self.adapter.get_payload(self.db, 701), {})
        _m_session_naming.set_meta(self.db, "operation_payload:701", "[1,2]")
        self.assertEqual(self.adapter.get_payload(self.db, 701), {})

    def test_message_id_decoder_preserves_order_and_deduplicates(self):
        rows = [
            ("10", '["11","10",null,""]'),
            (None, '["12","11"]'),
            ("", "not-json"),
        ]
        self.assertEqual(
            self.adapter.message_ids_from_rows(rows),
            ["10", "11", "12"],
        )

    def test_local_committed_delivery_failure_does_not_finish(self):
        now = time.time()
        self.db.execute(
            "INSERT INTO operations("
            "operation_id,kind,state,created_at,updated_at"
            ") VALUES(?,?,?,?,?)",
            ("702", "regen", "local_committed", now, now),
        )
        self.db.commit()

        def fail_delivery():
            raise RuntimeError("delivery failed")

        with self.assertRaisesRegex(RuntimeError, "delivery failed"):
            self.adapter.begin_or_recover(
                self.db,
                702,
                "regen",
                fail_delivery,
            )
        self.assertEqual(
            _m_message_commands.operation_phase(self.db, 702),
            "local_committed",
        )

    def test_finish_marks_applied_and_removes_payload(self):
        now = time.time()
        self.db.execute(
            "INSERT INTO operations("
            "operation_id,kind,state,created_at,updated_at"
            ") VALUES(?,?,?,?,?)",
            ("703", "regen", "local_committed", now, now),
        )
        self.db.commit()
        self.adapter.set_payload(
            self.db,
            703,
            {"old_message_ids": ["9"]},
        )

        self.adapter.finish(self.db, 703, "regen")

        self.assertEqual(_m_message_commands.operation_phase(self.db, 703), "applied")
        self.assertEqual(
            _m_session_naming.get_meta(self.db, "operation_payload:703", ""),
            "",
        )

    def test_cleanup_failure_is_logged_and_next_id_is_attempted(self):
        self.telegram.side_effect = [
            RuntimeError("delete failed"),
            {},
        ]

        self.adapter.delete_stored_telegram_ids(
            "token",
            "chat",
            ["9", "10"],
        )

        self.assertEqual(self.telegram.call_count, 2)
        self.log_info.assert_called_once_with(
            "Recovery cleanup could not delete Telegram message %s",
            "9",
            exc_info=True,
        )


if __name__ == "__main__":
    unittest.main()
