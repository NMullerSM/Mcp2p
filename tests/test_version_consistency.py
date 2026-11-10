"""Las copias de la versión (pyproject.toml, frontend/src/version.js, frontend/package.json,
frontend/package-lock.json) deben estar sincronizadas con la fuente única de verdad
src/app/version.py. Si se olvida ejecutar scripts/sync_version.py en un release, este test
falla en local y en CI en vez de publicar un binario que se cree una versión vieja."""
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def test_all_version_copies_are_in_sync():
    res = subprocess.run(
        [sys.executable, "scripts/sync_version.py", "--check"],
        cwd=str(REPO_ROOT), capture_output=True, text=True,
    )
    assert res.returncode == 0, f"Versiones desincronizadas:\n{res.stdout}\n{res.stderr}"
