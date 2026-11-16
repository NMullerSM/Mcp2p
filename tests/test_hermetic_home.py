"""La suite corre con un HOME temporal que ya trae identidad git (regresion del CI de 2.0.0)."""

import os
import subprocess
from pathlib import Path


def test_home_is_temporary_and_git_identity_is_defined():
    home = Path(os.environ["HOME"])
    assert home != Path.home().resolve() or "pytest" in str(home)
    out = subprocess.run(
        ["git", "config", "--global", "--get", "user.email"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    assert out == "test@mcp2p.local"


def test_git_commit_works_without_any_real_global_identity(tmp_path):
    repo = tmp_path / "r"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    (repo / "a.txt").write_text("x", encoding="utf-8")
    subprocess.run(["git", "add", "a.txt"], cwd=repo, check=True)
    res = subprocess.run(["git", "commit", "-q", "-m", "seed"], cwd=repo, capture_output=True, text=True)
    assert res.returncode == 0, res.stderr
