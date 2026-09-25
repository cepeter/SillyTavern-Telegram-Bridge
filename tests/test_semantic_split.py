from application_test_setup import ensure_application_extensions
from settings_test_support import SettingsTestCase

ensure_application_extensions()

import unittest

import bridge.telegram as _m_telegram


class SemanticTelegramSplitTests(SettingsTestCase):
    def test_prefers_paragraph_boundary(self):
        text = "First paragraph.\n\nSecond paragraph.\n\nThird paragraph."
        chunks = _m_telegram.split_telegram_text(text, limit=24)
        self.assertEqual(chunks, ["First paragraph.\n\n", "Second paragraph.\n\n", "Third paragraph."])

    def test_falls_back_to_sentence_then_space(self):
        text = "One sentence. Two sentence. Three sentence."
        chunks = _m_telegram.split_telegram_text(text, limit=24)
        self.assertTrue(all(len(chunk.encode("utf-16-le")) // 2 <= 24 for chunk in chunks))
        self.assertEqual("".join(chunks), text)
        self.assertTrue(chunks[0].rstrip().endswith((".", "!", "?")))

    def test_preserves_unicode_utf16_limit(self):
        text = "😀 " * 20
        chunks = _m_telegram.split_telegram_text(text, limit=10)
        self.assertTrue(all(len(chunk.encode("utf-16-le")) // 2 <= 10 for chunk in chunks))
        self.assertEqual("".join(chunks), text)

    def test_code_fence_prefers_boundary_outside_fence(self):
        text = "Intro.\n\n```\ncode line\n```\n\nOutro."
        chunks = _m_telegram.split_telegram_text(text, limit=22)
        self.assertEqual("".join(chunks), text)
        self.assertTrue(any(chunk.endswith("```\n\n") for chunk in chunks[:-1]))

    def test_invalid_limit_is_rejected(self):
        with self.assertRaises(ValueError):
            _m_telegram.split_telegram_text("text", 0)


if __name__ == "__main__":
    unittest.main()
