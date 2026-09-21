import unittest

from runtime_test_facade import runtime as rt


class QuotedVoiceTests(unittest.TestCase):
    def test_extracts_only_double_quoted_dialogue(self):
        text = '*walks closer* "I am here." *smiles* "Are you ready?"'
        self.assertEqual(rt.quoted_speech_from_reply(text), "I am here. Are you ready?")

    def test_narration_and_unquoted_text_are_not_spoken(self):
        self.assertEqual(rt.quoted_speech_from_reply("*walks closer* I am here"), "")

    def test_unclosed_quote_is_not_spoken(self):
        self.assertEqual(rt.quoted_speech_from_reply('"I am here'), "")

    def test_user_quote_is_queued_for_tts_when_voice_is_enabled(self):
        calls = []
        original_meta = rt.get_meta
        original_submit = rt.submit_background
        rt.get_meta = lambda *_args: "tts"
        rt.submit_background = lambda *args: calls.append(args) or True
        try:
            queued = rt.queue_user_quote_tts("token", "chat", '*waves* "Hello there."', object(), "session", 44)
        finally:
            rt.get_meta = original_meta
            rt.submit_background = original_submit
        self.assertTrue(queued)
        self.assertEqual(calls[0][0], "tts")
        self.assertEqual(calls[0][2:5], ("token", "chat", "Hello there."))

    def test_user_quote_is_not_queued_when_voice_is_disabled(self):
        calls = []
        original_meta = rt.get_meta
        original_submit = rt.submit_background
        rt.get_meta = lambda *_args: "off"
        rt.submit_background = lambda *args: calls.append(args) or True
        try:
            queued = rt.queue_user_quote_tts("token", "chat", '"Hello there."', object(), "session", 44)
        finally:
            rt.get_meta = original_meta
            rt.submit_background = original_submit
        self.assertFalse(queued)
        self.assertEqual(calls, [])

    def test_tts_command_is_not_in_help(self):
        commands = [command for entries in rt.HELP_CATEGORIES.values() for command, _summary in entries]
        self.assertNotIn("/tts", commands)


    def test_assistant_tts_uses_content_scoped_idempotency_key(self):
        class FakeDB:
            def execute(self, *_args, **_kwargs):
                return None

            def commit(self):
                return None

        calls = []
        original_expression = rt.deliver_expression
        original_send = rt.send_text
        original_meta = rt.get_meta
        original_submit = rt.submit_background
        rt.deliver_expression = lambda *_args, **_kwargs: None
        rt.send_text = lambda *_args, **_kwargs: [88]
        rt.get_meta = lambda *_args: "tts"
        rt.submit_background = lambda *args: calls.append(args) or True
        db = FakeDB()
        try:
            rt.send_reply("token", "chat", '"Hello there."', db, "session", 7)
            rt.send_reply("token", "chat", '"Hello there."', db, "session", 7)
            rt.send_reply("token", "chat", '"Changed reply."', db, "session", 7)
        finally:
            rt.deliver_expression = original_expression
            rt.send_text = original_send
            rt.get_meta = original_meta
            rt.submit_background = original_submit

        first_id = calls[0][-1]
        self.assertEqual(first_id, calls[1][-1])
        self.assertNotEqual(first_id, calls[2][-1])
        self.assertTrue(first_id.startswith("assistant-tts:chat:session:7:"))


if __name__ == "__main__":
    unittest.main()
