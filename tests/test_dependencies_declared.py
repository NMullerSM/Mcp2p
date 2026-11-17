"""Gate 0d (EGM-23/40): todo módulo de terceros que `src/` importa directamente debe estar
declarado en `pyproject.toml` (dependencias principales u opcionales, con o sin marcador de
plataforma). Un import que hoy solo llega como transitiva o desde los paquetes del sistema
rompe en cuanto el entorno se crea limpio.
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path
from typing import Dict, Iterable, Set

import pytest

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
PYPROJECT = ROOT / "pyproject.toml"

# Nombre de import -> distribución, para los casos donde no coinciden. Es estático a propósito:
# el test no debe depender de qué paquetes estén instalados (p. ej. `System` solo existe en
# Windows vía pythonnet).
IMPORT_TO_DIST: Dict[str, str] = {
    "webview": "pywebview",
    "yaml": "PyYAML",
    "System": "pythonnet",
    "clr": "pythonnet",
    "nacl": "PyNaCl",
    "PIL": "pillow",
    "winpty": "pywinpty",
}

OWN_TOP_LEVEL = {"src"}


def _normalize(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _imported_top_level(source: str) -> Set[str]:
    names: Set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            names.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names.add(node.module.split(".")[0])
    return names


def _third_party(names: Iterable[str]) -> Set[str]:
    stdlib = set(sys.stdlib_module_names) | {"__future__"}
    return {n for n in names if n not in stdlib and n not in OWN_TOP_LEVEL}


def _dist_for(module: str) -> str:
    if module in IMPORT_TO_DIST:
        return IMPORT_TO_DIST[module]
    try:
        from importlib.metadata import packages_distributions

        dists = [d for d in packages_distributions().get(module, []) if _normalize(d) != "mcp2p"]
        if dists:
            return dists[0]
    except Exception:
        pass
    return module


def _declared_distributions() -> Set[str]:
    try:
        import tomllib  # type: ignore[import-not-found]
    except ModuleNotFoundError:  # pragma: no cover - Python 3.10
        tomllib = pytest.importorskip("tomli")
    data = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    project = data["project"]
    reqs = list(project.get("dependencies", []))
    for extra in project.get("optional-dependencies", {}).values():
        reqs.extend(extra)
    declared: Set[str] = set()
    for req in reqs:
        match = re.match(r"\s*([A-Za-z0-9][A-Za-z0-9._-]*)", req)
        if match:
            declared.add(_normalize(match.group(1)))
    return declared


def _src_third_party_imports() -> Dict[str, Set[str]]:
    usage: Dict[str, Set[str]] = {}
    for path in sorted(SRC.rglob("*.py")):
        for name in _third_party(_imported_top_level(path.read_text(encoding="utf-8"))):
            usage.setdefault(name, set()).add(str(path.relative_to(ROOT)))
    return usage


def test_every_third_party_import_in_src_is_declared():
    declared = _declared_distributions()
    missing = {
        module: sorted(files)
        for module, files in _src_third_party_imports().items()
        if _normalize(_dist_for(module)) not in declared
    }
    assert not missing, (
        "Imports de terceros en src/ sin declarar en pyproject.toml "
        f"(módulo -> archivos): {missing}"
    )


def test_mcp_is_pinned_to_the_2x_api():
    try:
        import tomllib  # type: ignore[import-not-found]
    except ModuleNotFoundError:  # pragma: no cover
        tomllib = pytest.importorskip("tomli")
    deps = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))["project"]["dependencies"]
    mcp_reqs = [d for d in deps if re.match(r"\s*mcp\s*[<>=!~]", d)]
    assert mcp_reqs == ["mcp>=2.3,<3"]


def test_scanner_detects_an_undeclared_import():
    """El escáner no es vacuo: ve imports anidados en funciones/try y descarta stdlib y propios."""
    source = (
        "import os\n"
        "from src.storage import db\n"
        "def f():\n"
        "    try:\n"
        "        import paquete_inexistente_xyz.sub\n"
        "    except ImportError:\n"
        "        from yaml import safe_load\n"
    )
    found = _third_party(_imported_top_level(source))
    assert found == {"paquete_inexistente_xyz", "yaml"}
    assert _normalize(_dist_for("paquete_inexistente_xyz")) not in _declared_distributions()
    assert _normalize(_dist_for("yaml")) in _declared_distributions()
