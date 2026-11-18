"""Tests para la guardia de compilación de commits (`scripts/check_commit_compiles.py`)."""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = REPO_ROOT / "scripts" / "check_commit_compiles.py"
VERSIONED_HOOK_PATH = REPO_ROOT / ".githooks" / "pre-commit"


def _run_git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    """Ejecuta un comando git dentro de un repositorio temporal."""
    return subprocess.run(
        ["git", *args],
        cwd=str(repo),
        capture_output=True,
        text=True,
    )


def _init_temp_git_repo(repo: Path) -> Path:
    """Inicializa un repositorio git temporal con identidad mínima de usuario."""
    repo.mkdir(parents=True, exist_ok=True)
    init = _run_git(repo, "init")
    assert init.returncode == 0, init.stderr
    assert _run_git(repo, "config", "user.name", "Test Commit Guard").returncode == 0
    assert _run_git(repo, "config", "user.email", "commit-guard@example.invalid").returncode == 0
    return repo


def _run_guard(
    repo_root: Path,
    *extra_args: str,
    env_overrides: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Ejecuta `scripts/check_commit_compiles.py` como subproceso CLI."""
    env = dict(os.environ)
    if env_overrides:
        env.update(env_overrides)
    return subprocess.run(
        [sys.executable, str(SCRIPT_PATH), "--repo-root", str(repo_root), *extra_args],
        capture_output=True,
        text=True,
        env=env,
    )


def test_real_repository_staged_index_passes() -> None:
    """Ejecutado sobre el repositorio real debe devolver código de salida 0."""
    proc = _run_guard(REPO_ROOT)
    assert proc.returncode == 0, f"stdout={proc.stdout}\nstderr={proc.stderr}"
    assert "OK:" in proc.stdout


def test_non_git_directory_fails_cleanly(tmp_path: Path) -> None:
    """Si `--repo-root` no es un repositorio git debe fallar con código 1."""
    not_a_repo = tmp_path / "sin_git"
    not_a_repo.mkdir()
    proc = _run_guard(not_a_repo)
    assert proc.returncode == 1
    assert "no es un repositorio git" in proc.stderr


def test_staged_indentation_error_returns_1_and_identifies_file(tmp_path: Path) -> None:
    """Un archivo con IndentationError en el índice staged debe fallar identificando `bad.py`."""
    repo = _init_temp_git_repo(tmp_path / "repo_bad")
    bad_file = repo / "src" / "bad.py"
    bad_file.parent.mkdir(parents=True, exist_ok=True)
    bad_file.write_text("def rota():\nreturn 1\n", encoding="utf-8")

    assert _run_git(repo, "add", "src/bad.py").returncode == 0

    proc = _run_guard(repo)
    combined = f"{proc.stdout}\n{proc.stderr}"
    assert proc.returncode == 1
    assert "bad.py" in combined
    assert "IndentationError" in combined


def test_valid_staged_index_ignores_unstaged_indentation_error(tmp_path: Path) -> None:
    """Errores sin `git add` en el árbol de trabajo no deben romper un índice staged válido."""
    repo = _init_temp_git_repo(tmp_path / "repo_staged_vs_worktree")
    ok_file = repo / "src" / "ok.py"
    ok_file.parent.mkdir(parents=True, exist_ok=True)
    ok_file.write_text("def valida() -> int:\n    return 42\n", encoding="utf-8")
    assert _run_git(repo, "add", "src/ok.py").returncode == 0

    # Corrompe el archivo en el árbol de trabajo sin hacer `git add` y crea otro archivo roto sin rastrear.
    ok_file.write_text("def rota_sin_stage():\nreturn 0\n", encoding="utf-8")
    (repo / "src" / "unstaged_bad.py").write_text("if True:\nprint('x')\n", encoding="utf-8")

    proc = _run_guard(repo)
    assert proc.returncode == 0, f"stdout={proc.stdout}\nstderr={proc.stderr}"
    assert "OK:" in proc.stdout


def test_staged_broken_entrypoint_import_fails_with_traceback(tmp_path: Path) -> None:
    """Si existen los 3 entrypoints en el índice staged y uno falla al importar, devuelve 1."""
    repo = _init_temp_git_repo(tmp_path / "repo_broken_import")
    files = {
        "src/__init__.py": "",
        "src/app/__init__.py": "",
        "src/app/main.py": "VALUE = 1\n",
        "src/mcp/__init__.py": "",
        "src/mcp/server.py": "SERVER_READY = True\n",
        "src/core/__init__.py": "",
        "src/core/factory.py": "import modulo_inexistente_commit_guard\n",
    }
    for rel_path, content in files.items():
        target = repo / rel_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")

    assert _run_git(repo, "add", "src").returncode == 0

    proc = _run_guard(repo)
    combined = f"{proc.stdout}\n{proc.stderr}"
    assert proc.returncode == 1
    assert "Traceback" in combined
    assert "modulo_inexistente_commit_guard" in combined


def test_install_hook_creates_executable_hook_and_blocks_bad_commit(tmp_path: Path) -> None:
    """`--install-hook` crea `.git/hooks/pre-commit` ejecutable y bloquea commits rotos."""
    repo = _init_temp_git_repo(tmp_path / "repo_hook")

    install_proc = _run_guard(repo, "--install-hook")
    assert install_proc.returncode == 0, f"stdout={install_proc.stdout}\nstderr={install_proc.stderr}"

    hook_path = repo / ".git" / "hooks" / "pre-commit"
    assert hook_path.is_file()
    assert b"\r\n" not in hook_path.read_bytes()
    if os.name == "posix":
        assert os.access(hook_path, os.X_OK)

    bad_file = repo / "src" / "bad.py"
    bad_file.parent.mkdir(parents=True, exist_ok=True)
    bad_file.write_text("def fallo_hook():\nreturn 123\n", encoding="utf-8")
    assert _run_git(repo, "add", "src/bad.py").returncode == 0

    blocked_commit = _run_git(repo, "commit", "-m", "commit con IndentationError")
    combined = f"{blocked_commit.stdout}\n{blocked_commit.stderr}"
    assert blocked_commit.returncode != 0
    assert "bad.py" in combined
    assert _run_git(repo, "rev-parse", "--verify", "HEAD").returncode != 0

    # Al corregir el archivo y volver a hacer `git add`, el hook permite el commit.
    bad_file.write_text("def corregido() -> int:\n    return 123\n", encoding="utf-8")
    assert _run_git(repo, "add", "src/bad.py").returncode == 0

    valid_commit = _run_git(repo, "commit", "-m", "commit valido")
    assert valid_commit.returncode == 0, f"stdout={valid_commit.stdout}\nstderr={valid_commit.stderr}"
    assert _run_git(repo, "rev-parse", "--verify", "HEAD").returncode == 0


def test_real_versioned_pre_commit_hook_format_and_permissions() -> None:
    """`.githooks/pre-commit` debe existir en el repo real, usar LF puro y ser ejecutable en POSIX."""
    assert VERSIONED_HOOK_PATH.is_file()
    raw = VERSIONED_HOOK_PATH.read_bytes()
    assert b"\r\n" not in raw
    assert raw.startswith(b"#!/bin/sh\n")
    if os.name == "posix":
        mode = VERSIONED_HOOK_PATH.stat().st_mode
        assert mode & stat.S_IXUSR
        assert os.access(VERSIONED_HOOK_PATH, os.X_OK)


def test_versioned_githooks_blocks_bad_commit_and_allows_valid_commit_fast(tmp_path: Path) -> None:
    """Con `core.hooksPath=.githooks`, `git commit` rechaza IndentationError y acepta índice válido en <10s."""
    repo = _init_temp_git_repo(tmp_path / "repo_versioned_hook")

    dst_script = repo / "scripts" / "check_commit_compiles.py"
    dst_script.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(SCRIPT_PATH, dst_script)

    dst_hook = repo / ".githooks" / "pre-commit"
    dst_hook.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(VERSIONED_HOOK_PATH, dst_hook)
    if os.name == "posix":
        os.chmod(dst_hook, 0o755)

    cfg = _run_git(repo, "config", "core.hooksPath", ".githooks")
    assert cfg.returncode == 0, cfg.stderr

    bad_file = repo / "src" / "bad.py"
    bad_file.parent.mkdir(parents=True, exist_ok=True)
    bad_file.write_text("def fallo_versioned_hook():\nreturn 123\n", encoding="utf-8")
    assert _run_git(repo, "add", "src/bad.py").returncode == 0

    blocked_commit = _run_git(repo, "commit", "-m", "commit roto con githooks")
    combined = f"{blocked_commit.stdout}\n{blocked_commit.stderr}"
    assert blocked_commit.returncode != 0
    assert "bad.py" in combined
    assert "IndentationError" in combined
    assert _run_git(repo, "rev-parse", "--verify", "HEAD").returncode != 0

    bad_file.write_text("def corregido() -> int:\n    return 123\n", encoding="utf-8")
    assert _run_git(repo, "add", "src/bad.py").returncode == 0

    t0 = time.monotonic()
    valid_commit = _run_git(repo, "commit", "-m", "commit valido con githooks")
    elapsed = time.monotonic() - t0
    assert valid_commit.returncode == 0, f"stdout={valid_commit.stdout}\nstderr={valid_commit.stderr}"
    assert elapsed < 10.0, f"git commit tardó demasiado: {elapsed:.2f}s"
    assert _run_git(repo, "rev-parse", "--verify", "HEAD").returncode == 0


def test_install_hook_activates_versioned_githooks_when_present(tmp_path: Path) -> None:
    """`--install-hook` activa `core.hooksPath=.githooks`, fija 0o755 y conserva respaldo en `.git/hooks`."""
    repo = _init_temp_git_repo(tmp_path / "repo_install_versioned")

    dst_script = repo / "scripts" / "check_commit_compiles.py"
    dst_script.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(SCRIPT_PATH, dst_script)

    dst_hook = repo / ".githooks" / "pre-commit"
    dst_hook.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(VERSIONED_HOOK_PATH, dst_hook)
    if os.name == "posix":
        os.chmod(dst_hook, 0o644)

    install_proc = _run_guard(repo, "--install-hook")
    assert install_proc.returncode == 0, f"stdout={install_proc.stdout}\nstderr={install_proc.stderr}"

    hooks_path_cfg = _run_git(repo, "config", "--get", "core.hooksPath")
    assert hooks_path_cfg.returncode == 0
    assert hooks_path_cfg.stdout.strip() == ".githooks"

    if os.name == "posix":
        assert (dst_hook.stat().st_mode & 0o777) == 0o755
        assert os.access(dst_hook, os.X_OK)

    assert (repo / ".git" / "hooks" / "pre-commit").is_file()
