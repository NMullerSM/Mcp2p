"""D12-ter: el sistema solo escribe en un PTY por los caminos declarados (vigilado por AST).

Caminos permitidos:
  1. teclas del humano: `ExtensionsLabUIAdapter.pty_write_input`;
  2. entrega directa: `TerminalPaneInput.write` (solo la usa `DirectDeliveryService`);
  3. medición de CLIs (WP-5d): `HeadlessCliProbe._send`, solo sobre una terminal de PRUEBA que abre su propio conector
     privado (nunca las terminales del usuario) y solo la importa `measurement_ui`;
  4. respuesta a la sonda OSC 7501: `PosixPtyHostConnector._write_osc_probe_reply` (la vigila otra prueba).
Añadir un camino de escritura cambia la invariante: se discute con el dueño, no se «arregla» esta prueba.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Iterator, List, Optional, Tuple

REPO_ROOT = Path(__file__).resolve().parents[1]

ALLOWED_SEND_INPUT_CALLS = {
    ("src/app/ui_adapters/extensions_lab_ui.py", "ExtensionsLabUIAdapter", "pty_write_input"),
    ("src/adapters/terminal/direct_delivery_writer.py", "TerminalPaneInput", "write"),
    ("src/adapters/terminal/cli_measurement.py", "HeadlessCliProbe", "_send"),
}
PROBE_MODULE = "src.adapters.terminal.cli_measurement"
PROBE_IMPORTERS = {"src/app/ui_adapters/measurement_ui.py"}
WRITER_MODULE = "src.adapters.terminal.direct_delivery_writer"
WRITER_IMPORTERS = {"src/core/factory.py", "src/app/ui_adapters/delivery_ui.py"}


def _walk(source: str, rel: str) -> Iterator[Tuple[ast.AST, Optional[str], Optional[str]]]:
    tree = ast.parse(source, filename=rel)
    classes: List[Optional[str]] = []
    funcs: List[Optional[str]] = []

    def visit(node: ast.AST) -> Iterator[Tuple[ast.AST, Optional[str], Optional[str]]]:
        if isinstance(node, ast.ClassDef):
            classes.append(node.name)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            funcs.append(node.name)
        yield node, (classes[-1] if classes else None), (funcs[-1] if funcs else None)
        for child in ast.iter_child_nodes(node):
            yield from visit(child)
        if isinstance(node, ast.ClassDef):
            classes.pop()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            funcs.pop()

    yield from visit(tree)


def send_input_violations(source: str, rel: str) -> List[str]:
    out = []
    for node, cls, fn in _walk(source, rel):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "send_input":
            if (rel, cls, fn) not in ALLOWED_SEND_INPUT_CALLS:
                out.append(f"{rel}:{node.lineno} en {cls}.{fn}")
    return out


def _sources(subdir: str) -> Iterator[Tuple[str, str]]:
    for path in sorted((REPO_ROOT / subdir).rglob("*.py")):
        yield path.relative_to(REPO_ROOT).as_posix(), path.read_text(encoding="utf-8")


def _imports(source: str, rel: str) -> List[str]:
    names: List[str] = []
    for node in ast.walk(ast.parse(source, filename=rel)):
        if isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            names.append(base)
            names.extend(f"{base}.{alias.name}" for alias in node.names)
    return names


def test_send_input_is_only_called_from_the_two_declared_paths() -> None:
    violations: List[str] = []
    for rel, source in _sources("src"):
        violations.extend(send_input_violations(source, rel))
    assert violations == []


def test_the_detector_catches_a_synthetic_writer() -> None:
    bad = "class Sneaky:\n    def go(self, term):\n        term.send_input('pane_1', 'rm -rf /')\n"
    assert send_input_violations(bad, "src/core/services/sneaky.py") == ["src/core/services/sneaky.py:3 en Sneaky.go"]
    good = "class TerminalPaneInput:\n    def write(self, c):\n        c.send_input('p', 'x')\n"
    assert send_input_violations(good, "src/adapters/terminal/direct_delivery_writer.py") == []


def test_direct_delivery_writer_is_only_imported_by_the_composition_root_and_delivery_ui() -> None:
    importers = set()
    for rel, source in _sources("src"):
        if any(name == WRITER_MODULE or name.startswith(WRITER_MODULE + ".") for name in _imports(source, rel)):
            importers.add(rel)
    assert importers <= WRITER_IMPORTERS and "src/app/ui_adapters/delivery_ui.py" in importers, importers


def test_mcp_package_never_imports_direct_delivery() -> None:
    offenders = []
    for rel, source in _sources("src/mcp"):
        for name in _imports(source, rel):
            if "direct_delivery" in name or name.endswith("core.ports.delivery"):
                offenders.append(f"{rel}: {name}")
    assert offenders == []


def test_direct_delivery_service_is_hexagonal() -> None:
    rel = "src/core/services/direct_delivery_service.py"
    source = (REPO_ROOT / rel).read_text(encoding="utf-8")
    bad = [n for n in _imports(source, rel) if n.startswith("src.adapters") or n.startswith("src.app")]
    assert bad == []


def test_conpty_write_only_inside_send_input() -> None:
    rel = "src/adapters/terminal/winpty_host.py"
    source = (REPO_ROOT / rel).read_text(encoding="utf-8")
    bad = []
    for node, cls, fn in _walk(source, rel):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "write"
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "pty"
            and fn != "send_input"
        ):
            bad.append(f"{rel}:{node.lineno} en {cls}.{fn}")
    assert bad == []


def test_measurement_probe_is_only_imported_by_its_ui_adapter() -> None:
    importers = set()
    for rel, source in _sources("src"):
        if rel == "src/adapters/terminal/cli_measurement.py":
            continue
        if any(name == PROBE_MODULE or name.startswith(PROBE_MODULE + ".") for name in _imports(source, rel)):
            importers.add(rel)
    assert importers == PROBE_IMPORTERS


def test_measurement_probe_never_touches_the_users_panes() -> None:
    """La sonda abre SU conector (un solo panel) y no recibe el conector ni el registro de paneles del usuario."""
    rel = "src/adapters/terminal/cli_measurement.py"
    source = (REPO_ROOT / rel).read_text(encoding="utf-8")
    for forbidden in ("terminal_connector", "_get_terminal_connector", "PaneRegistry", "get_pane_registry"):
        assert forbidden not in source
