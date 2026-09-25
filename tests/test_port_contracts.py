"""Port shapes are compile-time contracts rather than argument-erasing callables."""

from __future__ import annotations

import ast
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]

GOOD = """
from typing import Any
from bridge.port_contracts import TelegramRequest, SendText, ProviderGenerate, DownloadFile

def request(token: str, method: str, payload: dict | None = None) -> Any:
    return {}
def text(token: str, chat_id: str, text: str) -> list[int]:
    return [1]
def download(token: str, file_id: str, max_bytes: int = 10) -> bytes:
    return b"fixture"
a: TelegramRequest = request
b: SendText = text
c: DownloadFile = download
b("token", "chat", "hello")
c("token", "file", max_bytes=10)
"""


def run_mypy(tmp_path: Path, source: str) -> subprocess.CompletedProcess[str]:
    path = tmp_path / "contract_sample.py"
    path.write_text(source)
    env = dict(os.environ, MYPYPATH=str(ROOT))
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "mypy",
            "--strict",
            "--follow-imports=silent",
            "--no-incremental",
            "--disable-error-code=type-arg",
            str(path),
        ],
        env=env,
        cwd=ROOT,
        capture_output=True,
        text=True,
    )


def test_correct_port_implementations_typecheck(tmp_path):
    result = run_mypy(tmp_path, GOOD)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize(
    "bad_call",
    [
        'b("token", "chat", textz="hello")',
        'b("token", "chat")',
        'b("token", "chat", "text", "extra")',
        'c("token", "file", max_bytes="not-an-integer")',
        'a("token", method=12)',
    ],
)
def test_wrong_call_shapes_are_type_errors(tmp_path, bad_call):
    result = run_mypy(tmp_path, GOOD + "\n" + bad_call + "\n")
    assert result.returncode != 0
    assert "import-not-found" not in result.stdout
    assert any(code in result.stdout for code in ("call-arg", "arg-type"))


def test_wrong_callback_signature_is_rejected(tmp_path):
    result = run_mypy(
        tmp_path,
        GOOD
        + """
def broken(token: str, chat_id: str) -> str:
    return 'bad'
bad: SendText = broken
""",
    )
    assert result.returncode != 0
    assert "[assignment]" in result.stdout


def test_core_ports_no_longer_erase_argument_signatures():
    for name in ("composition.py", "delivery_port.py", "provider_port.py", "conversation_service.py"):
        tree = ast.parse((ROOT / "bridge" / name).read_text())
        annotations = [node.annotation for node in ast.walk(tree) if isinstance(node, ast.AnnAssign)]
        assert not any("Callable[..." in ast.unparse(node) for node in annotations), name


def test_leaf_commands_do_not_receive_the_root_service_bag():
    tree = ast.parse((ROOT / "bridge/command_routes.py").read_text())
    names = {"_handle_basic", "_handle_memory_media", "_handle_entities", "_handle_chat", "_handle_panels"}
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name in names:
            args = {arg.arg for arg in (*node.args.args, *node.args.kwonlyargs)}
            assert "services" not in args, node.name
            assert not any(isinstance(n, ast.Name) and n.id == "services" for n in ast.walk(node)), node.name


def test_redundant_composition_factory_is_removed():
    tree = ast.parse((ROOT / "bridge/composition.py").read_text())
    assert "build_bridge_services" not in {n.name for n in tree.body if isinstance(n, ast.FunctionDef)}
