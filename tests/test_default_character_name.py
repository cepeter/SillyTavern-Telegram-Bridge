from application_test_setup import ensure_application_extensions
from settings_test_support import SettingsTestCase

ensure_application_extensions()

import unittest
from pathlib import Path

import bridge.main as _m_main


class DefaultCharacterNameTests(SettingsTestCase):
    def test_fallback_name_follows_configured_default_card(self):
        original = self.app_settings_builder.default_character_file
        self.app_settings_builder.default_character_file = "Seraphina.png"
        try:
            fields = _m_main.card_fields({"data": {"name": ""}}, app_settings=self.app_settings_builder.build())
            self.assertEqual(fields["name"], "Seraphina")
        finally:
            self.app_settings_builder.default_character_file = original

    def test_fallback_name_defaults_to_default_card_stem(self):
        default_name = Path(self.app_settings_builder.default_character_file).stem.strip() or "Character"
        fields = _m_main.card_fields({"data": {}}, app_settings=self.app_settings_builder.build())
        self.assertEqual(fields["name"], default_name)

    def test_fallback_name_last_resort_for_empty_config(self):
        original = self.app_settings_builder.default_character_file
        self.app_settings_builder.default_character_file = ""
        try:
            fields = _m_main.card_fields({"data": {"name": ""}}, app_settings=self.app_settings_builder.build())
            self.assertEqual(fields["name"], "Character")
        finally:
            self.app_settings_builder.default_character_file = original

    def test_real_card_name_is_never_replaced(self):
        fields = _m_main.card_fields({"data": {"name": "Illia"}}, app_settings=self.app_settings_builder.build())
        self.assertEqual(fields["name"], "Illia")


if __name__ == "__main__":
    unittest.main()
