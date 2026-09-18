from pathlib import Path
import tempfile
import unittest

import bridge.runtime as rt


class RagScalingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.original_db = rt.DB_FILE
        rt.DB_FILE = Path(self.tmp.name) / "bridge.sqlite3"
        with rt._DB_SCHEMA_LOCK:
            rt._DB_SCHEMA_READY = False
        self.db = rt.db_connect()
        self.namespace = rt.rag_embedding_namespace()

    def tearDown(self):
        self.db.close()
        rt.DB_FILE = self.original_db
        self.tmp.cleanup()

    def _insert_chunks(self, count: int, needle_index: int | None = None):
        now = rt.time.time()
        document_id = "doc-large"
        self.db.execute(
            "INSERT INTO data_bank_documents(chat_id,document_id,filename,byte_size,chunk_count,created_at,updated_at) "
            "VALUES(?,?,?,?,?,?,?)",
            ("chat", document_id, "large.txt", count, count, now, now),
        )
        ids = []
        for index in range(count):
            content = f"chunk {index}"
            if index == needle_index:
                content += " needle"
            cursor = self.db.execute(
                "INSERT INTO data_bank_chunks(chat_id,document_id,chunk_index,content) VALUES(?,?,?,?)",
                ("chat", document_id, index, content),
            )
            chunk_id = int(cursor.lastrowid)
            ids.append(chunk_id)
            self.db.execute(
                "INSERT INTO data_bank_embeddings(chunk_id,embedding_namespace,dimensions,vector_json) VALUES(?,?,?,?)",
                (chunk_id, self.namespace, 2, "[1.0,0.0]"),
            )
            self.db.execute(
                "INSERT INTO data_bank_fts(content,chat_id,document_id,filename,chunk_id) VALUES(?,?,?,?,?)",
                (content, "chat", document_id, "large.txt", chunk_id),
            )
        self.db.commit()
        return ids

    def test_small_corpus_keeps_exact_semantic_candidate_set(self):
        ids = self._insert_chunks(20)
        candidates = rt.semantic_candidate_chunk_ids(
            self.db, "chat", self.namespace, [], candidate_limit=64
        )
        self.assertEqual(candidates, tuple(ids))

    def test_large_corpus_is_bounded_and_keeps_lexical_neighborhood(self):
        ids = self._insert_chunks(500)
        hit = ids[250]
        candidates = rt.semantic_candidate_chunk_ids(
            self.db, "chat", self.namespace, [hit], candidate_limit=64
        )
        self.assertLessEqual(len(candidates), 64)
        for expected in ids[248:253]:
            self.assertIn(expected, candidates)

    def test_retrieve_decodes_only_bounded_semantic_shortlist(self):
        self._insert_chunks(300, needle_index=150)
        original_cached = rt.cached_rag_embedding
        original_limit = rt.rag_semantic_candidate_limit
        original_cosine = rt.cosine_similarity
        cosine_calls = []
        rt.cached_rag_embedding = lambda _db, _query: [1.0, 0.0]
        rt.rag_semantic_candidate_limit = lambda: 32

        def counted_cosine(left, right):
            cosine_calls.append((left, right))
            return original_cosine(left, right)

        rt.cosine_similarity = counted_cosine
        try:
            results = rt.retrieve_data_bank(self.db, "chat", "needle", limit=5)
        finally:
            rt.cached_rag_embedding = original_cached
            rt.rag_semantic_candidate_limit = original_limit
            rt.cosine_similarity = original_cosine

        self.assertTrue(results)
        self.assertGreater(len(cosine_calls), 0)
        self.assertLessEqual(len(cosine_calls), 32)


if __name__ == "__main__":
    unittest.main()
