"""Andamiaje del doble repositorio git de un proyecto nuevo (antes en `DatabaseManager.create_project`).

Implementa `GitRepositoryScaffoldPort`. La secuencia de comandos y los textos de los commits son
los mismos que tenía `db.py`; los fallos de git se registran como *warning* y no interrumpen la
creación del proyecto.
"""

from __future__ import annotations

import logging
import platform
import subprocess
from pathlib import Path
from typing import List

logger = logging.getLogger("mcp2p.adapters.provisioning.git")


def _run_git_logged(args: List[str], cwd: Path, creationflags: int = 0) -> subprocess.CompletedProcess:
    """Ejecuta git y registra un warning con stderr si el código de salida no es 0 (no lanza)."""
    res = subprocess.run(args, cwd=str(cwd), capture_output=True, creationflags=creationflags)
    if res.returncode != 0:
        err = (res.stderr or b"").decode("utf-8", errors="replace").strip()
        logger.warning("git %s falló en %s (código %s): %s", " ".join(args[1:3]), cwd, res.returncode, err[:300])
    return res


class SubprocessGitRepoScaffolder:
    """Inicializa repo de código, repo de gobernanza con rama huérfana en worktree, y hooks."""

    def scaffold_project_repositories(
        self,
        *,
        name: str,
        project_uuid: str,
        clean_name: str,
        app_dir: Path,
        gov_dir: Path,
    ) -> None:
        p_uuid = project_uuid
        # Configurar flags de subprocess para evitar CMD flashing en Windows
        creationflags = subprocess.CREATE_NO_WINDOW if platform.system() == "Windows" else 0

        # 1. Inicializar repo de código si no existe .git
        if not (app_dir / ".git").exists():
            _run_git_logged(["git", "init", "-b", "main"], app_dir, creationflags)
            _run_git_logged(["git", "config", "core.autocrlf", "false"], app_dir, creationflags)
            readme = app_dir / "README.md"
            if not readme.exists():
                readme.write_text(f"# {name}\n\nEspacio de desarrollo local-first gestionado por MCP2P.\nUUID: `{p_uuid}`\n", encoding="utf-8")
            _run_git_logged(["git", "add", "."], app_dir, creationflags)
            _run_git_logged(["git", "commit", "-m", f"chore(init): initialize {clean_name} development workspace"], app_dir, creationflags)

        # 2. Inicializar repo de gobernanza con rama huérfana aislada
        if not (gov_dir / ".git").exists():
            _run_git_logged(["git", "init", "-b", "main"], gov_dir, creationflags)
            _run_git_logged(["git", "config", "core.autocrlf", "false"], gov_dir, creationflags)

            # Commit inicial de OKF en main
            marker = gov_dir / ".okf_project.json"
            marker.write_text(f'{{"project_uuid": "{p_uuid}", "name": "{name}"}}\n', encoding="utf-8")
            _run_git_logged(["git", "add", "."], gov_dir, creationflags)
            _run_git_logged(["git", "commit", "-m", f"init(okf): initialize governance master branch for {p_uuid}"], gov_dir, creationflags)

            # Desacoplar la rama huérfana en un worktree dentro de .git para no ensuciar main
            orphan_branch = f"orphan-conversa-memory-{p_uuid}"
            orphan_wt_path = gov_dir / ".git" / "orphan_worktree"
            _run_git_logged(
                ["git", "worktree", "add", "--orphan", "-b", orphan_branch, str(orphan_wt_path)],
                gov_dir, creationflags
            )
            if orphan_wt_path.exists():
                conversa_readme = orphan_wt_path / "README_CONVERSA.md"
                conversa_readme.write_text(
                    f"# Memoria Conversacional Desacoplada\n\nProyecto: {name} ({p_uuid})\nRama: {orphan_branch}\n",
                    encoding="utf-8"
                )
                _run_git_logged(["git", "add", "."], orphan_wt_path, creationflags)
                _run_git_logged(
                    ["git", "commit", "-m", f"init(orphan): initialize conversation memory branch for {p_uuid}"],
                    orphan_wt_path, creationflags
                )

        # 3. Instalar hooks de Git para sincronización en tiempo real
        for d in (app_dir, gov_dir):
            hooks_dir = d / ".git" / "hooks"
            if hooks_dir.is_dir():
                post_commit = hooks_dir / "post-commit"
                post_commit.write_text("#!/bin/sh\ntouch .git_pulse 2>/dev/null || true\n", encoding="utf-8")
                try:
                    post_commit.chmod(0o755)
                except Exception:
                    pass
