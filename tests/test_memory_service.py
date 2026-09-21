from pathlib import Path
from types import SimpleNamespace
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

import bridge.runtime as rt

from bridge.memory_service import MemoryPromptContext, MemoryService


class MemoryServiceTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        self.session = {"session_id": "session-1", "character_file": "mira.png"}
        self.fields = {"name": "Mira"}

    def tearDown(self):
        self.db.close()

    def _service(
        self,
        *,
        recall=None,
        summary=None,
        summary_state=None,
        retain=None,
        purge=None,
    ):
        return MemoryService(
            recall_context=recall or (lambda *_args, **_kwargs: "memory"),
            summary_for_prompt=summary or (lambda *_args, **_kwargs: "summary"),
            summary_state=summary_state or (lambda *_args, **_kwargs: ("summary", 0)),
            retain_session=retain or (lambda *_args, **_kwargs: None),
            purge_session_memory=purge or (lambda *_args, **_kwargs: 0),
        )

    def test_normal_prompt_context_combines_recall_and_summary(self):
        calls = []
        service = self._service(
            recall=lambda db, chat_id, session, fields, query: (
                calls.append(("recall", chat_id, session["session_id"], fields["name"], query))
                or "remembered fact"
            ),
            summary=lambda db, chat_id, session: (
                calls.append(("summary", chat_id, session["session_id"]))
                or "continuity summary"
            ),
            summary_state=lambda *_args: (_ for _ in ()).throw(
                AssertionError("normal prompt must not read summary state")
            ),
        )

        result = service.prompt_context(
            self.db,
            "chat",
            self.session,
            self.fields,
            "Where are we?",
        )

        self.assertEqual(
            result,
            MemoryPromptContext(
                recall="remembered fact",
                summary="continuity summary",
            ),
        )
        self.assertEqual(
            calls,
            [
                ("recall", "chat", "session-1", "Mira", "Where are we?"),
                ("summary", "chat", "session-1"),
            ],
        )

    def test_edit_covered_by_summary_suppresses_summary_without_regeneration(self):
        summary_calls = []
        service = self._service(
            recall=lambda *_args: "memory",
            summary=lambda *_args: summary_calls.append("called") or "stale summary",
            summary_state=lambda _db, chat_id, session_id: (
                "stored summary",
                42,
            ),
        )

        result = service.prompt_context(
            self.db,
            "chat",
            self.session,
            self.fields,
            "edited text",
            edited_user_rowid=20,
        )

        self.assertEqual(result.recall, "memory")
        self.assertEqual(result.summary, "")
        self.assertEqual(summary_calls, [])

    def test_edit_not_covered_by_summary_uses_normal_summary_provider(self):
        service = self._service(
            recall=lambda *_args: "memory",
            summary=lambda *_args: "safe summary",
            summary_state=lambda *_args: ("older summary", 9),
        )

        result = service.prompt_context(
            self.db,
            "chat",
            self.session,
            self.fields,
            "edited text",
            edited_user_rowid=20,
        )

        self.assertEqual(
            result,
            MemoryPromptContext(recall="memory", summary="safe summary"),
        )

    def test_retain_delegates_to_injected_provider(self):
        calls = []
        service = self._service(
            retain=lambda db, chat_id, session, fields: calls.append(
                (db, chat_id, session, fields)
            )
        )

        service.retain(self.db, "chat", self.session, self.fields)

        self.assertEqual(
            calls,
            [(self.db, "chat", self.session, self.fields)],
        )

    def test_purge_session_returns_injected_provider_result(self):
        calls = []
        service = self._service(
            purge=lambda db, chat_id, session_id: (
                calls.append((db, chat_id, session_id)) or 7
            )
        )

        result = service.purge_session(self.db, "chat", "session-1")

        self.assertEqual(result, 7)
        self.assertEqual(calls, [(self.db, "chat", "session-1")])

    def test_summary_status_delegates_to_summary_state_provider(self):
        calls = []
        service = self._service(
            summary_state=lambda db, chat_id, session_id: (
                calls.append((db, chat_id, session_id))
                or ("stored summary", 23)
            )
        )

        result = service.summary_status(self.db, "chat", "session-1")

        self.assertEqual(result, ("stored summary", 23))
        self.assertEqual(calls, [(self.db, "chat", "session-1")])


class MemoryServiceMessageIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_db = rt.DB_FILE
        rt.DB_FILE = Path(self.tmp.name) / "bridge.sqlite3"
        with rt._DB_SCHEMA_LOCK:
            rt._DB_SCHEMA_READY = False
        self.db = rt.db_connect()
        self.session = rt.create_session(
            self.db,
            "chat",
            "provider::model",
            session_id="memory-message",
        )
        self.fields = {"name": "Mira"}

    def tearDown(self):
        self.db.close()
        rt.DB_FILE = self.old_db
        with rt._DB_SCHEMA_LOCK:
            rt._DB_SCHEMA_READY = False
        self.tmp.cleanup()

    def test_generate_and_store_reply_uses_injected_memory_service(self):
        calls = []
        captured = {}

        class FakeMemory:
            def prompt_context(
                self,
                db,
                chat_id,
                session,
                fields,
                query,
                **kwargs,
            ):
                calls.append(
                    (
                        "context",
                        db,
                        chat_id,
                        session["session_id"],
                        fields["name"],
                        query,
                        kwargs,
                    )
                )
                return MemoryPromptContext(
                    recall="service recall",
                    summary="service summary",
                )

            def retain(self, db, chat_id, session, fields):
                calls.append(
                    (
                        "retain",
                        db,
                        chat_id,
                        session["session_id"],
                        fields["name"],
                    )
                )

        def build_messages(
            session,
            fields,
            text,
            history_rows,
            **kwargs,
        ):
            captured.update(kwargs)
            return [{"role": "user", "content": text}]

        def legacy_called(*_args, **_kwargs):
            raise AssertionError("legacy memory global must not run")

        with patch.object(
            rt,
            "recall_memory_context",
            side_effect=legacy_called,
        ), patch.object(
            rt,
            "session_summary_for_prompt",
            side_effect=legacy_called,
        ), patch.object(
            rt,
            "retain_session_memory",
            side_effect=legacy_called,
        ), patch.object(
            rt,
            "rag_retrieval_bundle",
            return_value={},
        ), patch.object(
            rt,
            "rag_context_for_prompt",
            return_value="",
        ), patch.object(
            rt,
            "rag_citation_footer",
            return_value="",
        ), patch.object(
            rt,
            "build_chat_messages",
            side_effect=build_messages,
        ), patch.object(
            rt,
            "send_typing",
        ), patch.object(
            rt,
            "normalize_response_language",
            return_value="en",
        ), patch.object(
            rt,
            "get_generation_settings",
            return_value={},
        ), patch.object(
            rt,
            "generate_text",
            return_value="reply",
        ), patch.object(
            rt,
            "render_response_language",
            side_effect=lambda _key, _model, reply, *_args: reply,
        ), patch.object(
            rt,
            "save_response_variant",
            return_value=1,
        ), patch.object(
            rt,
            "queue_user_quote_tts",
        ), patch.object(
            rt,
            "send_reply",
        ):
            rt.generate_and_store_reply(
                self.db,
                "token",
                "key",
                self.fields,
                "chat",
                "hello",
                self.session,
                self.session["session_id"],
                "provider::model",
                None,
                "",
                None,
                None,
                memory_service=FakeMemory(),
            )

        self.assertEqual(captured["memory_context"], "service recall")
        self.assertEqual(captured["session_summary"], "service summary")
        self.assertEqual(calls[0][0], "context")
        self.assertEqual(calls[0][2:6], ("chat", "memory-message", "Mira", "hello"))
        self.assertEqual(calls[0][6], {})
        self.assertEqual(calls[1][0], "retain")
        self.assertEqual(calls[1][2:], ("chat", "memory-message", "Mira"))

    def test_edited_turn_recovery_uses_injected_memory_service(self):
        now = rt.time.time()
        user_cursor = self.db.execute(
            "INSERT INTO messages(chat_id,session_id,role,content,created_at) "
            "VALUES(?,?,?,?,?)",
            ("chat", self.session["session_id"], "user", "old text", now),
        )
        user_rowid = int(user_cursor.lastrowid)
        self.db.execute(
            "INSERT INTO messages(chat_id,session_id,role,content,created_at) "
            "VALUES(?,?,?,?,?)",
            ("chat", self.session["session_id"], "assistant", "old reply", now + 0.001),
        )
        self.db.commit()
        calls = []
        captured = {}

        class FakeMemory:
            def prompt_context(
                self,
                db,
                chat_id,
                session,
                fields,
                query,
                **kwargs,
            ):
                calls.append(("context", kwargs))
                return MemoryPromptContext(
                    recall="recovery recall",
                    summary="recovery summary",
                )

            def retain(self, db, chat_id, session, fields):
                calls.append(("retain", chat_id, session["session_id"]))

        def legacy_called(*_args, **_kwargs):
            raise AssertionError("legacy memory global must not run")

        def build_messages(
            session,
            fields,
            text,
            history_rows,
            **kwargs,
        ):
            captured.update(kwargs)
            return [{"role": "user", "content": text}]

        with patch.object(
            rt,
            "recall_memory_context",
            side_effect=legacy_called,
        ), patch.object(
            rt,
            "get_session_summary",
            side_effect=legacy_called,
        ), patch.object(
            rt,
            "session_summary_for_prompt",
            side_effect=legacy_called,
        ), patch.object(
            rt,
            "retain_session_memory",
            side_effect=legacy_called,
        ), patch.object(
            rt,
            "rag_retrieval_bundle",
            return_value={},
        ), patch.object(
            rt,
            "rag_context_for_prompt",
            return_value="",
        ), patch.object(
            rt,
            "build_chat_messages",
            side_effect=build_messages,
        ), patch.object(
            rt,
            "send_typing",
        ), patch.object(
            rt,
            "generate_text",
            return_value="new reply",
        ), patch.object(
            rt,
            "rag_citation_footer",
            return_value="",
        ), patch.object(
            rt,
            "render_session_response",
            side_effect=lambda _api_key, _session, reply, _chat_id, _settings: reply,
        ), patch.object(
            rt,
            "save_response_variant",
        ), patch.object(
            rt,
            "send_reply",
        ):
            rt.regenerate_edited_turn(
                self.db,
                "token",
                "key",
                self.session,
                self.fields,
                "chat",
                user_rowid,
                "new text",
                memory_service=FakeMemory(),
            )

        self.assertEqual(
            calls[0],
            ("context", {"edited_user_rowid": user_rowid}),
        )
        self.assertEqual(captured["memory_context"], "recovery recall")
        self.assertEqual(captured["session_summary"], "recovery summary")
        self.assertEqual(
            calls[1],
            ("retain", "chat", self.session["session_id"]),
        )

    def test_regen_command_propagates_injected_memory_service(self):
        memory = object()
        captured = {}

        def fake_regen(*args, **kwargs):
            captured.update(kwargs)

        with patch.object(
            rt,
            "_dispatch_extension_command_routes",
            return_value=False,
        ), patch.object(
            rt,
            "regenerate_last",
            side_effect=fake_regen,
        ):
            handled = rt.handle_command_route(
                self.db,
                "token",
                "key",
                "provider::model",
                self.fields,
                "chat",
                "/regen",
                "/regen",
                self.session,
                self.session["session_id"],
                "provider::model",
                "",
                "User",
                services=SimpleNamespace(memory=memory),
            )

        self.assertTrue(handled)
        self.assertIs(captured["memory_service"], memory)

    def test_pending_input_receives_injected_memory_service(self):
        memory = object()
        captured = {}

        def fake_pending(*args, **kwargs):
            captured.update(kwargs)
            return True

        with patch.object(
            rt,
            "handle_pending_input",
            side_effect=fake_pending,
        ):
            rt.process_message(
                self.db,
                "token",
                "key",
                "provider::model",
                self.fields,
                "chat",
                "replacement text",
                services=SimpleNamespace(
                    memory=memory,
                    group_director=None,
                ),
            )

        self.assertIs(captured["memory_service"], memory)

    def test_image_message_uses_injected_memory_service(self):
        calls = []
        captured = {}

        class FakeMemory:
            def prompt_context(
                self,
                db,
                chat_id,
                session,
                fields,
                query,
                **kwargs,
            ):
                calls.append(("context", query))
                return MemoryPromptContext(
                    recall="image recall",
                    summary="image summary",
                )

            def retain(self, db, chat_id, session, fields):
                calls.append(("retain", session["session_id"]))

        def legacy_called(*_args, **_kwargs):
            raise AssertionError("legacy memory global must not run")

        def build_messages(
            session,
            fields,
            text,
            history_rows,
            **kwargs,
        ):
            captured.update(kwargs)
            return [{"role": "user", "content": text}]

        with patch.object(
            rt,
            "group_current_speaker",
            return_value=None,
        ), patch.object(
            rt,
            "recall_memory_context",
            side_effect=legacy_called,
        ), patch.object(
            rt,
            "session_summary_for_prompt",
            side_effect=legacy_called,
        ), patch.object(
            rt,
            "retain_session_memory",
            side_effect=legacy_called,
        ), patch.object(
            rt,
            "rag_retrieval_bundle",
            return_value={},
        ), patch.object(
            rt,
            "rag_context_for_prompt",
            return_value="",
        ), patch.object(
            rt,
            "build_chat_messages",
            side_effect=build_messages,
        ), patch.object(
            rt,
            "send_typing",
        ), patch.object(
            rt,
            "get_generation_settings",
            return_value={},
        ), patch.object(
            rt,
            "generate_text",
            return_value="image reply",
        ), patch.object(
            rt,
            "rag_citation_footer",
            return_value="",
        ), patch.object(
            rt,
            "render_session_response",
            side_effect=lambda _key, _session, reply, *_args: reply,
        ), patch.object(
            rt,
            "save_response_variant",
            return_value=1,
        ), patch.object(
            rt,
            "send_reply",
        ):
            rt.process_image_message(
                self.db,
                "token",
                "key",
                self.session,
                self.fields,
                "chat",
                "describe this",
                b"image-bytes",
                memory_service=FakeMemory(),
            )

        self.assertEqual(captured["memory_context"], "image recall")
        self.assertEqual(captured["session_summary"], "image summary")
        self.assertEqual(calls, [
            ("context", "describe this"),
            ("retain", self.session["session_id"]),
        ])

    def test_telegram_image_adapter_propagates_memory_service(self):
        memory = object()
        captured = {}

        with patch.object(
            rt,
            "download_telegram_file",
            return_value=b"image",
        ), patch.object(
            rt,
            "ensure_session",
            return_value=self.session,
        ), patch.object(
            rt,
            "card_fields_from_file",
            return_value=self.fields,
        ), patch.object(
            rt,
            "process_image_message",
            side_effect=lambda *_args, **kwargs: captured.update(kwargs),
        ):
            rt.process_telegram_image(
                self.db,
                "token",
                "chat",
                "file-id",
                "caption",
                "provider::model",
                memory_service=memory,
            )

        self.assertIs(captured["memory_service"], memory)

    def test_png_document_adapter_propagates_memory_service(self):
        memory = object()
        captured = {}
        document = {
            "file_name": "photo.png",
            "file_id": "file-id",
            "file_size": 5,
            "caption": "caption",
        }

        with patch.object(
            rt,
            "_consume_world_upload",
            return_value=False,
        ), patch.object(
            rt,
            "download_telegram_file",
            return_value=b"not-a-card",
        ), patch.object(
            rt,
            "parse_png_chara_bytes",
            side_effect=ValueError("not card"),
        ), patch.object(
            rt,
            "ensure_session",
            return_value=self.session,
        ), patch.object(
            rt,
            "card_fields_from_file",
            return_value=self.fields,
        ), patch.object(
            rt,
            "process_image_message",
            side_effect=lambda *_args, **kwargs: captured.update(kwargs),
        ):
            rt.import_telegram_document(
                self.db,
                "token",
                "chat",
                document,
                "provider::model",
                memory_service=memory,
            )

        self.assertIs(captured["memory_service"], memory)



class MemoryServiceCompatibilityBoundaryTests(unittest.TestCase):
    def test_compatibility_service_binds_current_runtime_memory_collaborators(self):
        calls = []
        session = {"session_id": "compat-session"}
        fields = {"name": "Mira"}

        with patch.object(
            rt,
            "recall_memory_context",
            side_effect=lambda *_args: calls.append("recall") or "compat recall",
        ), patch.object(
            rt,
            "session_summary_for_prompt",
            side_effect=lambda *_args: calls.append("summary") or "compat summary",
        ), patch.object(
            rt,
            "get_session_summary",
            side_effect=lambda *_args: ("stored", 0),
        ), patch.object(
            rt,
            "retain_session_memory",
            side_effect=lambda *_args: calls.append("retain"),
        ), patch.object(
            rt,
            "purge_hindsight_session",
            side_effect=lambda *_args: calls.append("purge") or 4,
        ):
            service = rt.compatibility_memory_service()
            context = service.prompt_context(
                sqlite3.connect(":memory:"),
                "chat",
                session,
                fields,
                "query",
            )
            service.retain(None, "chat", session, fields)
            purged = service.purge_session(None, "chat", "compat-session")

        self.assertEqual(
            context,
            MemoryPromptContext(
                recall="compat recall",
                summary="compat summary",
            ),
        )
        self.assertEqual(purged, 4)
        self.assertEqual(calls, ["recall", "summary", "retain", "purge"])

    def test_reviewed_application_paths_do_not_call_memory_backend_functions_directly(self):
        root = Path(__file__).parents[1] / "bridge"
        reviewed = (
            "commands.py",
            "generation.py",
            "message_commands.py",
            "recovery.py",
        )
        forbidden_calls = (
            "recall_memory_context(",
            "session_summary_for_prompt(",
            "retain_session_memory(",
            "purge_hindsight_session(",
            "get_session_summary(",
        )

        for filename in reviewed:
            source = (root / filename).read_text(encoding="utf-8")
            for forbidden in forbidden_calls:
                with self.subTest(filename=filename, forbidden=forbidden):
                    self.assertNotIn(forbidden, source)

    def test_reset_session_uses_memory_service_boundary(self):
        tmp = tempfile.TemporaryDirectory()
        old_db = rt.DB_FILE
        try:
            rt.DB_FILE = Path(tmp.name) / "reset.sqlite3"
            with rt._DB_SCHEMA_LOCK:
                rt._DB_SCHEMA_READY = False
            db = rt.db_connect()
            session = rt.create_session(
                db,
                "chat",
                "provider::model",
                session_id="reset-memory",
            )
            db.execute(
                "INSERT INTO messages(chat_id,session_id,role,content,created_at) "
                "VALUES(?,?,?,?,?)",
                ("chat", "reset-memory", "user", "hello", rt.time.time()),
            )
            db.commit()
            calls = []

            class FakeMemory:
                def purge_session(self, current_db, chat_id, session_id):
                    calls.append((current_db, chat_id, session_id))
                    return 1

            with patch.object(
                rt,
                "purge_hindsight_session",
                side_effect=AssertionError("raw purge must not run"),
            ), patch.object(
                rt,
                "optimize_database",
            ):
                rt.reset_session(
                    db,
                    "token",
                    "chat",
                    session,
                    memory_service=FakeMemory(),
                )

            self.assertEqual(calls, [(db, "chat", "reset-memory")])
            self.assertEqual(
                db.execute(
                    "SELECT COUNT(*) FROM messages WHERE chat_id=? AND session_id=?",
                    ("chat", "reset-memory"),
                ).fetchone()[0],
                0,
            )
            db.close()
        finally:
            rt.DB_FILE = old_db
            with rt._DB_SCHEMA_LOCK:
                rt._DB_SCHEMA_READY = False
            tmp.cleanup()

if __name__ == "__main__":
    unittest.main()
