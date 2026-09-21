"""Phase 7B4 application/UI ordinary-import boundary tests."""

from __future__ import annotations

import ast
import builtins
from pathlib import Path
import subprocess
import symtable
import sys
import unittest

from bridge.ordinary_dependencies import declared_dependencies_for


REPO_ROOT = Path(__file__).parents[1]
BRIDGE_DIR = REPO_ROOT / "bridge"

MIGRATED_RUNTIME_FILES = (
    "common.py",
    "cards.py",
    "memory.py",
    "rag.py",
    "groups.py",
    "telegram.py",
    "persona_delete_panel.py",
    "language.py",
    "greetings.py",
    "help_details.py",
    "help.py",
    "input_flows.py",
    "catalog.py",
    "update.py",
    "image_generation.py",
    "expressions.py",
    "media.py",
    "generation.py",
    "commands.py",
    "status_panels.py",
    "command_routes.py",
    "message_commands.py",
    "callbacks.py",
    "panel_callback_routes.py",
    "sync_core.py",
    "sync_api.py",
    "persona_sync.py",
    "character_identity.py",
    "session_naming.py",
    "scene_state.py",
    "director_goals.py",
    "memory_curator.py",
)


def _module_bound_names(source: str) -> set[str]:
    tree = ast.parse(source)
    names: set[str] = set()

    def bind_target(node):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, (ast.Tuple, ast.List)):
            for child in node.elts:
                bind_target(child)

    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.asname or alias.name.split(".", 1)[0])
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.name == "*":
                    continue
                names.add(alias.asname or alias.name)
        elif isinstance(node, (ast.Assign, ast.AnnAssign, ast.NamedExpr)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                bind_target(target)
        elif isinstance(node, (ast.For, ast.AsyncFor)):
            bind_target(node.target)
    return names


def _referenced_globals(source: str, filename: str) -> set[str]:
    table = symtable.symtable(source, filename, "exec")
    result: set[str] = set()

    def walk(node):
        for symbol in node.get_symbols():
            if symbol.is_referenced() and symbol.is_global():
                result.add(symbol.get_name())
        for child in node.get_children():
            walk(child)

    walk(table)
    return result


def _module_defined_names(source: str) -> set[str]:
    tree = ast.parse(source)
    names: set[str] = set()

    def bind_target(node):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, (ast.Tuple, ast.List)):
            for child in node.elts:
                bind_target(child)

    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, (ast.Assign, ast.AnnAssign, ast.NamedExpr)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                bind_target(target)
    return names


def _owner_index() -> dict[str, list[str]]:
    owners: dict[str, list[str]] = {}
    for path in sorted(BRIDGE_DIR.glob("*.py")):
        source = path.read_text(encoding="utf-8")
        for name in _module_defined_names(source):
            owners.setdefault(name, []).append(path.name)
    return owners


class Phase7B4ApplicationImportBoundaryTests(unittest.TestCase):
    def _run_python(self, source: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-c", source],
            cwd=REPO_ROOT,
            text=True,
            capture_output=True,
            check=False,
        )

    def test_migrated_modules_import_without_runtime(self):
        for filename in MIGRATED_RUNTIME_FILES:
            module = "bridge." + filename.removesuffix(".py")
            with self.subTest(module=module):
                completed = self._run_python(
                    "import sys\n"
                    f"import {module}\n"
                    "assert 'bridge.runtime' not in sys.modules\n"
                )
                self.assertEqual(
                    completed.returncode,
                    0,
                    completed.stdout + completed.stderr,
                )

    def test_migrated_modules_have_no_implicit_shared_runtime_globals(self):
        owners = _owner_index()
        builtin_names = set(dir(builtins))
        failures = []
        for filename in MIGRATED_RUNTIME_FILES:
            source = (BRIDGE_DIR / filename).read_text(encoding="utf-8")
            bound = _module_bound_names(source)
            declared = set(
                declared_dependencies_for(
                    "bridge." + filename.removesuffix(".py")
                )
            )
            unresolved = sorted(
                _referenced_globals(source, filename)
                - bound
                - declared
                - builtin_names
                - {"__file__", "__name__", "__package__"}
            )
            if unresolved:
                detail = []
                for name in unresolved:
                    candidates = owners.get(name, [])
                    detail.append(
                        name
                        + (" <- " + ",".join(candidates) if candidates else "")
                    )
                failures.append(filename + ": " + "; ".join(detail))
        self.assertEqual(failures, [], "\n".join(failures))

    def test_declared_dependencies_never_point_to_runtime_or_main(self):
        for module_name in (
            "bridge." + filename.removesuffix(".py")
            for filename in MIGRATED_RUNTIME_FILES
        ):
            for name, (source_module, _attribute) in declared_dependencies_for(module_name).items():
                with self.subTest(module=module_name, name=name):
                    self.assertNotIn(
                        source_module,
                        {"bridge.runtime", "bridge.main"},
                    )

    def test_migrated_modules_do_not_import_runtime(self):
        for filename in MIGRATED_RUNTIME_FILES:
            source = (BRIDGE_DIR / filename).read_text(encoding="utf-8")
            with self.subTest(filename=filename):
                self.assertNotIn("import bridge.runtime", source)
                self.assertNotIn("from bridge.runtime import", source)

    def test_phase_7b4_loader_contains_only_main(self):
        from bridge.runtime_loader import DEFAULT_RUNTIME_STAGES

        loaded = tuple(
            module
            for stage in DEFAULT_RUNTIME_STAGES
            for module in stage.modules
        )
        self.assertEqual(loaded, ("main.py",))

    def test_cards_is_ordinary_and_runtime_identity_is_canonical(self):
        import bridge.cards as cards
        import bridge.runtime as rt

        for name in (
            "get_persona",
            "default_persona_id",
            "persona_name",
            "send_panel_message",
            "send_persona_menu",
            "send_character_menu",
            "send_character_info_menu",
            "send_character_delete_menu",
            "send_character_delete_confirm",
            "send_session_menu",
        ):
            with self.subTest(name=name):
                self.assertIs(getattr(rt, name), getattr(cards, name))

    def test_generation_command_callback_owners_are_ordinary(self):
        import bridge.callbacks as callbacks
        import bridge.command_routes as command_routes
        import bridge.generation as generation
        import bridge.message_commands as message_commands
        import bridge.panel_callback_routes as panel_callback_routes
        import bridge.runtime as rt

        for module, names in (
            (generation, ("generate_text", "build_chat_messages", "regenerate_last", "continue_last")),
            (command_routes, ("handle_command_route",)),
            (message_commands, ("process_message", "generate_and_store_reply")),
            (callbacks, ("process_callback",)),
            (panel_callback_routes, ("handle_primary_panel_callback",)),
        ):
            for name in names:
                with self.subTest(module=module.__name__, name=name):
                    self.assertIs(getattr(rt, name), getattr(module, name))


    def test_runtime_patch_compatibility_mirrors_to_ordinary_owner(self):
        import bridge.runtime as rt
        import bridge.telegram as telegram

        original = rt.send_text
        sentinel = lambda *_args, **_kwargs: []
        try:
            rt.send_text = sentinel
            self.assertIs(telegram.send_text, sentinel)
            self.assertIs(rt.send_text, sentinel)
        finally:
            rt.send_text = original

    def test_runtime_reads_mutable_state_from_live_ordinary_owner(self):
        import bridge.common as common
        import bridge.runtime as rt

        original = common._BACKGROUND_ACCEPTING
        try:
            common._BACKGROUND_ACCEPTING = not original
            self.assertEqual(
                rt._BACKGROUND_ACCEPTING,
                common._BACKGROUND_ACCEPTING,
            )
        finally:
            common._BACKGROUND_ACCEPTING = original

    def test_extension_modules_expose_explicit_registration(self):
        import bridge.director_goals as director_goals
        import bridge.memory_curator as memory_curator
        import bridge.scene_state as scene_state

        self.assertTrue(callable(scene_state.register_scene_state_extensions))
        self.assertTrue(callable(director_goals.register_director_goal_extensions))
        self.assertTrue(callable(memory_curator.register_memory_curator_extensions))


if __name__ == "__main__":
    unittest.main()
