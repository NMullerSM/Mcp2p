"""Propiedades del parser OSC 7501, invariantes AST y validacion de catalogo OSC 7501."""

from __future__ import annotations

import ast
import json
import random
from pathlib import Path
from typing import List, Optional, Tuple

import pytest

from src.adapters.terminal.cli_catalog import (
    CatalogValidationError,
    CliSpec,
    load_catalog,
    load_repo_catalog,
)
from src.adapters.terminal.pty_common import (
    OSC_7501_MAX_PENDING,
    OSC_7501_PREFIX,
    OSC_7501_PROBE_REPLY,
    Osc7501Parser,
)

REPO_ROOT = Path(__file__).resolve().parent.parent


def _run_stream(chunks: List[bytes]) -> Tuple[List[str], Optional[str], int, bool, bool]:
    parser = Osc7501Parser()
    statuses: List[str] = []
    probes = 0
    for chunk in chunks:
        statuses.extend(parser.feed(chunk))
        probes += parser.take_probes()
    return statuses, parser.last_kind, probes, parser.cleared, parser.trailing_output


def _random_chunks(data: bytes, seed: int) -> List[bytes]:
    rng = random.Random(seed)
    chunks: List[bytes] = []
    idx = 0
    while idx < len(data):
        step = rng.randint(1, 13)
        chunks.append(data[idx : idx + step])
        idx += step
    return chunks


@pytest.mark.parametrize(
    "stream",
    [
        (
            b"inicio \x1b[31mrojo\x1b[0m\r\n"
            b"\x1b]7501;state=working\x07"
            b"\x1b[?25h"
            b"\x1b]7501;state=blocked:kind=permission\x1b\\"
            b"\x1b]7501;state=idle\x07"
            b"\x1b]7501;?\x07"
            b"   \r\n\x1b[38;5;196m\x1b[0m"
        ),
        (
            b"prefijo \x1b[1;34mazul\x1b[0m"
            b"\x1b]7501;state=working\x07"
            b"\x1b]7501;state=blocked:kind=permission\x07"
            b"\x1b]7501;state=idle\x07"
            b"\x1b]7501;state=clear\x07"
            b"\x1b]7501;?\x07"
            + "café 🚀\n".encode("utf-8")
            + b"\xff\xfe\x80\x1b[0m"
        ),
        (
            b"\x1b]7501;state=clear:id=abc\x07"
            b"\x1b]7501;state=idle\x07"
            b"\x1b[32m  \r\n\x1b[0m"
            b"\x1b]7501;?\x1b\\"
            b"visible\x1b[0m"
        ),
    ],
)
def test_osc7501_parser_fragmentation_invariant(stream: bytes) -> None:
    expected = _run_stream([stream])
    byte_by_byte = _run_stream([stream[i : i + 1] for i in range(len(stream))])
    assert byte_by_byte == expected

    for seed in (0, 1, 7, 42, 99, 2026):
        chunked = _run_stream(_random_chunks(stream, seed))
        assert chunked == expected


def test_osc7501_parser_fuzz_and_buffer_bound() -> None:
    max_len = OSC_7501_MAX_PENDING + len(OSC_7501_PREFIX)
    adversarial_inputs = [
        b"\x1b]7501;" + b"A" * 5000,
        b"\x1b]7501;" + b"B" * 4500 + b"\x07\x1b]7501;idle\x07",
        b"\x1b]7501;" * 1000,
        b"\x1b[" + b"0" * 5000,
        b"\x1b]7501;\xff\xfe\x80\x07\xff\xfe\x80",
    ]
    for payload in adversarial_inputs:
        parser = Osc7501Parser()
        for chunk in [payload[i : i + 17] for i in range(0, len(payload), 17)]:
            parser.feed(chunk)
            assert len(parser._buf) <= max_len

    for seed in (10, 20, 30, 40):
        rng = random.Random(seed)
        raw = bytes(rng.randrange(256) for _ in range(4096))
        parser = Osc7501Parser()
        for chunk in _random_chunks(raw, seed + 1000):
            parser.feed(chunk)
            assert len(parser._buf) <= max_len


def test_osc7501_parser_clear_and_reactivation() -> None:
    parser = Osc7501Parser()
    assert parser.feed(b"\x1b]7501;state=blocked:kind=permission\x07") == ["blocked"]
    assert parser.last_kind == "permission"
    assert parser.cleared is False

    assert parser.feed(b"\x1b]7501;clear\x07") == []
    assert parser.cleared is True
    assert parser.last_kind is None

    assert parser.feed(b"\x1b]7501;state=clear:id=42\x07\x1b]7501;working\x07") == ["working"]
    assert parser.cleared is False
    assert parser.last_kind is None


def test_ast_os_write_only_in_allowed_functions() -> None:
    allowed_methods = {
        ("src/adapters/terminal/pty_host.py", "PosixPtyHostConnector", "send_input"),
        ("src/adapters/terminal/pty_host.py", "PosixPtyHostConnector", "_write_osc_probe_reply"),
    }
    allowed_files = {"src/mcp/operator_proxy.py"}
    violations: List[str] = []

    for py_file in sorted((REPO_ROOT / "src").rglob("*.py")):
        rel = py_file.relative_to(REPO_ROOT).as_posix()
        tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=rel)

        imported_write_names = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module == "os":
                for alias in node.names:
                    if alias.name == "write":
                        imported_write_names.add(alias.asname or alias.name)

        class Visitor(ast.NodeVisitor):
            def __init__(self) -> None:
                self.class_stack: List[Optional[str]] = []
                self.func_stack: List[Optional[str]] = []

            def visit_ClassDef(self, node: ast.ClassDef) -> None:
                self.class_stack.append(node.name)
                self.generic_visit(node)
                self.class_stack.pop()

            def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
                self.func_stack.append(node.name)
                self.generic_visit(node)
                self.func_stack.pop()

            def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
                self.func_stack.append(node.name)
                self.generic_visit(node)
                self.func_stack.pop()

            def visit_Call(self, node: ast.Call) -> None:
                is_os_write = (
                    isinstance(node.func, ast.Attribute)
                    and node.func.attr == "write"
                    and isinstance(node.func.value, ast.Name)
                    and node.func.value.id == "os"
                ) or (
                    isinstance(node.func, ast.Name)
                    and node.func.id in imported_write_names
                )
                if is_os_write and rel not in allowed_files:
                    cls_name = self.class_stack[-1] if self.class_stack else None
                    fn_name = self.func_stack[-1] if self.func_stack else None
                    if (rel, cls_name, fn_name) not in allowed_methods:
                        violations.append(f"{rel}:{node.lineno} en {cls_name}.{fn_name}")
                self.generic_visit(node)

        Visitor().visit(tree)

    assert violations == []


def test_ast_non_terminal_layers_never_reference_osc_status_or_kind() -> None:
    forbidden = {"osc_status", "osc_kind"}
    roots = ("src/core", "src/mcp", "src/p2p", "src/storage")
    violations: List[str] = []

    for root_rel in roots:
        root_dir = REPO_ROOT / root_rel
        if not root_dir.exists():
            continue
        for py_file in sorted(root_dir.rglob("*.py")):
            rel = py_file.relative_to(REPO_ROOT).as_posix()
            tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=rel)
            for node in ast.walk(tree):
                if isinstance(node, ast.Name) and node.id in forbidden:
                    violations.append(f"{rel}:{node.lineno} Name({node.id})")
                elif isinstance(node, ast.Attribute) and node.attr in forbidden:
                    violations.append(f"{rel}:{node.lineno} Attribute({node.attr})")
                elif isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value in forbidden:
                    violations.append(f"{rel}:{node.lineno} Constant({node.value})")
                elif isinstance(node, ast.arg) and node.arg in forbidden:
                    violations.append(f"{rel}:{node.lineno} arg({node.arg})")

    assert violations == []


def test_cli_catalog_osc7501_verification_and_user_isolation(tmp_path: Path) -> None:
    assert OSC_7501_PROBE_REPLY == b"\x1b]7501;?1\x07"
    repo_cat = load_repo_catalog()
    claude = repo_cat.get("claude")
    assert claude is not None
    assert claude.osc7501["status"] == "verified"
    assert claude.osc7501_probe_reply_bytes() == b"\x1b]7501;?1\x07"

    for profile_id in ("gemini", "codex", "opencode", "antigravity", "custom_shell"):
        spec = repo_cat.get(profile_id)
        assert spec is not None
        assert spec.osc7501["status"] == "unverified"
        assert spec.osc7501_probe_reply_bytes() is None

    # Un catalogo de usuario nunca puede autodeclararse verificado para OSC 7501.
    user_path = tmp_path / "cli_catalog.user.json"
    user_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "clis": [
                    {
                        "id": "myagent",
                        "display_name": "My Agent",
                        "icon": "myagent",
                        "binary": "myagent",
                        "osc7501": {
                            "status": "verified",
                            "probe_reply_payload": "?1",
                            "verified_with_cli_version": "1.0.0",
                            "verified_on": "2026-11-18",
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    combined = load_catalog(user_path=user_path)
    myagent = combined.get("myagent")
    assert myagent is not None
    assert myagent.osc7501["status"] == "unverified"
    assert myagent.osc7501["probe_reply_payload"] == ""
    assert myagent.osc7501_probe_reply_bytes() is None

    # Una entrada de repo con status="verified" sin metadatos completos o payload invalido falla validacion.
    bad_repo = tmp_path / "bad_repo.json"
    bad_repo.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "catalog_version": "1.0",
                "clis": [
                    {
                        "id": "badcli",
                        "display_name": "Bad CLI",
                        "icon": "badcli",
                        "binary": "badcli",
                        "osc7501": {
                            "status": "verified",
                            "probe_reply_payload": "1",  # debe empezar por '?'
                            "verified_with_cli_version": "1.0.0",
                            "verified_on": "2026-11-18",
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(CatalogValidationError):
        load_repo_catalog(bad_repo)

    # Un CliSpec directo de kind="shell" nunca devuelve respuesta de sonda.
    shell_spec = CliSpec(
        id="sh",
        display_name="Shell",
        icon="shell",
        kind="shell",
        binary="sh",
        binary_aliases=(),
        candidate_paths={},
        launch_flags=(),
        mcp={"method": "none"},
        resume={"argv": []},
        install={},
        source="repo",
        osc7501={"status": "verified", "probe_reply_payload": "?1"},
    )
    assert shell_spec.osc7501_probe_reply_bytes() is None
