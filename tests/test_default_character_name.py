from pathlib import Path
import unittest

import bridge.runtime as rt


class DefaultCharacterNameTests(unittest.TestCase):
    def test_fallback_name_follows_configured_default_card(self):
        original = rt.DEFAULT_CHARACTER_FILE
        rt.DEFAULT_CHARACTER_FILE = "Seraphina.png"
        try:
            fields = rt.card_fields({"data": {"name": ""}})
            self.assertEqual(fields["name"], "Seraphina")
        finally:
            rt.DEFAULT_CHARACTER_FILE = original

    def test_fallback_name_defaults_to_default_card_stem(self):
        default_name = Path(rt.DEFAULT_CHARACTER_FILE).stem.strip() or "Character"
        fields = rt.card_fields({"data": {}})
        self.assertEqual(fields["name"], default_name)

    def test_fallback_name_last_resort_for_empty_config(self):
        original = rt.DEFAULT_CHARACTER_FILE
        rt.DEFAULT_CHARACTER_FILE = ""
        try:
            fields = rt.card_fields({"data": {"name": ""}})
            self.assertEqual(fields["name"], "Character")
        finally:
            rt.DEFAULT_CHARACTER_FILE = original

    def test_real_card_name_is_never_replaced(self):
        fields = rt.card_fields({"data": {"name": "Illia"}})
        self.assertEqual(fields["name"], "Illia")


if __name__ == "__main__":
    unittest.main()
