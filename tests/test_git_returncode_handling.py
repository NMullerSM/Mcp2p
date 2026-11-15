"""F7: varios flujos git ignoraban subprocess.run(check=False) sin revisar returncode — el
mismo patrón que ya rompió el CI por falta de identidad git. Ahora los fallos se registran
y, cuando corresponde, bloquean el efecto secundario en lugar de fingir éxito."""
import subprocess
import sys
from pathlib import Path

import pytest

from src.app.api_bridge import ApiBridge
from src.storage.db import DatabaseManager
from src.storage.okf_adapter import OKFAdapter

REPO_ROOT = Path(__file__).resolve().parent.parent


def _git_env(monkeypatch):
    monkeypatch.setenv("GIT_AUTHOR_NAME", "MCP2P Test")
    monkeypatch.setenv("GIT_AUTHOR_EMAIL", "test@mcp2p.local")
    monkeypatch.setenv("GIT_COMMITTER_NAME", "MCP2P Test")
    monkeypatch.setenv("GIT_COMMITTER_EMAIL", "test@mcp2p.local")


def _fake_run_failing_on(match_substr):
    real_run = subprocess.run

    def fake_run(args, *a, **kw):
        if isinstance(args, (list, tuple)) and any(match_substr in str(x) for x in args):
            out = "" if (kw.get("text") or kw.get("encoding")) else b""
            err = "fatal: simulated failure" if (kw.get("text") or kw.get("encoding")) else b"fatal: simulated failure"
            return subprocess.CompletedProcess(args, 128, out, err)
        return real_run(args, *a, **kw)

    return fake_run


def test_sync_three_tiers_commit_failure_keeps_events_pending(tmp_path, monkeypatch):
    _git_env(monkeypatch)
    db_path = str(tmp_path / "test.db")
    db = DatabaseManager(db_path)
    api = ApiBridge(db_manager=db)

    proj = api.create_project("SyncFail")["project"]
    p_uuid = proj["project_uuid"]
    db.register_or_update_node("node_local_1", "Local", "agent_local_1")
    api.bus.send_informative(p_uuid, "node_local_1", "TEST_HEARTBEAT", {"s": 1})

    pending_before = db.get_pending_outbox_events(p_uuid)
    assert len(pending_before) >= 1

    import src.app.api_bridge as api_bridge_mod
    monkeypatch.setattr(api_bridge_mod.subprocess, "run", _fake_run_failing_on("sync(orphan)"))
    res = api.sync_three_tiers(p_uuid)

    assert res["tier1_orphan"]["status"].startswith("commit_failed") if "tier1_orphan" in res else True
    pending_after = db.get_pending_outbox_events(p_uuid)
    assert len(pending_after) == len(pending_before)

    monkeypatch.undo()
    res2 = api.sync_three_tiers(p_uuid)
    assert db.get_pending_outbox_events(p_uuid) == [] or len(db.get_pending_outbox_events(p_uuid)) < len(pending_before)


def test_create_project_logs_git_failures(tmp_path, monkeypatch, caplog):
    _git_env(monkeypatch)
    db = DatabaseManager(str(tmp_path / "test.db"))

    # EGM-55: el git de create_project vive en el andamiaje inyectado, no en db.py.
    import src.adapters.provisioning.git_scaffolder as scaffold_mod
    monkeypatch.setattr(scaffold_mod.subprocess, "run", _fake_run_failing_on("commit"))

    caplog.set_level("WARNING", logger="mcp2p.adapters.provisioning.git")
    proj = db.create_project(name="NoCommit", base_path=str(tmp_path / "ws"), init_git=True)
    assert proj.get("project_uuid")
    assert any("commit" in r.getMessage() and "simulated" in r.getMessage() for r in caplog.records)


def test_worktree_creation_failures_are_reported(tmp_path, monkeypatch):
    _git_env(monkeypatch)
    db = DatabaseManager(str(tmp_path / "test.db"))
    api = ApiBridge(db_manager=db)
    proj = api.create_project("WtFail")["project"]
    p_uuid = proj["project_uuid"]
    gov_path = Path(proj["gov_repo_path"])

    orphan_wt = gov_path / ".git" / "orphan_worktree"
    subprocess.run(["git", "worktree", "remove", "--force", str(orphan_wt)], cwd=gov_path, capture_output=True)
    subprocess.run(["git", "worktree", "prune"], cwd=gov_path, capture_output=True)

    import src.app.api_bridge as api_bridge_mod
    monkeypatch.setattr(api_bridge_mod.subprocess, "run", _fake_run_failing_on("worktree"))

    with pytest.raises(RuntimeError, match="worktree"):
        api.ensure_orphan_worktree(gov_path, p_uuid)

    res = api.sync_three_tiers(p_uuid)
    assert res["tier1_orphan"]["status"].startswith("error")

    monkeypatch.undo()

    app_path = proj["code_repo_path"]
    import src.storage.okf_adapter as okf_mod
    monkeypatch.setattr(okf_mod.subprocess, "run", _fake_run_failing_on("worktree"))
    okf = OKFAdapter()
    res2 = okf.manage_task_worktree(app_path, gov_path, p_uuid, "CARD-WT1", "node_x", "create")
    assert res2["success"] is False
    registry_file = Path(app_path) / ".worktrees" / "registry.json"
    if registry_file.exists():
        assert "CARD-WT1" not in registry_file.read_text(encoding="utf-8")


def test_approve_collaborator_does_not_push_when_gov_commit_fails(tmp_path, monkeypatch):
    _git_env(monkeypatch)
    db = DatabaseManager(str(tmp_path / "test.db"))
    api = ApiBridge(db_manager=db)
    proj = api.create_project("ApproveFail")["project"]
    p_uuid = proj["project_uuid"]

    db.add_or_request_collaborator(p_uuid, "node_peer", github_username="", public_key="PK_TEST")

    monkeypatch.setattr(api.github, "has_remote", lambda *a, **k: True)
    push_calls = []
    monkeypatch.setattr(api.github, "git_push_branch", lambda *a, **k: push_calls.append((a, k)))

    import src.app.api_bridge as api_bridge_mod
    monkeypatch.setattr(api_bridge_mod.subprocess, "run", _fake_run_failing_on("authorize peer"))

    res = api.approve_collaborator(p_uuid, "node_peer")
    assert res["success"] is True
    assert push_calls == []
    assert "gov_commit_error" in res
