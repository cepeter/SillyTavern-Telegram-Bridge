from application_test_setup import ensure_application_extensions, make_test_provider_port

ensure_application_extensions()

import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

import bridge.extension_registry as registry
import bridge.config as config
import bridge.memory_backend as memory_backend
import time
import bridge.main as _m_main
import bridge.memory as _m_memory
import bridge.memory_curator as _m_memory_curator
import bridge.session_naming as _m_session_naming
import bridge.sync_core as _m_sync_core
import bridge.common as _m_common
class _FakeDocuments:
    def __init__(self):
        self.documents = {}
        self.deleted = []
        self.api_client = self

    async def close(self):
        return None

    async def list_documents(
        self,
        bank_id,
        q=None,
        tags=None,
        tags_match=None,
        limit=1000,
        offset=0,
    ):
        items = []
        for document_id, document_tags in self.documents.items():
            if q and q.casefold() not in document_id.casefold():
                continue
            if (
                tags
                and not set(tags).intersection(
                    set(document_tags)
                )
            ):
                continue
            items.append(
                SimpleNamespace(
                    id=document_id,
                    tags=document_tags,
                )
            )
        page = items[offset:offset + limit]
        return SimpleNamespace(
            items=page,
            total=len(items),
            limit=limit,
            offset=offset,
        )

    async def delete_document(
        self,
        bank_id,
        document_id,
    ):
        self.deleted.append(document_id)
        self.documents.pop(document_id, None)
        return SimpleNamespace(success=True)


class _FakeHindsight:
    def __init__(self):
        self.documents = _FakeDocuments()
        self.retained = []

    def retain(self, **kwargs):
        self.retained.append(kwargs)
        self.documents.documents[
            kwargs["document_id"]
        ] = list(kwargs.get("tags") or [])
        return SimpleNamespace(success=True)


class MemoryNativeBackendTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_db = config.DB_FILE
        self.old_hindsight = memory_backend.hindsight_client
        config.DB_FILE = (
            Path(self.tmp.name) / "bridge.sqlite3"
        )
        self.db = _m_memory_curator.db_connect()
        self.session = _m_session_naming.create_session(
            self.db,
            "chat",
            _m_memory_curator.DEFAULT_MODEL,
            session_id="memory-native",
        )
        self.fields = {"name": "Mira"}
        self.provider = make_test_provider_port()

    def tearDown(self):
        memory_backend.hindsight_client = self.old_hindsight
        self.db.close()
        config.DB_FILE = self.old_db
        self.tmp.cleanup()

    def _add_message(self, content="old text"):
        self.db.execute(
            "INSERT INTO messages("
            "chat_id,session_id,role,content,created_at"
            ") VALUES(?,?,?,?,?)",
            (
                "chat",
                self.session["session_id"],
                "user",
                content,
                time.time(),
            ),
        )
        self.db.commit()

    def test_memory_module_owns_guard_and_persistence_helpers(self):
        shell_source = (
            Path(__file__).parents[1]
            / "bridge"
            / "memory.py"
        ).read_text(encoding="utf-8")
        backend_source = (
            Path(__file__).parents[1]
            / "bridge"
            / "memory_backend.py"
        ).read_text(encoding="utf-8")

        self.assertIn(
            "_HINDSIGHT_STALE_GUARD = _HindsightStaleGuard(",
            shell_source,
        )
        for name in (
            "_memory_hindsight_conversation_snapshot",
            "_memory_hindsight_epoch",
            "_memory_hindsight_session_exists",
        ):
            self.assertIn(f"def {name}(", backend_source)
            self.assertNotIn(f"def {name}(", shell_source)

    def test_malformed_and_negative_epoch_values_read_as_zero(self):
        key = (
            "hindsight_epoch:"
            "chat:"
            f"{self.session['session_id']}"
        )

        _m_session_naming.set_meta(self.db, key, "not-an-int")
        self.assertEqual(
            _m_memory._HINDSIGHT_STALE_GUARD.read_epoch(
                self.db,
                "chat",
                self.session["session_id"],
            ),
            0,
        )

        _m_session_naming.set_meta(self.db, key, "-9")
        self.assertEqual(
            _m_memory._HINDSIGHT_STALE_GUARD.read_epoch(
                self.db,
                "chat",
                self.session["session_id"],
            ),
            0,
        )

    def test_direct_guard_rejects_transcript_changed_after_queue(self):
        self._add_message()
        queued = []
        fake = _FakeHindsight()
        memory_backend.hindsight_client = lambda: fake

        with patch.object(
            _m_memory,
            "submit_background",
            side_effect=lambda name, fn, *args, **kwargs: (
                queued.append(
                    (name, fn, args, kwargs)
                )
            ),
        ):
            _m_memory._HINDSIGHT_STALE_GUARD.retain(
                self.db,
                "chat",
                self.session,
                self.fields,
                provider_port=self.provider,
            )

        hindsight_jobs = [
            item
            for item in queued
            if item[0] == "hindsight_retain"
        ]
        self.assertEqual(len(hindsight_jobs), 1)
        self.db.execute(
            "UPDATE messages SET content='new text' "
            "WHERE chat_id=? AND session_id=?",
            ("chat", self.session["session_id"]),
        )
        self.db.commit()

        _name, fn, args, kwargs = hindsight_jobs[0]
        fn(*args, **kwargs)

        self.assertEqual(fake.retained, [])

    def test_successful_direct_guard_purge_advances_epoch_and_clears_mapping(self):
        self._add_message()
        mapped = _m_memory.hindsight_conversation_document_id(
            self.session["session_id"]
        )
        self.db.execute(
            "INSERT INTO hindsight_documents("
            "chat_id,session_id,document_id,kind,created_at"
            ") VALUES(?,?,?,?,?)",
            (
                "chat",
                self.session["session_id"],
                mapped,
                "conversation",
                time.time(),
            ),
        )
        self.db.commit()

        fake = _FakeHindsight()
        fake.documents.documents[mapped] = [
            f"session:{self.session['session_id']}"
        ]
        memory_backend.hindsight_client = lambda: fake

        old_epoch = (
            _m_memory._HINDSIGHT_STALE_GUARD.read_epoch(
                self.db,
                "chat",
                self.session["session_id"],
            )
        )

        deleted = _m_memory._HINDSIGHT_STALE_GUARD.purge(
            self.db,
            "chat",
            self.session["session_id"],
        )

        self.assertGreaterEqual(deleted, 1)
        self.assertEqual(
            _m_memory._HINDSIGHT_STALE_GUARD.read_epoch(
                self.db,
                "chat",
                self.session["session_id"],
            ),
            old_epoch + 1,
        )
        self.assertEqual(
            self.db.execute(
                "SELECT COUNT(*) FROM hindsight_documents "
                "WHERE chat_id=? AND session_id=?",
                ("chat", self.session["session_id"]),
            ).fetchone()[0],
            0,
        )

    def test_failed_remote_purge_preserves_epoch_and_mapping(self):
        mapped = _m_memory.hindsight_conversation_document_id(
            self.session["session_id"]
        )
        self.db.execute(
            "INSERT INTO hindsight_documents("
            "chat_id,session_id,document_id,kind,created_at"
            ") VALUES(?,?,?,?,?)",
            (
                "chat",
                self.session["session_id"],
                mapped,
                "conversation",
                time.time(),
            ),
        )
        self.db.commit()

        key = (
            "hindsight_epoch:"
            "chat:"
            f"{self.session['session_id']}"
        )
        _m_session_naming.set_meta(self.db, key, "5")

        class BrokenDocuments(_FakeDocuments):
            async def list_documents(self, *args, **kwargs):
                raise RuntimeError("remote offline")

        fake = _FakeHindsight()
        fake.documents = BrokenDocuments()
        memory_backend.hindsight_client = lambda: fake

        with self.assertRaisesRegex(
            RuntimeError,
            "Hindsight session memory cleanup failed",
        ):
            _m_memory._HINDSIGHT_STALE_GUARD.purge(
                self.db,
                "chat",
                self.session["session_id"],
            )

        self.assertEqual(
            _m_session_naming.get_meta(self.db, key, ""),
            "5",
        )
        self.assertEqual(
            self.db.execute(
                "SELECT COUNT(*) FROM hindsight_documents "
                "WHERE chat_id=? AND session_id=?",
                ("chat", self.session["session_id"]),
            ).fetchone()[0],
            1,
        )

    def test_public_retain_rejects_stale_transcript_after_cutover(self):
        self._add_message()
        queued = []
        fake = _FakeHindsight()
        memory_backend.hindsight_client = lambda: fake

        with patch.object(
            _m_memory,
            "submit_background",
            side_effect=lambda name, fn, *args, **kwargs: (
                queued.append(
                    (name, fn, args, kwargs)
                )
            ),
        ):
            _m_sync_core.retain_session_memory(
                self.db,
                "chat",
                self.session,
                self.fields,
                provider_port=self.provider,
            )

        hindsight_jobs = [
            item
            for item in queued
            if item[0] == "hindsight_retain"
        ]
        self.assertEqual(len(hindsight_jobs), 1)

        self.db.execute(
            "UPDATE messages SET content='new text' "
            "WHERE chat_id=? AND session_id=?",
            ("chat", self.session["session_id"]),
        )
        self.db.commit()

        _name, fn, args, kwargs = hindsight_jobs[0]
        fn(*args, **kwargs)

        self.assertEqual(fake.retained, [])

    def test_public_purge_invalidates_already_queued_retain(self):
        self._add_message()
        queued = []
        fake = _FakeHindsight()
        memory_backend.hindsight_client = lambda: fake

        with patch.object(
            _m_memory,
            "submit_background",
            side_effect=lambda name, fn, *args, **kwargs: (
                queued.append(
                    (name, fn, args, kwargs)
                )
            ),
        ):
            _m_sync_core.retain_session_memory(
                self.db,
                "chat",
                self.session,
                self.fields,
                provider_port=self.provider,
            )

        hindsight_jobs = [
            item
            for item in queued
            if item[0] == "hindsight_retain"
        ]
        self.assertEqual(len(hindsight_jobs), 1)

        _m_main.purge_hindsight_session(
            self.db,
            "chat",
            self.session["session_id"],
        )

        _name, fn, args, kwargs = hindsight_jobs[0]
        fn(*args, **kwargs)

        self.assertEqual(fake.retained, [])

    def test_public_queued_retain_skips_deleted_session(self):
        self._add_message()
        queued = []
        fake = _FakeHindsight()
        memory_backend.hindsight_client = lambda: fake

        with patch.object(
            _m_memory,
            "submit_background",
            side_effect=lambda name, fn, *args, **kwargs: (
                queued.append(
                    (name, fn, args, kwargs)
                )
            ),
        ):
            _m_sync_core.retain_session_memory(
                self.db,
                "chat",
                self.session,
                self.fields,
                provider_port=self.provider,
            )

        hindsight_jobs = [
            item
            for item in queued
            if item[0] == "hindsight_retain"
        ]
        self.assertEqual(len(hindsight_jobs), 1)

        self.db.execute(
            "DELETE FROM sessions "
            "WHERE chat_id=? AND session_id=?",
            ("chat", self.session["session_id"]),
        )
        self.db.commit()

        _name, fn, args, kwargs = hindsight_jobs[0]
        fn(*args, **kwargs)

        self.assertEqual(fake.retained, [])

    def test_direct_guard_runs_post_retain_hook_when_memory_off(self):
        calls = []

        def hook(db, chat_id, session, fields, provider_port):
            calls.append(
                (
                    db,
                    chat_id,
                    session["session_id"],
                    fields["name"],
                    provider_port,
                )
            )

        with patch.dict(
            registry._POST_RETAIN_HOOKS,
            {"test": hook},
            clear=True,
        ):
            _m_session_naming.set_meta(
                self.db,
                "memory_mode:chat",
                "off",
            )
            _m_memory._HINDSIGHT_STALE_GUARD.retain(
                self.db,
                "chat",
                self.session,
                self.fields,
                provider_port=self.provider,
            )

        self.assertEqual(
            calls,
            [
                (
                    self.db,
                    "chat",
                    "memory-native",
                    "Mira",
                    self.provider,
                )
            ],
        )


class HindsightSourceBoundaryTests(unittest.TestCase):
    def test_state_integrity_no_longer_owns_hindsight_safety(self):
        path = (
            Path(__file__).parents[1]
            / "bridge"
            / "state_integrity.py"
        )
        self.assertFalse(path.exists())

    def test_hindsight_integrity_has_no_runtime_import(self):
        source = (
            Path(__file__).parents[1]
            / "bridge"
            / "hindsight_integrity.py"
        ).read_text(encoding="utf-8")

        self.assertNotIn(
            "import bridge.runtime",
            source,
        )
        self.assertNotIn(
            "from bridge.runtime",
            source,
        )


if __name__ == "__main__":
    unittest.main()
