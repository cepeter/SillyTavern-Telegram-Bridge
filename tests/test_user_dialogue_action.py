import unittest

import bridge.runtime as rt


class UserDialogueActionTests(unittest.TestCase):
    def test_dialogue_and_action_are_labeled(self):
        result = rt.format_user_dialogue_action("I am coming *walking toward the door*")
        self.assertEqual(result, "User dialogue:\nI am coming\n\nUser action:\nwalking toward the door")

    def test_multiple_actions_are_combined(self):
        result = rt.format_user_dialogue_action("Hello *waves* there *smiles*")
        self.assertEqual(result, "User dialogue:\nHello there\n\nUser action:\nwaves smiles")

    def test_plain_text_and_double_stars_are_preserved(self):
        self.assertEqual(rt.format_user_dialogue_action("I am **ready**"), "I am **ready**")

    def test_builder_formats_current_and_historical_user_messages(self):
        session = {"persona_id": "", "system_prompt": "", "author_note": "", "world_file": "", "response_language": "auto"}
        fields = {"name": "Character", "description": "", "personality": "", "scenario": "", "first_mes": "", "mes_example": "", "system_prompt": "", "post_history_instructions": ""}
        messages = rt.build_chat_messages(session, fields, "I am coming *walking toward the door*", [("user", "*waits quietly* Ready?")])
        self.assertEqual(messages[-2]["content"], "User dialogue:\nReady?\n\nUser action:\nwaits quietly")
        self.assertEqual(messages[-1]["content"], "User dialogue:\nI am coming\n\nUser action:\nwalking toward the door")


if __name__ == "__main__":
    unittest.main()
