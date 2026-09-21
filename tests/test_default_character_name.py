from pathlib import Path
import unittest

import bridge.config as config
from runtime_test_facade import runtime as rt


class DefaultCharacterNameTests(unittest.TestCase):
    def test_fallback_name_follows_configured_default_card(self):
        original = config.DEFAULT_CHARACTER_FILE
        config.DEFAULT_CHARACTER_FILE = "Seraphina.png"
        try:
            fields = rt.card_fields({"data": {"name": ""}})
            self.assertEqual(fields["name"], "Seraphina")
        finally:
            config.DEFAULT_CHARACTER_FILE = original

    def test_fallback_name_defaults_to_default_card_stem(self):
        default_name = Path(config.DEFAULT_CHARACTER_FILE).stem.strip() or "Character"
        fields = rt.card_fields({"data": {}})
        self.assertEqual(fields["name"], default_name)

    def test_fallback_name_last_resort_for_empty_config(self):
        original = config.DEFAULT_CHARACTER_FILE
        config.DEFAULT_CHARACTER_FILE = ""
        try:
            fields = rt.card_fields({"data": {"name": ""}})
            self.assertEqual(fields["name"], "Character")
        finally:
            config.DEFAULT_CHARACTER_FILE = original

    def test_real_card_name_is_never_replaced(self):
        fields = rt.card_fields({"data": {"name": "Illia"}})
        self.assertEqual(fields["name"], "Illia")


if __name__ == "__main__":
    unittest.main()
