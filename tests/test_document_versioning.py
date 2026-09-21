from pathlib import Path
import tempfile
import unittest

import bridge.config as config
import bridge.rag_core as rag_core
from dependency_patch import dependency_module

_m_help = dependency_module("bridge.help")
_m_memory_curator = dependency_module("bridge.memory_curator")
_m_rag = dependency_module("bridge.rag")
_m_status_panels = dependency_module("bridge.status_panels")
_m_telegram = dependency_module("bridge.telegram")


class DocumentVersioningTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_db = config.DB_FILE
        config.DB_FILE = Path(self.tmp.name) / "bridge.sqlite3"
        self.db = _m_memory_curator.db_connect()
        self.old_batch = rag_core.embed_rag_batch
        self.old_cached = rag_core.cached_rag_embedding
        rag_core.embed_rag_batch = lambda texts: [None] * len(texts)
        rag_core.cached_rag_embedding = lambda _db, _text: None

    def tearDown(self):
        rag_core.embed_rag_batch = self.old_batch
        rag_core.cached_rag_embedding = self.old_cached
        self.db.close()
        config.DB_FILE = self.old_db
        self.tmp.cleanup()

    def test_same_filename_creates_new_active_version(self):
        status1, chunks1 = _m_telegram.add_data_bank_document(
            self.db, "chat", "notes.txt", b"alpha old version"
        )
        status2, chunks2 = _m_telegram.add_data_bank_document(
            self.db, "chat", "notes.txt", b"beta new version"
        )

        self.assertEqual(status1, "added")
        self.assertEqual(status2, "versioned")
        self.assertGreater(chunks1, 0)
        self.assertGreater(chunks2, 0)

        versions = _m_help.data_bank_document_versions(self.db, "chat", "notes.txt")
        self.assertEqual([row[1] for row in versions], [2, 1])
        self.assertEqual([row[2] for row in versions], [1, 0])

        active = _m_status_panels.data_bank_documents(self.db, "chat")
        self.assertEqual(len(active), 1)
        self.assertEqual(active[0][1], "notes.txt")

    def test_exact_content_reupload_stays_duplicate(self):
        payload = b"same exact document"
        self.assertEqual(
            _m_telegram.add_data_bank_document(self.db, "chat", "notes.txt", payload)[0],
            "added",
        )
        self.assertEqual(
            _m_telegram.add_data_bank_document(self.db, "chat", "renamed.txt", payload)[0],
            "duplicate",
        )
        count = self.db.execute(
            "SELECT COUNT(*) FROM data_bank_documents WHERE chat_id='chat'"
        ).fetchone()[0]
        self.assertEqual(count, 1)

    def test_retrieval_uses_only_active_version_and_rollback_is_atomic(self):
        _m_telegram.add_data_bank_document(
            self.db, "chat", "story.txt", b"ancient dragon sleeps beneath mountain"
        )
        _m_telegram.add_data_bank_document(
            self.db, "chat", "story.txt", b"modern spaceship waits above city"
        )

        current = _m_rag.retrieve_data_bank(self.db, "chat", "spaceship")
        old_hidden = _m_rag.retrieve_data_bank(self.db, "chat", "dragon")
        self.assertTrue(current)
        self.assertEqual(old_hidden, [])

        self.assertTrue(_m_help.activate_data_bank_version(self.db, "chat", "story.txt", 1))
        old_visible = _m_rag.retrieve_data_bank(self.db, "chat", "dragon")
        new_hidden = _m_rag.retrieve_data_bank(self.db, "chat", "spaceship")
        self.assertTrue(old_visible)
        self.assertEqual(new_hidden, [])

        active_count = self.db.execute(
            "SELECT COUNT(*) FROM data_bank_documents "
            "WHERE chat_id='chat' AND filename='story.txt' AND active=1"
        ).fetchone()[0]
        self.assertEqual(active_count, 1)

    def test_activate_unknown_version_does_not_change_active_version(self):
        _m_telegram.add_data_bank_document(self.db, "chat", "notes.txt", b"version one")
        _m_telegram.add_data_bank_document(self.db, "chat", "notes.txt", b"version two")
        self.assertFalse(_m_help.activate_data_bank_version(self.db, "chat", "notes.txt", 99))
        versions = _m_help.data_bank_document_versions(self.db, "chat", "notes.txt")
        active = [row[1] for row in versions if row[2]]
        self.assertEqual(active, [2])


if __name__ == "__main__":
    unittest.main()
