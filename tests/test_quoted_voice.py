import unittest

import bridge.runtime as rt


class QuotedVoiceTests(unittest.TestCase):
    def test_extracts_only_double_quoted_dialogue(self):
        text = '*walks closer* "I am here." *smiles* "Are you ready?"'
        self.assertEqual(rt.quoted_speech_from_reply(text), "I am here. Are you ready?")

    def test_narration_and_unquoted_text_are_not_spoken(self):
        self.assertEqual(rt.quoted_speech_from_reply("*walks closer* I am here"), "")

    def test_unclosed_quote_is_not_spoken(self):
        self.assertEqual(rt.quoted_speech_from_reply('"I am here'), "")

    def test_tts_command_is_not_in_help(self):
        commands = [command for entries in rt.HELP_CATEGORIES.values() for command, _summary in entries]
        self.assertNotIn("/tts", commands)


if __name__ == "__main__":
    unittest.main()
