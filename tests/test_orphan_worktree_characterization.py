"""Caracterización de `ensure_orphan_worktree` y del commit de la rama huérfana en
`sync_three_tiers` (EGM-57: `git worktree`/`git commit` salen de `git_branches_ui.py` hacia
`GitWorkspacePort`). Escrito y en verde contra el código previo a la extracción.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from src.app.api_bridge import ApiBridge
from src.storage.db import DatabaseManager


@pytest.fixture
def git_env(monkeypatch):
    for k, v in {
        "GIT_AUTHOR_NAME": "MCP2P Test", "GIT_AUTHOR_EMAIL": "t@mcp2p.local",
        "GIT_COMMITTER_NAME": "MCP2P Test", "GIT_COMMITTER_EMAIL": "t@mcp2p.local",
        "MCP2P_NODE_UUID": "node_wt",
    }.items():
        monkeypatch.setenv(k, v)


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)


def _bridge_with_project(tmp_path):
    api = ApiBridge(db_manager=DatabaseManager(str(tmp_path / "wt.db")))
    proj = api.create_project("WtCharac")["project"]
    return api, proj, Path(proj["gov_repo_path"]).resolve()


def test_existing_worktree_is_returned_without_git(tmp_path, git_env, monkeypatch):
    api, proj, gov = _bridge_with_project(tmp_path)
    wt = gov / ".git" / "orphan_worktree"
    assert (wt / ".git").exists()

    def _no_git(*_a, **_k):
        raise AssertionError("no debe ejecutar git si el worktree ya está montado")

    monkeypatch.setattr(subprocess, "run", _no_git)
    assert api.ensure_orphan_worktree(gov, proj["project_uuid"]) == wt


def test_non_repo_root_is_refused(tmp_path, git_env):
    api, proj, _gov = _bridge_with_project(tmp_path)
    plain = tmp_path / "plain"
    plain.mkdir()
    with pytest.raises(RuntimeError, match="no es la raíz de un repositorio Git"):
        api.ensure_orphan_worktree(plain, proj["project_uuid"])


def test_missing_branch_creates_orphan_with_readme_commit(tmp_path, git_env):
    api, _proj, _gov = _bridge_with_project(tmp_path)
    repo = tmp_path / "gov_fresh"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    (repo / "x.txt").write_text("x", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "base")

    wt = api.ensure_orphan_worktree(repo, "proj_fresh")
    assert wt == repo.resolve() / ".git" / "orphan_worktree"
    assert (wt / "README_CONVERSA.md").read_text(encoding="utf-8") == (
        "# Memoria Conversacional Desacoplada\n\nUUID: `proj_fresh`\nRama: `orphan-conversa-memory-proj_fresh`\n"
    )
    assert _git(wt, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip() == "orphan-conversa-memory-proj_fresh"
    assert _git(wt, "log", "--format=%s").stdout.strip() == (
        "init(orphan): initialize decoupled conversational memory for proj_fresh"
    )
    assert "x.txt" not in _git(wt, "ls-files").stdout


def test_existing_local_branch_is_remounted(tmp_path, git_env):
    api, proj, gov = _bridge_with_project(tmp_path)
    wt = gov / ".git" / "orphan_worktree"
    head_before = _git(wt, "rev-parse", "HEAD").stdout.strip()
    _git(gov, "worktree", "remove", "--force", str(wt))
    _git(gov, "worktree", "prune")
    assert not wt.exists()

    assert api.ensure_orphan_worktree(gov, proj["project_uuid"]) == wt
    assert _git(wt, "rev-parse", "HEAD").stdout.strip() == head_before
    assert (wt / "README_CONVERSA.md").exists()


def test_sync_tier1_commits_outbox_on_orphan_branch(tmp_path, git_env):
    api, proj, gov = _bridge_with_project(tmp_path)
    p_uuid = proj["project_uuid"]
    api.db.register_or_update_node("node_wt", "Local", "agent_wt")
    api.bus.send_informative(p_uuid, "node_wt", "TEST_HEARTBEAT", {"s": 1})
    pending = len(api.db.get_pending_outbox_events(p_uuid))
    assert pending >= 1

    res = api.sync_three_tiers(p_uuid)
    assert res["success"] is True
    assert res["tier1_orphan"]["status"] == "ok" and res["tier1_orphan"]["flushed"] == pending
    wt = gov / ".git" / "orphan_worktree"
    assert _git(wt, "log", "-1", "--format=%s").stdout.strip() == (
        f"sync(orphan): flush {pending} events from node node_wt"
    )
    assert api.db.get_pending_outbox_events(p_uuid) == []
    assert list((wt / "nodes" / "node_wt" / "events").glob("*.jsonl"))


def test_git_branches_ui_no_longer_runs_git_by_subprocess():
    import re

    src = (Path(__file__).resolve().parents[1] / "src" / "app" / "ui_adapters" / "git_branches_ui.py").read_text(
        encoding="utf-8"
    )
    assert not re.search(r"\bsubprocess\b|_subprocess_run\(|creationflags", src)


def test_git_sync_manager_implements_orphan_worktree_on_the_port(tmp_path):
    from src.core.ports.connectors import GitWorkspacePort
    from src.storage.github_sync import GitHubSyncManager

    manager = GitHubSyncManager(db_manager=DatabaseManager(str(tmp_path / "port.db")))
    assert isinstance(manager, GitWorkspacePort)
    assert callable(manager.ensure_orphan_worktree)
