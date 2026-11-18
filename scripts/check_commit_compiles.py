#!/usr/bin/env python3
"""
Guardia de compilación de commits sobre el índice staged de git.

Vuelca el contenido actualmente preparado en el índice (`git checkout-index`) a un
directorio temporal aislado para validar lo que realmente va a entrar en el commit,
sin depender de archivos sin `git add` en el árbol de trabajo.

Comprobaciones realizadas sobre la instantánea staged:
1. Compilación de sintaxis (`python -m compileall -q <tmpdir>/src`) si existe `src/` con `.py`.
2. Importación básica de los puntos de entrada (`src.app.main`, `src.mcp.server`, `src.core.factory`)
   con `MCP2P_SKIP_GUI_BOOT=1` si los tres archivos existen en el índice staged.

Uso:
    python scripts/check_commit_compiles.py [--repo-root RUTA] [--install-hook]
"""

from __future__ import annotations

import argparse
import os
import shlex
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Sequence

DEFAULT_REPO_ROOT = Path(__file__).resolve().parent.parent
REQUIRED_ENTRYPOINTS: tuple[Path, ...] = (
    Path("src/app/main.py"),
    Path("src/mcp/server.py"),
    Path("src/core/factory.py"),
)
IMPORT_CHECK_STATEMENT = "import src.app.main, src.mcp.server, src.core.factory"


def _resolve_git_dir(repo_root: Path) -> Path | None:
    """Devuelve la ruta al directorio `.git` si `repo_root` es un repo git válido, o None."""
    if not repo_root.is_dir():
        return None
    proc = subprocess.run(
        ["git", "rev-parse", "--git-dir"],
        cwd=str(repo_root),
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        return None
    raw_git_dir = proc.stdout.strip()
    if not raw_git_dir:
        return None
    git_dir = Path(raw_git_dir)
    if not git_dir.is_absolute():
        git_dir = repo_root / git_dir
    return git_dir.resolve()


def _dump_staged_index(repo_root: Path, dest_dir: Path) -> tuple[bool, str]:
    """Vuelca el índice staged de git en `dest_dir` usando `--prefix` con barra final."""
    # `git checkout-index --prefix=...` exige separador final para escribir dentro del directorio.
    prefix = f"{dest_dir}{os.sep}"
    proc = subprocess.run(
        ["git", "checkout-index", "-a", "-f", f"--prefix={prefix}"],
        cwd=str(repo_root),
        capture_output=True,
        text=True,
    )
    detail = (proc.stderr or proc.stdout or "").strip()
    return proc.returncode == 0, detail


def _compile_staged_src(tmpdir_path: Path) -> tuple[bool, int]:
    """Ejecuta `compileall -q` sobre `<tmpdir>/src` si contiene archivos `.py`."""
    src_dir = tmpdir_path / "src"
    if not src_dir.is_dir():
        return True, 0
    py_files = list(src_dir.rglob("*.py"))
    if not py_files:
        return True, 0

    proc = subprocess.run(
        [sys.executable, "-m", "compileall", "-q", str(src_dir)],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        print(
            "ERROR: Fallo de compilación en archivos Python del índice staged:",
            file=sys.stderr,
        )
        if proc.stdout:
            print(proc.stdout.rstrip(), file=sys.stderr)
        if proc.stderr:
            print(proc.stderr.rstrip(), file=sys.stderr)
        return False, len(py_files)
    return True, len(py_files)


def _check_entrypoint_imports(tmpdir_path: Path) -> tuple[bool, bool]:
    """Verifica la importación de los tres módulos principales si existen en la copia staged."""
    if not all((tmpdir_path / rel_path).is_file() for rel_path in REQUIRED_ENTRYPOINTS):
        return True, False

    env = {
        **os.environ,
        "PYTHONPATH": str(tmpdir_path),
        "MCP2P_SKIP_GUI_BOOT": "1",
    }
    proc = subprocess.run(
        [sys.executable, "-c", IMPORT_CHECK_STATEMENT],
        cwd=str(tmpdir_path),
        env=env,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        print(
            "ERROR: Fallo al importar los módulos de arranque desde el índice staged:",
            file=sys.stderr,
        )
        if proc.stdout:
            print(proc.stdout.rstrip(), file=sys.stderr)
        if proc.stderr:
            print(proc.stderr.rstrip(), file=sys.stderr)
        return False, True
    return True, True


def check_staged_index(repo_root: Path) -> int:
    """Valida compilación e importaciones sobre el índice staged de `repo_root`."""
    resolved_root = Path(repo_root).resolve()
    if _resolve_git_dir(resolved_root) is None:
        print(
            f"ERROR: '{resolved_root}' no es un repositorio git válido.",
            file=sys.stderr,
        )
        return 1

    with tempfile.TemporaryDirectory(prefix="mcp2p_commit_guard_") as tmpdir:
        tmpdir_path = Path(tmpdir)
        ok_dump, detail = _dump_staged_index(resolved_root, tmpdir_path)
        if not ok_dump:
            msg = f"ERROR: No se pudo volcar el índice staged de '{resolved_root}'."
            if detail:
                msg = f"{msg} Detalle: {detail}"
            print(msg, file=sys.stderr)
            return 1

        ok_compile, py_count = _compile_staged_src(tmpdir_path)
        if not ok_compile:
            return 1

        ok_imports, checked_imports = _check_entrypoint_imports(tmpdir_path)
        if not ok_imports:
            return 1

    if py_count == 0 and not checked_imports:
        print("OK: índice staged válido (sin archivos .py en src/).")
    elif checked_imports:
        print(
            f"OK: índice staged válido ({py_count} archivo(s) .py compilado(s) "
            "e importación de arranque correcta)."
        )
    else:
        print(f"OK: índice staged válido ({py_count} archivo(s) .py compilado(s)).")
    return 0


def install_pre_commit_hook(repo_root: Path) -> int:
    """Instala `.git/hooks/pre-commit` en `repo_root` ejecutando este script."""
    resolved_root = Path(repo_root).resolve()
    git_dir = _resolve_git_dir(resolved_root)
    if git_dir is None:
        print(
            f"ERROR: No se puede instalar el hook; '{resolved_root}' no es un repositorio git válido.",
            file=sys.stderr,
        )
        return 1

    hook_path = git_dir / "hooks" / "pre-commit"
    hook_path.parent.mkdir(parents=True, exist_ok=True)

    python_cmd = shlex.quote(Path(sys.executable).as_posix() if os.name != "posix" else sys.executable)
    script_path = shlex.quote(Path(__file__).resolve().as_posix())
    root_arg = shlex.quote(resolved_root.as_posix())

    hook_content = (
        "#!/bin/sh\n"
        "set -eu\n"
        f"exec {python_cmd} {script_path} --repo-root {root_arg}\n"
    )
    hook_path.write_text(hook_content, encoding="utf-8", newline="\n")
    if os.name == "posix":
        os.chmod(hook_path, 0o755)

    print(f"OK: hook pre-commit instalado en '{hook_path}'.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    """Construye el analizador de argumentos de línea de comandos."""
    parser = argparse.ArgumentParser(
        description="Verifica que los archivos Python en el índice staged de git compilen e importen."
    )
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=DEFAULT_REPO_ROOT,
        help="Ruta a la raíz del repositorio git (por defecto la raíz de este proyecto).",
    )
    parser.add_argument(
        "--install-hook",
        action="store_true",
        help="Instala el hook ejecutable .git/hooks/pre-commit en el repositorio indicado.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Punto de entrada CLI."""
    args = build_parser().parse_args(argv)
    if args.install_hook:
        return install_pre_commit_hook(args.repo_root)
    return check_staged_index(args.repo_root)


if __name__ == "__main__":
    sys.exit(main())
