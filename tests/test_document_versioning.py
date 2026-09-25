from application_test_setup import ensure_application_extensions
from settings_test_support import SettingsTestCase

import bridge.rag_core as _owner_rag_core

ensure_application_extensions()

import tempfile
import unittest
from pathlib import Path

import bridge.memory_curator as _m_memory_curator
import bridge.rag as _m_rag
import bridge.rag_core as _m_telegram
import bridge.rag_core as rag_core


class DocumentVersioningTests(SettingsTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_db = self.app_settings_builder.db_file
        self.app_settings_builder.db_file = Path(self.tmp.name) / "bridge.sqlite3"
        self.db = _m_memory_curator.db_connect(app_settings=self.app_settings_builder.build())
        self.old_batch = rag_core.embed_rag_batch
        self.old_cached = rag_core.cached_rag_embedding
        rag_core.embed_rag_batch = lambda texts, *, app_settings=None: [None] * len(texts)
        rag_core.cached_rag_embedding = lambda _db, _text, *, app_settings=None: None

    def tearDown(self):
        rag_core.embed_rag_batch = self.old_batch
        rag_core.cached_rag_embedding = self.old_cached
        self.db.close()
        self.app_settings_builder.db_file = self.old_db
        self.tmp.cleanup()

    def test_same_filename_creates_new_active_version(self):
        status1, chunks1 = _m_telegram.add_data_bank_document(
            self.db, "chat", "notes.txt", b"alpha old version", app_settings=self.app_settings_builder.build()
        )
        status2, chunks2 = _m_telegram.add_data_bank_document(
            self.db, "chat", "notes.txt", b"beta new version", app_settings=self.app_settings_builder.build()
        )

        self.assertEqual(status1, "added")
        self.assertEqual(status2, "versioned")
        self.assertGreater(chunks1, 0)
        self.assertGreater(chunks2, 0)

        versions = _owner_rag_core.data_bank_document_versions(self.db, "chat", "notes.txt")
        self.assertEqual([row[1] for row in versions], [2, 1])
        self.assertEqual([row[2] for row in versions], [1, 0])

        active = _owner_rag_core.data_bank_documents(self.db, "chat")
        self.assertEqual(len(active), 1)
        self.assertEqual(active[0][1], "notes.txt")

    def test_exact_content_reupload_stays_duplicate(self):
        payload = b"same exact document"
        self.assertEqual(
            _m_telegram.add_data_bank_document(
                self.db, "chat", "notes.txt", payload, app_settings=self.app_settings_builder.build()
            )[0],
            "added",
        )
        self.assertEqual(
            _m_telegram.add_data_bank_document(
                self.db, "chat", "renamed.txt", payload, app_settings=self.app_settings_builder.build()
            )[0],
            "duplicate",
        )
        count = self.db.execute("SELECT COUNT(*) FROM data_bank_documents WHERE chat_id='chat'").fetchone()[0]
        self.assertEqual(count, 1)

    def test_retrieval_uses_only_active_version_and_rollback_is_atomic(self):
        _m_telegram.add_data_bank_document(
            self.db,
            "chat",
            "story.txt",
            b"ancient dragon sleeps beneath mountain",
            app_settings=self.app_settings_builder.build(),
        )
        _m_telegram.add_data_bank_document(
            self.db,
            "chat",
            "story.txt",
            b"modern spaceship waits above city",
            app_settings=self.app_settings_builder.build(),
        )

        current = _m_rag.retrieve_data_bank(
            self.db, "chat", "spaceship", app_settings=self.app_settings_builder.build()
        )
        old_hidden = _m_rag.retrieve_data_bank(
            self.db, "chat", "dragon", app_settings=self.app_settings_builder.build()
        )
        self.assertTrue(current)
        self.assertEqual(old_hidden, [])

        self.assertTrue(_owner_rag_core.activate_data_bank_version(self.db, "chat", "story.txt", 1))
        old_visible = _m_rag.retrieve_data_bank(
            self.db, "chat", "dragon", app_settings=self.app_settings_builder.build()
        )
        new_hidden = _m_rag.retrieve_data_bank(
            self.db, "chat", "spaceship", app_settings=self.app_settings_builder.build()
        )
        self.assertTrue(old_visible)
        self.assertEqual(new_hidden, [])

        active_count = self.db.execute(
            "SELECT COUNT(*) FROM data_bank_documents WHERE chat_id='chat' AND filename='story.txt' AND active=1"
        ).fetchone()[0]
        self.assertEqual(active_count, 1)

    def test_activate_unknown_version_does_not_change_active_version(self):
        _m_telegram.add_data_bank_document(
            self.db, "chat", "notes.txt", b"version one", app_settings=self.app_settings_builder.build()
        )
        _m_telegram.add_data_bank_document(
            self.db, "chat", "notes.txt", b"version two", app_settings=self.app_settings_builder.build()
        )
        self.assertFalse(_owner_rag_core.activate_data_bank_version(self.db, "chat", "notes.txt", 99))
        versions = _owner_rag_core.data_bank_document_versions(self.db, "chat", "notes.txt")
        active = [row[1] for row in versions if row[2]]
        self.assertEqual(active, [2])


if __name__ == "__main__":
    unittest.main()
