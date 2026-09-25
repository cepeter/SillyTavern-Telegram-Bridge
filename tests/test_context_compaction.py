import unittest

from settings_test_support import SettingsTestCase

from bridge.context_compaction import compact_chat_messages, estimate_message_tokens


class SmartContextCompactionTests(SettingsTestCase):
    def test_drops_oldest_history_before_touching_current_turn(self):
        messages = [{"role": "system", "content": "System rules."}]
        for index in range(20):
            role = "user" if index % 2 == 0 else "assistant"
            messages.append({"role": role, "content": f"OLD-{index} " + ("history " * 300)})
        messages.append(
            {
                "role": "user",
                "content": (
                    "<untrusted_memory>\n" + ("memory " * 400) + "\n</untrusted_memory>\n\n"
                    "CURRENT QUESTION MUST SURVIVE"
                    "\n\n<untrusted_data_bank_references>\n" + ("rag " * 500) + "\n</untrusted_data_bank_references>\n"
                ),
            }
        )

        compacted, stats = compact_chat_messages(
            messages, budget_tokens=3000, app_settings=self.app_settings_builder.build()
        )

        self.assertLessEqual(estimate_message_tokens(compacted), 3000)
        self.assertGreater(stats["dropped_history"], 0)
        self.assertNotIn("OLD-0", "\n".join(str(m["content"]) for m in compacted))
        self.assertIn("CURRENT QUESTION MUST SURVIVE", str(compacted[-1]["content"]))

    def test_trims_rag_memory_and_summary_when_history_is_not_enough(self):
        messages = [
            {
                "role": "system",
                "content": (
                    "Fixed character rules.\n\n"
                    "## Session continuity summary\n"
                    + ("summary " * 1200)
                    + "\n\n## Mandatory response language\nReply naturally."
                ),
            },
            {"role": "assistant", "content": "recent assistant turn"},
            {
                "role": "user",
                "content": (
                    "<untrusted_memory>\n" + ("memory " * 900) + "\n</untrusted_memory>\n"
                    "CURRENT"
                    "\n<untrusted_data_bank_references>\n" + ("rag " * 1000) + "\n</untrusted_data_bank_references>"
                ),
            },
        ]

        compacted, stats = compact_chat_messages(
            messages, budget_tokens=2200, app_settings=self.app_settings_builder.build()
        )

        self.assertLessEqual(estimate_message_tokens(compacted), 2200)
        self.assertTrue(stats["rag_trimmed"])
        self.assertTrue(stats["memory_trimmed"])
        self.assertTrue(stats["summary_trimmed"])
        self.assertIn("Fixed character rules.", compacted[0]["content"])
        self.assertIn("CURRENT", compacted[-1]["content"])

    def test_fixed_prompt_is_never_silently_truncated(self):
        system = "FIXED-RULE " + ("x" * 12000)
        current = "CURRENT " + ("y" * 1000)
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": current},
        ]

        compacted, stats = compact_chat_messages(
            messages, budget_tokens=2048, app_settings=self.app_settings_builder.build()
        )

        self.assertEqual(compacted[0]["content"], system)
        self.assertEqual(compacted[-1]["content"], current)
        self.assertTrue(stats["over_budget"])

    def test_image_data_uri_is_not_counted_as_base64_text(self):
        messages = [
            {"role": "system", "content": "system"},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "describe"},
                    {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + ("A" * 500000)}},
                ],
            },
        ]
        tokens = estimate_message_tokens(messages)
        self.assertLess(tokens, 2000)


if __name__ == "__main__":
    unittest.main()
