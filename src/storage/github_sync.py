"""
Módulo de Integración y Autenticación con GitHub para MCP2P.

Gestiona:
1. Comprobación de estado de autenticación (vía gh CLI o token de entorno).
2. Obtención de identidad del desarrollador GitHub (username, scopes).
3. Vinculación y publicación de repositorios locales (app y okf).
4. Verificación y asignación de colaboradores en repositorios remotos.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("mcp2p.github")


class GitHubSyncManager:
    """Gestiona la autenticación y operaciones con GitHub de forma determinista a costo 0 de tokens."""

    def __init__(self, db_manager: Optional[Any] = None) -> None:
        self.gh_bin = shutil.which("gh")
        self._db = db_manager

    @property
    def db(self) -> Any:
        if self._db is None:
            from src.storage.db import DatabaseManager
            self._db = DatabaseManager.get_instance()
        return self._db

    def get_auth_status(self) -> Dict[str, Any]:
        """Comprueba si el usuario tiene una sesión activa de GitHub (PAT, gh CLI o env)."""
        git_identity = self.get_git_global_identity()

        # 1. Comprobar Token Personal (PAT) almacenado en SQLite
        try:
            pat_token = self.db.get_setting("github_pat_token")
            if pat_token:
                username = self.db.get_setting("github_pat_username", "github_user")
                return {
                    "authenticated": True,
                    "provider": "pat",
                    "username": username,
                    "details": f"Autenticado mediante Personal Access Token (PAT) (@{username})",
                    "git_identity": git_identity
                }
        except Exception as e:
            logger.debug(f"Error comprobando PAT en db: {e}")

        # 2. Comprobar gh CLI
        if self.gh_bin:
            try:
                proc = subprocess.run(
                    [self.gh_bin, "auth", "status", "--show-token"],
                    capture_output=True,
                    text=True,
                    timeout=5
                )
                if proc.returncode == 0 or "Logged in to github.com" in proc.stdout or "Logged in to github.com" in proc.stderr:
                    username = self._extract_username_from_gh()
                    return {
                        "authenticated": True,
                        "provider": "gh_cli",
                        "username": username or "github_user",
                        "details": f"Sesión activa mediante GitHub CLI (@{username or 'usuario'})",
                        "git_identity": git_identity
                    }
            except Exception as e:
                logger.debug(f"gh auth status falló: {e}")

        # 3. Comprobar variable de entorno GITHUB_TOKEN / GH_TOKEN
        token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
        if token:
            return {
                "authenticated": True,
                "provider": "env_token",
                "username": os.environ.get("GITHUB_USER", "token_user"),
                "details": "Autenticado mediante variable de entorno",
                "git_identity": git_identity
            }

        return {
            "authenticated": False,
            "provider": None,
            "username": "",
            "details": "No se detectó sesión de GitHub. Puedes ingresar un Personal Access Token (PAT) o autenticarte con 'gh auth login'.",
            "git_identity": git_identity
        }

    def save_pat_token(self, token: str) -> Dict[str, Any]:
        """Valida y guarda un GitHub Personal Access Token (PAT) en la base de datos local."""
        token_clean = token.strip()
        if not token_clean:
            return {"success": False, "error": "El token de GitHub no puede estar vacío"}

        # Validar token consultando la API oficial de GitHub https://api.github.com/user
        req = urllib.request.Request(
            "https://api.github.com/user",
            headers={
                "Authorization": f"Bearer {token_clean}",
                "Accept": "application/vnd.github+json",
                "User-Agent": "MCP2P-Desktop-Client"
            }
        )

        try:
            with urllib.request.urlopen(req, timeout=8) as response:
                if response.status == 200:
                    data = json.loads(response.read().decode("utf-8"))
                    username = data.get("login", "github_user")
                    # Guardar en SQLite
                    self.db.set_setting("github_pat_token", token_clean)
                    self.db.set_setting("github_pat_username", username)
                    return {
                        "success": True,
                        "username": username,
                        "provider": "pat",
                        "details": f"Autenticado exitosamente vía PAT como @{username}"
                    }
                else:
                    return {"success": False, "error": f"Respuesta inesperada de GitHub: HTTP {response.status}"}
        except urllib.error.HTTPError as e:
            if e.code == 401:
                return {"success": False, "error": "Token de GitHub no válido o expirado (HTTP 401 Unauthorized)"}
            return {"success": False, "error": f"Error de autenticación con GitHub (HTTP {e.code}): {e.reason}"}
        except Exception as e:
            return {"success": False, "error": f"Error de conexión con api.github.com: {str(e)}"}

    def clear_pat_token(self) -> Dict[str, Any]:
        """Elimina el Personal Access Token almacenado localmente."""
        try:
            self.db.delete_setting("github_pat_token")
            self.db.delete_setting("github_pat_username")
            return {"success": True, "message": "Token de GitHub eliminado correctamente"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def _extract_username_from_gh(self) -> Optional[str]:
        """Obtiene el nombre de usuario autenticado en gh CLI."""
        if not self.gh_bin:
            return None
        try:
            proc = subprocess.run(
                [self.gh_bin, "api", "user", "--jq", ".login"],
                capture_output=True,
                text=True,
                timeout=5
            )
            if proc.returncode == 0 and proc.stdout.strip():
                return proc.stdout.strip()
        except Exception:
            pass
        return None

    def setup_repo_remote(self, repo_path: str | Path, remote_url: str, remote_name: str = "origin") -> Dict[str, Any]:
        """Configura el repositorio remoto para una carpeta Git local."""
        path = Path(repo_path)
        if not (path / ".git").is_dir():
            return {"success": False, "error": f"No es un repositorio Git válido: {path}"}

        try:
            # Comprobar si ya existe el remote
            check = subprocess.run(
                ["git", "remote", "get-url", remote_name],
                cwd=path,
                capture_output=True,
                text=True
            )
            if check.returncode == 0:
                # Actualizar URL existente
                subprocess.run(
                    ["git", "remote", "set-url", remote_name, remote_url],
                    cwd=path,
                    capture_output=True,
                    check=True
                )
            else:
                # Agregar nuevo remote
                subprocess.run(
                    ["git", "remote", "add", remote_name, remote_url],
                    cwd=path,
                    capture_output=True,
                    check=True
                )
            return {"success": True, "remote_name": remote_name, "remote_url": remote_url}
        except subprocess.CalledProcessError as e:
            return {"success": False, "error": f"Error configurando remoto Git: {e}"}

    def get_git_global_identity(self) -> Dict[str, str]:
        """Obtiene el nombre y correo configurados en Git global del sistema."""
        name = ""
        email = ""
        try:
            p_name = subprocess.run(["git", "config", "--global", "user.name"], capture_output=True, text=True, timeout=3)
            if p_name.returncode == 0:
                name = p_name.stdout.strip()
            p_email = subprocess.run(["git", "config", "--global", "user.email"], capture_output=True, text=True, timeout=3)
            if p_email.returncode == 0:
                email = p_email.stdout.strip()
        except Exception as e:
            logger.debug(f"Error leyendo git config global: {e}")
        return {"name": name, "email": email}

    def get_git_branches_and_log(self, repo_path: str | Path) -> Dict[str, Any]:
        """Recupera la rama actual, ramas disponibles y el historial de commits con grafo."""
        path = Path(repo_path)
        if not (path / ".git").is_dir():
            return {"success": False, "error": f"No es un repositorio Git: {path}", "branches": [], "commits": []}

        try:
            # 1. Rama actual
            cur_p = subprocess.run(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=path, capture_output=True, text=True, timeout=5)
            current_branch = cur_p.stdout.strip() if cur_p.returncode == 0 else "main"

            # 2. Lista de ramas
            b_p = subprocess.run(["git", "branch", "--format=%(refname:short)"], cwd=path, capture_output=True, text=True, timeout=5)
            branches = [b.strip() for b in b_p.stdout.splitlines() if b.strip()] if b_p.returncode == 0 else [current_branch]

            # 3. Historial de commits con padres para graficar ramas
            log_p = subprocess.run(
                ["git", "log", "--all", "--date=iso", "--pretty=format:%h|%p|%an|%ad|%s|%d", "-n", "30"],
                cwd=path,
                capture_output=True,
                text=True,
                timeout=5
            )
            commits = []
            if log_p.returncode == 0 and log_p.stdout.strip():
                for line in log_p.stdout.splitlines():
                    parts = line.strip().split("|")
                    if len(parts) >= 5:
                        h = parts[0].strip()
                        parents = [p.strip() for p in parts[1].split() if p.strip()]
                        author = parts[2].strip()
                        date_str = parts[3].strip()
                        msg = parts[4].strip()
                        refs = parts[5].strip() if len(parts) > 5 else ""
                        commits.append({
                            "hash": h,
                            "parents": parents,
                            "author": author,
                            "date": date_str,
                            "message": msg,
                            "refs": refs,
                            "is_head": f"HEAD -> {current_branch}" in refs or refs == f"HEAD"
                        })

            # 4. Remotos configurados
            rem_p = subprocess.run(["git", "remote", "-v"], cwd=path, capture_output=True, text=True, timeout=5)
            remotes = rem_p.stdout.strip().splitlines() if rem_p.returncode == 0 else []

            return {
                "success": True,
                "current_branch": current_branch,
                "branches": branches,
                "commits": commits,
                "has_remotes": len(remotes) > 0,
                "remotes": remotes
            }
        except Exception as e:
            return {"success": False, "error": f"Error inspeccionando Git: {str(e)}", "branches": [], "commits": []}

    def git_fetch(self, repo_path: str | Path, remote: str = "origin") -> Dict[str, Any]:
        """Ejecuta git fetch para sincronizar referencias con el remoto."""
        path = Path(repo_path)
        try:
            proc = subprocess.run(["git", "fetch", remote], cwd=path, capture_output=True, text=True, timeout=15)
            if proc.returncode == 0:
                return {"success": True, "output": proc.stdout or "Referencias remotas actualizadas"}
            return {"success": False, "error": proc.stderr or proc.stdout}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def git_pull(self, repo_path: str | Path, remote: str = "origin", branch: Optional[str] = None) -> Dict[str, Any]:
        """Ejecuta git pull para traer los últimos cambios."""
        path = Path(repo_path)
        cmd = ["git", "pull", remote]
        if branch:
            cmd.append(branch)
        try:
            proc = subprocess.run(cmd, cwd=path, capture_output=True, text=True, timeout=20)
            if proc.returncode == 0:
                return {"success": True, "output": proc.stdout}
            return {"success": False, "error": proc.stderr or proc.stdout}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def git_push_branch(self, repo_path: str | Path, branch: str, remote: str = "origin") -> Dict[str, Any]:
        """Publica o empuja una rama local hacia el repositorio remoto."""
        path = Path(repo_path)
        try:
            proc = subprocess.run(["git", "push", "-u", remote, branch], cwd=path, capture_output=True, text=True, timeout=25)
            if proc.returncode == 0:
                return {"success": True, "output": proc.stdout}
            return {"success": False, "error": proc.stderr or proc.stdout}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def git_merge(self, repo_path: str | Path, source_branch: str, target_branch: str = "main") -> Dict[str, Any]:
        """Fusiona una rama de feature en la rama destino."""
        path = Path(repo_path)
        try:
            # 1. Asegurar checkout en target
            subprocess.run(["git", "checkout", target_branch], cwd=path, capture_output=True, check=True, timeout=10)
            # 2. Merge de source_branch
            proc = subprocess.run(["git", "merge", source_branch, "--no-ff", "-m", f"merge: integrate {source_branch} into {target_branch}"], cwd=path, capture_output=True, text=True, timeout=15)
            if proc.returncode == 0:
                return {"success": True, "output": proc.stdout}
            return {"success": False, "error": proc.stderr or proc.stdout}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def git_create_branch(self, repo_path: str | Path, branch_name: str) -> Dict[str, Any]:
        """Crea y hace checkout de una nueva rama."""
        path = Path(repo_path)
        try:
            proc = subprocess.run(["git", "checkout", "-b", branch_name], cwd=path, capture_output=True, text=True, timeout=10)
            if proc.returncode == 0:
                return {"success": True, "branch": branch_name}
            return {"success": False, "error": proc.stderr or proc.stdout}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def push_initial_commit(self, repo_path: str | Path, branch: str = "main") -> Dict[str, Any]:
        """Realiza el push inicial al remoto configurado si está autenticado."""
        path = Path(repo_path)
        try:
            proc = subprocess.run(
                ["git", "push", "-u", "origin", branch],
                cwd=path,
                capture_output=True,
                text=True,
                timeout=20
            )
            if proc.returncode == 0:
                return {"success": True, "output": proc.stdout}
            return {"success": False, "error": proc.stderr or proc.stdout}
        except Exception as e:
            return {"success": False, "error": str(e)}
