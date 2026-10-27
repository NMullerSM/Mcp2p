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
        # 1. Comprobar Token Personal (PAT) almacenado en SQLite
        try:
            pat_token = self.db.get_setting("github_pat_token")
            if pat_token:
                username = self.db.get_setting("github_pat_username", "github_user")
                return {
                    "authenticated": True,
                    "provider": "pat",
                    "username": username,
                    "details": f"Autenticado mediante Personal Access Token (PAT) (@{username})"
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
                        "details": f"Sesión activa mediante GitHub CLI (@{username or 'usuario'})"
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
                "details": "Autenticado mediante variable de entorno"
            }

        return {
            "authenticated": False,
            "provider": None,
            "username": "",
            "details": "No se detectó sesión de GitHub. Puedes ingresar un Personal Access Token (PAT) o autenticarte con 'gh auth login'."
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
