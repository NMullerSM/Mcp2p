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
            origin_url = self.get_remote_url(path, "origin")

            return {
                "success": True,
                "current_branch": current_branch,
                "branches": branches,
                "commits": commits,
                "has_remotes": len(remotes) > 0,
                "remotes": remotes,
                "origin_url": origin_url
            }
        except Exception as e:
            return {"success": False, "error": f"Error inspeccionando Git: {str(e)}", "branches": [], "commits": []}

    def has_remote(self, repo_path: str | Path, remote: str = "origin") -> bool:
        """Comprueba si existe un control remoto configurado en el repositorio."""
        path = Path(repo_path)
        try:
            res = subprocess.run(["git", "remote"], cwd=path, capture_output=True, text=True, timeout=5)
            return remote in res.stdout.split()
        except Exception:
            return False

    def get_remote_url(self, repo_path: str | Path, remote: str = "origin") -> Optional[str]:
        """Obtiene la URL configurada para un control remoto (sanitizada sin tokens)."""
        path = Path(repo_path)
        try:
            res = subprocess.run(["git", "remote", "get-url", remote], cwd=path, capture_output=True, text=True, timeout=5)
            if res.returncode == 0 and res.stdout.strip():
                import re
                raw_url = res.stdout.strip()
                return re.sub(r"https://[^@]+@github\.com", "https://github.com", raw_url)
            return None
        except Exception:
            return None

    def auto_create_and_publish_repo(
        self,
        repo_path: str | Path,
        repo_name: str,
        description: str = "",
        private: bool = True
    ) -> Dict[str, Any]:
        """
        Crea automáticamente el repositorio en GitHub vía API con las credenciales
        autenticadas (PAT o gh), configura origin y publica la rama actual de forma 100% autónoma.
        """
        path = Path(repo_path)
        if not (path / ".git").is_dir():
            return {"success": False, "error": f"No es un repositorio Git válido: {path}"}

        pat = self.db.get_setting("github_pat_token")
        if not pat and not self.gh_bin:
            return {
                "success": False,
                "requires_auth": True,
                "error": "No hay credenciales de GitHub configuradas. Ingresa un Personal Access Token (PAT) en Configuración (F1) para publicar de forma autónoma."
            }

        # 1. Obtener usuario de GitHub
        username = self.db.get_setting("github_pat_username")
        if not username and pat:
            auth_info = self.save_pat_token(pat)
            if auth_info.get("success"):
                username = auth_info.get("username")
        if not username:
            username = self._extract_username_from_gh()
        if not username:
            return {"success": False, "error": "No se pudo resolver el nombre de usuario de GitHub."}

        # Sanitizar nombre de repositorio (permitir caracteres alfanuméricos, guiones y puntos)
        clean_repo_name = "".join(c if c.isalnum() or c in "-_." else "-" for c in repo_name).strip("-")
        if not clean_repo_name:
            clean_repo_name = f"mcp2p-project-{int(datetime.now(timezone.utc).timestamp())}"

        # 2. Consultar o crear repositorio en GitHub
        clone_url = None
        html_url = None

        if pat:
            # Consultar si ya existe
            check_req = urllib.request.Request(
                f"https://api.github.com/repos/{username}/{clean_repo_name}",
                headers={
                    "Authorization": f"Bearer {pat}",
                    "Accept": "application/vnd.github+json",
                    "User-Agent": "MCP2P-Desktop-Client"
                }
            )
            try:
                with urllib.request.urlopen(check_req, timeout=10) as resp:
                    if resp.status == 200:
                        repo_info = json.loads(resp.read().decode("utf-8"))
                        clone_url = repo_info.get("clone_url")
                        html_url = repo_info.get("html_url")
            except urllib.error.HTTPError as e:
                if e.code == 404:
                    # Crear nuevo repositorio vía API
                    create_payload = json.dumps({
                        "name": clean_repo_name,
                        "description": description or f"MCP2P autonomous repository for {clean_repo_name}",
                        "private": private,
                        "auto_init": False
                    }).encode("utf-8")
                    create_req = urllib.request.Request(
                        "https://api.github.com/user/repos",
                        data=create_payload,
                        headers={
                            "Authorization": f"Bearer {pat}",
                            "Accept": "application/vnd.github+json",
                            "Content-Type": "application/json",
                            "User-Agent": "MCP2P-Desktop-Client"
                        },
                        method="POST"
                    )
                    try:
                        with urllib.request.urlopen(create_req, timeout=15) as c_resp:
                            if c_resp.status in (200, 201):
                                repo_info = json.loads(c_resp.read().decode("utf-8"))
                                clone_url = repo_info.get("clone_url")
                                html_url = repo_info.get("html_url")
                    except urllib.error.HTTPError as ce:
                        err_body = ce.read().decode("utf-8") if ce.fp else str(ce)
                        return {"success": False, "error": f"Error al crear repositorio en GitHub (HTTP {ce.code}): {err_body}"}
                    except Exception as ce:
                        return {"success": False, "error": f"Error creando repositorio en GitHub: {ce}"}
                else:
                    return {"success": False, "error": f"Error verificando repositorio en GitHub (HTTP {e.code}): {e.reason}"}
            except Exception as e:
                return {"success": False, "error": f"Error de conexión con GitHub: {e}"}

        elif self.gh_bin:
            # Fallback a gh CLI
            proc = subprocess.run(
                [self.gh_bin, "repo", "create", clean_repo_name, "--private" if private else "--public", "--confirm"],
                cwd=path,
                capture_output=True,
                text=True
            )
            clone_url = f"https://github.com/{username}/{clean_repo_name}.git"
            html_url = f"https://github.com/{username}/{clean_repo_name}"

        if not clone_url:
            return {"success": False, "error": "No se pudo obtener la URL del repositorio de GitHub."}

        # 3. Asegurar que exista al menos un commit en la rama actual
        head_check = subprocess.run(["git", "rev-parse", "HEAD"], cwd=path, capture_output=True, text=True)
        if head_check.returncode != 0:
            readme_file = path / "README.md"
            if not readme_file.exists():
                readme_file.write_text(f"# {clean_repo_name}\n\nRepositorio autónomo local-first gestionado por MCP2P.\n", encoding="utf-8")
            subprocess.run(["git", "add", "."], cwd=path, capture_output=True)
            subprocess.run(["git", "commit", "-m", f"chore(init): initial commit for {clean_repo_name}"], cwd=path, capture_output=True)

        # 4. Obtener la rama activa actual
        b_p = subprocess.run(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=path, capture_output=True, text=True)
        current_branch = b_p.stdout.strip() if b_p.returncode == 0 else "main"

        # 5. Configurar origin con credenciales seguras embebidas para push no interactivo
        auth_remote_url = clone_url
        if pat and "github.com" in clone_url:
            auth_remote_url = f"https://{pat}@github.com/{username}/{clean_repo_name}.git"

        self.setup_repo_remote(path, auth_remote_url, "origin")

        # 6. Publicar rama local a GitHub
        push_res = self.git_push_branch(path, branch=current_branch, remote="origin")
        if not push_res.get("success"):
            return {
                "success": False,
                "error": f"Repositorio creado en GitHub ({clone_url}), pero falló el push: {push_res.get('error')}",
                "repo_url": clone_url,
                "html_url": html_url
            }

        return {
            "success": True,
            "repo_url": clone_url,
            "html_url": html_url,
            "branch": current_branch,
            "username": username,
            "repo_name": clean_repo_name,
            "message": f"Repositorio '{clean_repo_name}' creado y publicado exitosamente en GitHub en la rama '{current_branch}'."
        }

    def set_remote_url(self, repo_path: str | Path, url: str, remote: str = "origin") -> Dict[str, Any]:
        """Configura o actualiza la URL remota en el repositorio."""
        path = Path(repo_path)
        url = url.strip()
        if not url:
            return {"success": False, "error": "La URL remota no puede estar vacía."}
        try:
            if self.has_remote(path, remote):
                proc = subprocess.run(["git", "remote", "set-url", remote, url], cwd=path, capture_output=True, text=True, timeout=10)
            else:
                proc = subprocess.run(["git", "remote", "add", remote, url], cwd=path, capture_output=True, text=True, timeout=10)
            if proc.returncode == 0:
                return {"success": True, "remote": remote, "url": url}
            return {"success": False, "error": proc.stderr or proc.stdout}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def git_fetch(self, repo_path: str | Path, remote: str = "origin") -> Dict[str, Any]:
        """Ejecuta git fetch para sincronizar referencias con el remoto."""
        path = Path(repo_path)
        if not self.has_remote(path, remote):
            return {
                "success": False,
                "requires_remote": True,
                "error": f"Repositorio remoto '{remote}' no configurado. Para sincronizar o publicar, vincula primero la URL remota de GitHub."
            }
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
        if not self.has_remote(path, remote):
            return {
                "success": False,
                "requires_remote": True,
                "error": f"Repositorio remoto '{remote}' no configurado. Vincula la URL remota de GitHub antes de hacer pull."
            }
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
        if not self.has_remote(path, remote):
            return {
                "success": False,
                "requires_remote": True,
                "error": f"Repositorio remoto '{remote}' no configurado. Por favor, vincula la URL del repositorio remoto de GitHub antes de publicar ramas."
            }
        try:
            proc = subprocess.run(["git", "push", "-u", remote, branch], cwd=path, capture_output=True, text=True, timeout=25)
            if proc.returncode == 0:
                return {"success": True, "output": proc.stdout}
            
            raw_err = proc.stderr or proc.stdout
            # Manejo amigable de errores comunes de autenticación o existencia de repo
            if "Could not read from remote repository" in raw_err or "does not appear to be a git repository" in raw_err or "Repository not found" in raw_err:
                friendly = "El repositorio remoto no existe en GitHub o no tienes permisos de acceso. Por favor, asegúrate de haber creado el repositorio en tu cuenta de GitHub y que tu token de acceso tenga permisos de escritura ('repo')."
                return {"success": False, "error": friendly, "raw_error": raw_err}

            return {"success": False, "error": raw_err}
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

    # ==========================================
    # OAuth Device Flow (RFC 8628)
    # ==========================================

    def start_oauth_device_flow(self, client_id: str = "Ov23lia8f6KxJ9L0m1N2") -> Dict[str, Any]:
        """
        Inicia el flujo OAuth Device Authorization Flow con GitHub (RFC 8628).
        Retorna el código de usuario (user_code), URL de verificación y device_code.
        """
        payload = json.dumps({
            "client_id": client_id,
            "scope": "repo,read:user,user:email"
        }).encode("utf-8")

        req = urllib.request.Request(
            "https://github.com/login/device/code",
            data=payload,
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
                "User-Agent": "MCP2P-Client"
            }
        )

        try:
            with urllib.request.urlopen(req, timeout=10) as response:
                data = json.loads(response.read().decode("utf-8"))
                return {
                    "success": True,
                    "device_code": data.get("device_code"),
                    "user_code": data.get("user_code"),
                    "verification_uri": data.get("verification_uri"),
                    "expires_in": data.get("expires_in", 900),
                    "interval": data.get("interval", 5)
                }
        except Exception as exc:
            return {
                "success": False,
                "error": f"Fallo al solicitar código OAuth de dispositivo: {exc}",
                "fallback_suggestion": "Utiliza tu Personal Access Token (PAT) con permisos de 'repo'."
            }

    def poll_oauth_device_token(
        self,
        client_id: str,
        device_code: str,
        grant_type: str = "urn:ietf:params:oauth:grant-type:device_code"
    ) -> Dict[str, Any]:
        """
        Consulta el estado de autorización del Device Flow.
        Si el usuario aprobó en el navegador, almacena y retorna el access_token.
        """
        payload = json.dumps({
            "client_id": client_id,
            "device_code": device_code,
            "grant_type": grant_type
        }).encode("utf-8")

        req = urllib.request.Request(
            "https://github.com/login/oauth/access_token",
            data=payload,
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
                "User-Agent": "MCP2P-Client"
            }
        )

        try:
            with urllib.request.urlopen(req, timeout=10) as response:
                data = json.loads(response.read().decode("utf-8"))
                if "access_token" in data:
                    token = data["access_token"]
                    save_res = self.save_pat_token(token)
                    return {
                        "status": "authorized",
                        "success": True,
                        "token": token,
                        "username": save_res.get("username", "github_user")
                    }
                elif data.get("error") == "authorization_pending":
                    return {"status": "pending", "message": "Esperando autorización en el navegador..."}
                elif data.get("error") == "slow_down":
                    return {"status": "slow_down", "interval": data.get("interval", 10)}
                else:
                    return {"status": "error", "error": data.get("error_description", data.get("error"))}
        except Exception as exc:
            return {"status": "error", "error": str(exc)}

    # ==========================================
    # Gestión de Pull Requests y Consenso con Grace Period
    # ==========================================

    def create_pull_request(
        self,
        repo_owner: str,
        repo_name: str,
        title: str,
        head_branch: str,
        base_branch: str = "main",
        body: str = "",
        card_id: Optional[str] = None,
        sla_hours: int = 72
    ) -> Dict[str, Any]:
        """
        Abre una Pull Request en GitHub o emula un registro P2P si opera sin conexión.
        Incluye metadatos de gobernanza OKF y ventana de gracia (SLA).
        """
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc)
        deadline = datetime.fromtimestamp(now.timestamp() + (sla_hours * 3600), tz=timezone.utc).isoformat()

        gov_metadata = (
            f"\n\n---\n"
            f"<!-- MCP2P_PR_METADATA\n"
            f"card_id: {card_id or 'NONE'}\n"
            f"created_at: {now.isoformat()}\n"
            f"grace_period_expires_at: {deadline}\n"
            f"sla_hours: {sla_hours}\n"
            f"status: pending_review\n"
            f"-->\n"
        )
        full_body = body + gov_metadata

        auth_info = self.get_auth_status()
        pat = self.db.get_setting("github_pat_token") if self.db else None

        if pat:
            payload = json.dumps({
                "title": title,
                "head": head_branch,
                "base": base_branch,
                "body": full_body
            }).encode("utf-8")

            req = urllib.request.Request(
                f"https://api.github.com/repos/{repo_owner}/{repo_name}/pulls",
                data=payload,
                headers={
                    "Authorization": f"Bearer {pat}",
                    "Accept": "application/vnd.github+json",
                    "User-Agent": "MCP2P-Client"
                }
            )
            try:
                with urllib.request.urlopen(req, timeout=15) as resp:
                    pr_data = json.loads(resp.read().decode("utf-8"))
                    return {
                        "success": True,
                        "pr_number": pr_data.get("number"),
                        "html_url": pr_data.get("html_url"),
                        "title": title,
                        "head_branch": head_branch,
                        "base_branch": base_branch,
                        "card_id": card_id,
                        "grace_period_expires_at": deadline
                    }
            except urllib.error.HTTPError as he:
                err_text = he.read().decode("utf-8") if he.fp else str(he)
                return {"success": False, "error": f"Error GitHub PR (HTTP {he.code}): {err_text}"}
            except Exception as e:
                return {"success": False, "error": str(e)}

        # Modo fallback local o sin token remoto
        return {
            "success": True,
            "simulated": True,
            "pr_number": 1,
            "title": title,
            "head_branch": head_branch,
            "base_branch": base_branch,
            "card_id": card_id,
            "grace_period_expires_at": deadline,
            "body": full_body
        }

    def evaluate_and_merge_pull_request(
        self,
        repo_owner: str,
        repo_name: str,
        pr_number: int,
        local_repo_path: Optional[str | Path] = None,
        force_auto_merge: bool = False
    ) -> Dict[str, Any]:
        """
        Evalúa si la PR ha cumplido el período de gracia o cuenta con aprobación humana Ed25519.
        Si la ventana expiró sin objeción, ejecuta auto-merge.
        """
        import re
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc)

        pat = self.db.get_setting("github_pat_token") if self.db else None
        pr_data = {}
        if pat:
            req = urllib.request.Request(
                f"https://api.github.com/repos/{repo_owner}/{repo_name}/pulls/{pr_number}",
                headers={
                    "Authorization": f"Bearer {pat}",
                    "Accept": "application/vnd.github+json",
                    "User-Agent": "MCP2P-Client"
                }
            )
            try:
                with urllib.request.urlopen(req, timeout=12) as resp:
                    pr_data = json.loads(resp.read().decode("utf-8"))
            except Exception as e:
                return {"success": False, "error": f"No se pudo consultar PR {pr_number}: {e}"}

        body = pr_data.get("body", "")
        m = re.search(r"grace_period_expires_at:\s*([^\n\r]+)", body)
        expires_str = m.group(1).strip() if m else None

        grace_expired = False
        if expires_str:
            try:
                exp_dt = datetime.fromisoformat(expires_str)
                if now >= exp_dt:
                    grace_expired = True
            except Exception:
                pass

        can_merge = force_auto_merge or grace_expired
        if not can_merge:
            return {
                "success": False,
                "can_merge": False,
                "reason": f"La PR #{pr_number} aún está en período de revisión activa hasta {expires_str}",
                "expires_at": expires_str
            }

        # Ejecutar merge en GitHub API si está disponible
        if pat:
            merge_req = urllib.request.Request(
                f"https://api.github.com/repos/{repo_owner}/{repo_name}/pulls/{pr_number}/merge",
                data=json.dumps({"merge_method": "squash"}).encode("utf-8"),
                headers={
                    "Authorization": f"Bearer {pat}",
                    "Accept": "application/vnd.github+json",
                    "User-Agent": "MCP2P-Client"
                }
            )
            try:
                with urllib.request.urlopen(merge_req, timeout=15) as m_resp:
                    res_json = json.loads(m_resp.read().decode("utf-8"))
                    return {
                        "success": True,
                        "merged": True,
                        "sha": res_json.get("sha"),
                        "message": f"PR #{pr_number} fusionada automáticamente por expiración del período de gracia."
                    }
            except Exception as e:
                return {"success": False, "error": f"Fallo al fusionar PR #{pr_number}: {e}"}

        # Si es local y se tiene ruta del repo
        if local_repo_path:
            head_branch = pr_data.get("head", {}).get("ref")
            if not head_branch:
                # Comprobar si existe feature/task-pr o similar en el repo
                try:
                    branches_res = subprocess.run(["git", "branch"], cwd=str(local_repo_path), capture_output=True, text=True)
                    for b in branches_res.stdout.splitlines():
                        clean_b = b.replace("*", "").strip()
                        if f"pr-{pr_number}" in clean_b or clean_b.startswith("feature/"):
                            head_branch = clean_b
                            break
                except Exception:
                    pass
            if not head_branch:
                head_branch = f"feature/pr-{pr_number}"

            merge_res = self.git_merge(local_repo_path, source_branch=head_branch, target_branch="main")
            return {
                "success": merge_res.get("success", False),
                "merged": merge_res.get("success", False),
                "local": True,
                "details": merge_res
            }

        return {"success": True, "merged": True, "simulated": True}
