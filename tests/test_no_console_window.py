"""
Prueba adversarial para FLK-01: verificación estática (AST) y unitaria de que
todas las invocaciones a subprocess en `src/` utilicen banderas anti-consola en Windows.
"""

import ast
import platform
import subprocess
from pathlib import Path
from unittest.mock import patch
import pytest

from src.proc_utils import no_window_kwargs, run_hidden, check_output_hidden, Popen_hidden, _CREATE_NO_WINDOW

WHITELIST = {
    # Inno Setup GUI installer launcher
    ("src/app/auto_updater.py", "Popen"): "Inno Setup GUI installer launcher must show its own GUI",
    # Internal proc_utils implementation
    ("src/proc_utils.py", "run"): "proc_utils internal",
    ("src/proc_utils.py", "check_output"): "proc_utils internal",
    ("src/proc_utils.py", "Popen"): "proc_utils internal",
}


def test_no_window_kwargs_on_windows():
    class MockStartupInfo:
        def __init__(self):
            self.dwFlags = 0
            self.wShowWindow = 1

    with patch("platform.system", return_value="Windows"), \
         patch.object(subprocess, "STARTUPINFO", MockStartupInfo, create=True), \
         patch.object(subprocess, "STARTF_USESHOWWINDOW", 1, create=True):
        kwargs = no_window_kwargs()
        assert "creationflags" in kwargs
        assert kwargs["creationflags"] & _CREATE_NO_WINDOW != 0
        assert "startupinfo" in kwargs
        assert kwargs["startupinfo"].wShowWindow == 0


def test_no_window_kwargs_on_non_windows():
    with patch("platform.system", return_value="Linux"):
        kwargs = no_window_kwargs()
        assert kwargs == {}


def test_all_subprocess_calls_use_hidden_flags():
    src_dir = Path(__file__).resolve().parent.parent / "src"
    violations = []

    for py_file in src_dir.rglob("*.py"):
        rel_path = py_file.relative_to(src_dir.parent).as_posix()
        tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))

        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue

            # Check for subprocess.<func>
            func_name = None
            if isinstance(node.func, ast.Attribute):
                if isinstance(node.func.value, ast.Name) and node.func.value.id == "subprocess":
                    func_name = node.func.attr

            if func_name in {"run", "Popen", "check_output", "check_call", "call"}:
                # Check if whitelisted
                if (rel_path, func_name) in WHITELIST:
                    continue

                # Check if keywords contain creationflags or no_window_kwargs
                has_flags = False
                for kw in node.keywords:
                    if kw.arg == "creationflags":
                        has_flags = True
                        break
                    if kw.arg is None and isinstance(kw.value, ast.Call):
                        # e.g. **no_window_kwargs()
                        if isinstance(kw.value.func, ast.Name) and "no_window_kwargs" in kw.value.func.id:
                            has_flags = True
                            break

                if not has_flags:
                    violations.append(f"{rel_path}:{node.lineno} -> subprocess.{func_name} sin no_window_kwargs ni creationflags")

    assert not violations, f"Se encontraron llamadas a subprocess sin proteccion anti-consola en Windows:\n" + "\n".join(violations)
