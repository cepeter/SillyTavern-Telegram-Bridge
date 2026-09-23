import ast
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
BRIDGE_DIR = REPO_ROOT / "bridge"


def _top_level_functions(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return {
        node.name
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


class MainDecompositionTests(unittest.TestCase):
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
        self.assertTrue(routing_path.is_file(), "update routing module must exist")

        expected = {
            "route_update",
            "complete_update",
            "is_long_running_command",
        }
        routing_functions = _top_level_functions(routing_path)
        main_functions = _top_level_functions(BRIDGE_DIR / "main.py")

        self.assertTrue(expected <= routing_functions)
        self.assertTrue(expected.isdisjoint(main_functions))


if __name__ == "__main__":
    unittest.main()
