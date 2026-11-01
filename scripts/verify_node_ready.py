#!/usr/bin/env python3
"""
MCP2P Pre-Flight Node Diagnostic & Verification Script
Comprueba en <5 segundos si el entorno local (Linux o Windows) está 100% listo para
operar como nodo del enjambre P2P y participar en las pruebas 4A (LAN) y 4B (WAN).
"""

from __future__ import annotations

import os
import platform
import socket
import subprocess
import sys
from pathlib import Path

# Asegurar que el directorio raíz del proyecto esté en sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def print_status(step: str, ok: bool, message: str = ""):
    symbol = "[\033[92mOK\033[0m]" if ok else "[\033[91mFAIL\033[0m]"
    print(f" {symbol} {step}: {message}")


def check_python_version() -> bool:
    v = sys.version_info
    ok = v.major == 3 and v.minor >= 10
    print_status("Python 3.10+", ok, f"Detectado Python {v.major}.{v.minor}.{v.micro}")
    return ok


def check_git_installation() -> bool:
    try:
        res = subprocess.run(["git", "--version"], capture_output=True, text=True, check=True)
        ver = res.stdout.strip()
        print_status("Git CLI", True, ver)
        return True
    except Exception as e:
        print_status("Git CLI", False, str(e))
        return False


def check_ed25519_node_identity() -> bool:
    try:
        from src.p2p.crypto import NodeCryptoManager
        crypto = NodeCryptoManager()
        pubkey = crypto.public_key_base64
        key_exists = crypto.key_path.exists()
        print_status("Identidad Criptográfica Ed25519", key_exists, f"Clave Pública: {pubkey[:16]}... ({crypto.key_path})")
        return key_exists
    except Exception as e:
        print_status("Identidad Criptográfica Ed25519", False, str(e))
        return False


def check_lan_port_availability(port: int = 42424) -> bool:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind(("0.0.0.0", port))
        print_status(f"Puerto LAN {port}", True, "Disponible para Webhooks P2P")
        return True
    except Exception as e:
        print_status(f"Puerto LAN {port}", False, f"En uso o bloqueado: {e}")
        return False


def check_github_auth() -> bool:
    try:
        from src.storage.github_sync import GitHubSyncManager
        gh = GitHubSyncManager()
        status = gh.get_auth_status()
        is_auth = status.get("authenticated", False)
        provider = status.get("provider", "ninguno")
        username = status.get("username", "anónimo")
        print_status("Autenticación GitHub", is_auth, f"Proveedor: {provider} (@{username})")
        return is_auth
    except Exception as e:
        print_status("Autenticación GitHub", False, str(e))
        return False


def run_test_suite() -> bool:
    print("\nEjecutando suite completa de pruebas unitarias e integración...")
    pytest_bin = Path(sys.executable).parent / "pytest"
    cmd = [str(pytest_bin) if pytest_bin.exists() else "pytest", "-q"]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        passed = res.returncode == 0
        summary = res.stdout.strip().splitlines()[-1] if res.stdout.strip() else res.stderr
        print_status("Suite Pytest Completa (Etapas 1-4)", passed, summary)
        return passed
    except Exception as e:
        print_status("Suite Pytest Completa", False, str(e))
        return False


def main():
    print("=" * 60)
    print("  MCP2P: Verificación Pre-Vuelo de Nodo (Pre-Flight Check)")
    print(f"  Sistema: {platform.system()} {platform.release()} ({platform.machine()})")
    print(f"  Directorio: {Path.cwd()}")
    print("=" * 60)

    checks = [
        check_python_version(),
        check_git_installation(),
        check_ed25519_node_identity(),
        check_lan_port_availability(),
        check_github_auth()
    ]

    all_passed = all(checks)
    print("-" * 60)
    if all_passed:
        print("\033[92m Nodo validado con éxito. Listo para coordinar enjambre P2P.\033[0m")
    else:
        print("\033[93m Advertencia: Hay dependencias o configuraciones pendientes.\033[0m")
    print("=" * 60)


if __name__ == "__main__":
    main()
