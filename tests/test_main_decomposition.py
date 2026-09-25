import ast
import unittest
from pathlib import Path

from settings_test_support import SettingsTestCase

REPO_ROOT = Path(__file__).resolve().parents[1]
BRIDGE_DIR = REPO_ROOT / "bridge"


def _top_level_functions(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return {node.name for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}


class MainDecompositionTests(SettingsTestCase):
    def test_worker_recovery_orchestration_has_focused_owner(self):
        worker_path = BRIDGE_DIR / "worker_orchestration.py"
        self.assertTrue(worker_path.is_file(), "worker orchestration module must exist")

        expected = {
            "process_message_job",
            "process_image_job",
            "process_callback_job",
            "native_edit_committed_after_failure",
            "process_edit_job",
            "resolve_recovered_job_submission",
            "make_durable_backlog_dispatcher",
        }
        worker_functions = _top_level_functions(worker_path)
        main_functions = _top_level_functions(BRIDGE_DIR / "main.py")

        self.assertTrue(expected <= worker_functions)
        self.assertTrue(expected.isdisjoint(main_functions))

    def test_update_routing_has_focused_owner(self):
        routing_path = BRIDGE_DIR / "update_routing.py"
        callback_path = BRIDGE_DIR / "update_callback_routing.py"
        message_path = BRIDGE_DIR / "update_message_routing.py"
        self.assertTrue(routing_path.is_file(), "update routing module must exist")
        self.assertTrue(callback_path.is_file(), "callback routing module must exist")
        self.assertTrue(message_path.is_file(), "message routing module must exist")

        routing_functions = _top_level_functions(routing_path)
        callback_functions = _top_level_functions(callback_path)
        message_functions = _top_level_functions(message_path)
        main_functions = _top_level_functions(BRIDGE_DIR / "main.py")

        self.assertTrue({"route_update", "complete_update"} <= routing_functions)
        self.assertNotIn("is_long_running_command", routing_functions)
        self.assertIn("route_callback_update", callback_functions)
        self.assertTrue(
            {
                "route_edited_message_update",
                "route_message_update",
                "is_long_running_command",
            }
            <= message_functions
        )
        self.assertTrue(
            {
                "route_update",
                "complete_update",
                "route_callback_update",
                "route_edited_message_update",
                "route_message_update",
                "is_long_running_command",
            }.isdisjoint(main_functions)
        )

    def test_runtime_lifecycle_has_focused_owner(self):
        lifecycle_path = BRIDGE_DIR / "runtime_lifecycle.py"
        self.assertTrue(lifecycle_path.is_file(), "runtime lifecycle module must exist")

        expected = {
            "run_bridge_runtime",
            "request_bridge_shutdown",
            "install_bridge_signal_handlers",
            "restore_poll_offset",
        }
        lifecycle_functions = _top_level_functions(lifecycle_path)
        main_functions = _top_level_functions(BRIDGE_DIR / "main.py")

        self.assertTrue(expected <= lifecycle_functions)
        self.assertTrue(expected.isdisjoint(main_functions))

        main_source = (BRIDGE_DIR / "main.py").read_text(encoding="utf-8")
        main_chunk = main_source[main_source.index("def _main()") :]
        self.assertIn("run_bridge_runtime(", main_chunk)
        self.assertNotIn("getUpdates", main_chunk)
        self.assertNotIn("services.jobs.recover(", main_chunk)
        self.assertNotIn("start_live_sync_worker(", main_chunk)
        self.assertNotIn("shutdown_background_executors(", main_chunk)

    def test_main_passes_loaded_card_fields_to_runtime(self):
        main_path = BRIDGE_DIR / "main.py"
        tree = ast.parse(main_path.read_text(encoding="utf-8"))
        calls = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "run_bridge_runtime"
        ]

        self.assertEqual(len(calls), 1)
        self.assertEqual(len(calls[0].args), 2)
        self.assertIsInstance(calls[0].args[1], ast.Name)
        self.assertEqual(calls[0].args[1].id, "fields")


if __name__ == "__main__":
    unittest.main()
