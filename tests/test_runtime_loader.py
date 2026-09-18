from pathlib import Path
import tempfile
import unittest

import bridge.runtime as rt
from bridge.runtime_loader import RuntimeStage, load_runtime_namespace


class RuntimeLoaderTests(unittest.TestCase):
    def test_runtime_exposes_structured_load_report(self):
        stages = {entry["stage"] for entry in rt.RUNTIME_LOAD_REPORT}
        self.assertEqual(
            stages,
            {
                "core",
                "recovery_overrides",
                "sync_extensions",
                "native_adapter_overrides",
                "identity_extensions",
                "safety_overrides",
            },
        )
        self.assertTrue(
            any(
                entry["public_callable_overrides"]
                for entry in rt.RUNTIME_LOAD_REPORT
                if entry["stage"] in {
                    "recovery_overrides",
                    "native_adapter_overrides",
                    "safety_overrides",
                }
            )
        )

    def test_core_stage_rejects_silent_public_callable_override(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "one.py").write_text("def action():\n    return 1\n", encoding="utf-8")
            (root / "two.py").write_text("def action():\n    return 2\n", encoding="utf-8")
            namespace = {"__name__": "test_runtime"}
            stages = (RuntimeStage("core", ("one.py", "two.py")),)
            with self.assertRaisesRegex(RuntimeError, "unexpectedly overrides"):
                load_runtime_namespace(namespace, root, stages)

    def test_override_stage_records_declared_replacement(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "one.py").write_text("def action():\n    return 1\n", encoding="utf-8")
            (root / "two.py").write_text("def action():\n    return 2\n", encoding="utf-8")
            namespace = {"__name__": "test_runtime"}
            stages = (
                RuntimeStage("core", ("one.py",)),
                RuntimeStage(
                    "hardening",
                    ("two.py",),
                    (("two.py", ("action",)),),
                ),
            )
            report = load_runtime_namespace(namespace, root, stages)
            self.assertEqual(report[-1]["public_callable_overrides"], ("action",))
            self.assertEqual(namespace["action"](), 2)


    def test_override_stage_rejects_non_allowlisted_symbol(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "one.py").write_text(
                "def action():\n    return 1\n\ndef extra():\n    return 1\n",
                encoding="utf-8",
            )
            (root / "two.py").write_text(
                "def action():\n    return 2\n\ndef extra():\n    return 2\n",
                encoding="utf-8",
            )
            namespace = {"__name__": "test_runtime"}
            stages = (
                RuntimeStage("core", ("one.py",)),
                RuntimeStage(
                    "hardening",
                    ("two.py",),
                    (("two.py", ("action",)),),
                ),
            )
            with self.assertRaisesRegex(RuntimeError, "extra"):
                load_runtime_namespace(namespace, root, stages)


if __name__ == "__main__":
    unittest.main()
