from application_test_setup import ensure_application_extensions
from settings_test_support import SettingsTestCase

import bridge.response_delivery as _owner_response_delivery

ensure_application_extensions()

import unittest

import bridge.help_details as _m_help_details
import bridge.message_commands as _m_message_commands


class QuotedVoiceTests(SettingsTestCase):
    def test_extracts_only_double_quoted_dialogue(self):
        text = '*walks closer* "I am here." *smiles* "Are you ready?"'
        self.assertEqual(_owner_response_delivery.quoted_speech_from_reply(text), "I am here. Are you ready?")

    def test_narration_and_unquoted_text_are_not_spoken(self):
        self.assertEqual(_owner_response_delivery.quoted_speech_from_reply("*walks closer* I am here"), "")

    def test_unclosed_quote_is_not_spoken(self):
        self.assertEqual(_owner_response_delivery.quoted_speech_from_reply('"I am here'), "")

    def test_user_quote_is_queued_for_tts_when_voice_is_enabled(self):
        calls = []
        original_meta = _owner_response_delivery.get_meta
        original_submit = _owner_response_delivery.submit_background
        _owner_response_delivery.get_meta = lambda *_args: "tts"
        _owner_response_delivery.submit_background = lambda *args: calls.append(args) or True
        try:
            queued = _m_message_commands.queue_user_quote_tts(
                "token",
                "chat",
                '*waves* "Hello there."',
                object(),
                "session",
                44,
                app_settings=self.app_settings_builder.build(),
            )
        finally:
            _owner_response_delivery.get_meta = original_meta
            _owner_response_delivery.submit_background = original_submit
        self.assertTrue(queued)
        self.assertEqual(calls[0][0], "tts")
        self.assertEqual(calls[0][2:5], ("token", "chat", "Hello there."))

    def test_user_quote_is_not_queued_when_voice_is_disabled(self):
        calls = []
        original_meta = _owner_response_delivery.get_meta
        original_submit = _owner_response_delivery.submit_background
        _owner_response_delivery.get_meta = lambda *_args: "off"
        _owner_response_delivery.submit_background = lambda *args: calls.append(args) or True
        try:
            queued = _m_message_commands.queue_user_quote_tts(
                "token",
                "chat",
                '"Hello there."',
                object(),
                "session",
                44,
                app_settings=self.app_settings_builder.build(),
            )
        finally:
            _owner_response_delivery.get_meta = original_meta
            _owner_response_delivery.submit_background = original_submit
        self.assertFalse(queued)
        self.assertEqual(calls, [])

    def test_tts_command_is_not_in_help(self):
        commands = [command for entries in _m_help_details.HELP_CATEGORIES.values() for command, _summary in entries]
        self.assertNotIn("/tts", commands)

    def test_assistant_tts_uses_content_scoped_idempotency_key(self):
        class FakeDB:
            in_transaction = False

            def execute(self, *_args, **_kwargs):
                return None

            def commit(self):
                return None

        calls = []
        original_expression = _owner_response_delivery.deliver_expression
        original_send = _owner_response_delivery.send_text
        original_meta = _owner_response_delivery.get_meta
        original_submit = _owner_response_delivery.submit_background
        _owner_response_delivery.deliver_expression = lambda *_args, app_settings=None, **_kwargs: None
        _owner_response_delivery.send_text = lambda *_args, **_kwargs: [88]
        _owner_response_delivery.get_meta = lambda *_args: "tts"
        _owner_response_delivery.submit_background = lambda *args: calls.append(args) or True
        db = FakeDB()
        try:
            _m_message_commands.send_reply(
                "token", "chat", '"Hello there."', db, "session", 7, app_settings=self.app_settings_builder.build()
            )
            _m_message_commands.send_reply(
                "token", "chat", '"Hello there."', db, "session", 7, app_settings=self.app_settings_builder.build()
            )
            _m_message_commands.send_reply(
                "token", "chat", '"Changed reply."', db, "session", 7, app_settings=self.app_settings_builder.build()
            )
        finally:
            _owner_response_delivery.deliver_expression = original_expression
            _owner_response_delivery.send_text = original_send
            _owner_response_delivery.get_meta = original_meta
            _owner_response_delivery.submit_background = original_submit

        first_id = calls[0][-1]
        self.assertEqual(first_id, calls[1][-1])
        self.assertNotEqual(first_id, calls[2][-1])
        self.assertTrue(first_id.startswith("assistant-tts:chat:session:7:"))


if __name__ == "__main__":
    unittest.main()
