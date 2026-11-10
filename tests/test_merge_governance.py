"""F1+F2: sign_and_approve_item('pr_merge') reportaba 'merged: True' y marcaba la tarjeta
como fusionada aunque el merge real fallara (rama inexistente, conflicto), y la aprobación
por número de PR llamaba a la API de GitHub con repo_owner/repo_name vacíos."""
import subprocess
from pathlib import Path

import pytest

from src.app.api_bridge import ApiBridge
from src.storage.db import DatabaseManager


def _git_env(monkeypatch):
    monkeypatch.setenv("GIT_AUTHOR_NAME", "MCP2P Test")
    monkeypatch.setenv("GIT_AUTHOR_EMAIL", "test@mcp2p.local")
    monkeypatch.setenv("GIT_COMMITTER_NAME", "MCP2P Test")
    monkeypatch.setenv("GIT_COMMITTER_EMAIL", "test@mcp2p.local")


@pytest.mark.parametrize("scenario", ["missing_branch", "conflict"])
def test_pr_merge_failure_does_not_mark_card_merged(tmp_path, monkeypatch, scenario):
    _git_env(monkeypatch)
    db_path = str(tmp_path / "test.db")
    db = DatabaseManager(db_path)
    bridge = ApiBridge(db_manager=db)

    proj = bridge.create_project("MergeGov", code_repo_url="", gov_repo_url="")["project"]
    p_uuid = proj["project_uuid"]
    app = Path(proj["code_repo_path"])

    if scenario == "conflict":
        subprocess.run(["git", "checkout", "-b", "feature/conflict"], cwd=app, capture_output=True)
        (app / "README.md").write_text("conflict from feature\n", encoding="utf-8")
        subprocess.run(["git", "add", "."], cwd=app, capture_output=True)
        subprocess.run(["git", "commit", "-m", "feature change"], cwd=app, capture_output=True)
        subprocess.run(["git", "checkout", "main"], cwd=app, capture_output=True)
        (app / "README.md").write_text("conflict from main\n", encoding="utf-8")
        subprocess.run(["git", "add", "."], cwd=app, capture_output=True)
        subprocess.run(["git", "commit", "-m", "main change"], cwd=app, capture_output=True)
        branch_ref = "feature/conflict"
    else:
        branch_ref = "feature/nope"

    db.create_card(p_uuid, "CARD-MERGE-01", "merge test")
    db.update_card_details(p_uuid, "CARD-MERGE-01", status="in_review", branch_ref=branch_ref)

    res = bridge.sign_and_approve_item(p_uuid, "pr_merge", "CARD-MERGE-01", "APPROVED")
    assert res["success"] is False
    assert res["merged"] is False
    assert res.get("error")
    assert db.get_card_by_id(p_uuid, "CARD-MERGE-01")["status"] == "in_review"

    if scenario == "conflict":
        assert not (app / ".git" / "MERGE_HEAD").exists()
        status = subprocess.run(["git", "status", "--porcelain"], cwd=app, capture_output=True, text=True)
        assert status.stdout.strip() == ""


@pytest.mark.parametrize("case", ["resolvable", "dotted_repo", "unresolvable"])
def test_pr_merge_numeric_pr_owner_repo_resolution(tmp_path, monkeypatch, case):
    _git_env(monkeypatch)
    db_path = str(tmp_path / "test.db")
    db = DatabaseManager(db_path)

    urls = {
        "resolvable": "https://github.com/acme/widget.git",
        "dotted_repo": "git@github.com:acme/my.repo.git",
        "unresolvable": "",
    }
    proj = db.create_project(
        name="PrOwnerRepo", base_path=str(tmp_path / "ws"), init_git=False, code_repo_url=urls[case]
    )
    p_uuid = proj["project_uuid"]
    app_path = Path(proj["code_repo_path"])
    app_path.mkdir(parents=True, exist_ok=True)

    bridge = ApiBridge(db_manager=db)

    calls = []

    def fake_evaluate(**kwargs):
        calls.append(kwargs)
        return {"success": True, "merged": True}

    monkeypatch.setattr(bridge.github, "evaluate_and_merge_pull_request", fake_evaluate)

    res = bridge.sign_and_approve_item(p_uuid, "pr_merge", "42", "APPROVED")

    if case == "resolvable":
        assert len(calls) == 1
        assert calls[0]["repo_owner"] == "acme"
        assert calls[0]["repo_name"] == "widget"
        assert calls[0]["pr_number"] == 42
        assert res["success"] is True
    elif case == "dotted_repo":
        assert len(calls) == 1
        assert calls[0]["repo_name"] == "my.repo"
    else:
        assert calls == []
        assert res["success"] is False
        assert "owner/repo" in res["error"]
