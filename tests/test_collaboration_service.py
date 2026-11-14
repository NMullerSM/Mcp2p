"""EGM-57: ProjectCollaborationService (aprobar/rechazar/retirar) con dobles de puerto, sin git real."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List

from src.core.services.collaboration_service import ProjectCollaborationService

ROOT = Path(__file__).resolve().parents[1]


class _Repo:
    def __init__(self, collabs: List[Dict[str, Any]]):
        self.collabs = collabs
        self.calls: List[tuple] = []

    def approve_collaborator(self, p, n):
        self.calls.append(("approve", p, n))
        return True

    def get_project_collaborators(self, p):
        return self.collabs

    def record_rejection(self, p, n):
        self.calls.append(("reject", p, n))
        return {"success": True}

    def remove_collaborator(self, p, n):
        self.calls.append(("remove", p, n))
        return True


class _Git:
    def __init__(self, commit_result):
        self.commit_result = commit_result
        self.commits: List[tuple] = []
        self.pushes: List[tuple] = []

    def commit_all_changes(self, path, msg):
        self.commits.append((str(path), msg))
        return self.commit_result

    def has_remote(self, path, remote="origin"):
        return True

    def git_push_branch(self, path, branch, remote="origin"):
        self.pushes.append((str(path), branch, remote))
        return {"success": True}


class _Okf:
    def __init__(self):
        self.trusted: List[tuple] = []

    def add_trusted_node_pubkey(self, gov, node, pub, user):
        self.trusted.append((str(gov), node, pub, user))


def _service(tmp_path, git, notes):
    gov = tmp_path / "gov"
    (gov / ".git").mkdir(parents=True)
    repo = _Repo([{"node_uuid": "node_peer_1234", "public_key": "PK", "github_username": ""}])
    okf = _Okf()
    svc = ProjectCollaborationService(
        collab_repo=repo,
        project_lookup=lambda p: {"project_uuid": p, "gov_repo_path": str(gov)},
        okf_port=okf, git_port=git, repo_host=None,
        peer_notifier=lambda node, env: notes.append((node, env)),
    )
    return svc, repo, okf, gov


def test_approve_commits_trusted_nodes_through_git_port_and_pushes(tmp_path):
    notes: list = []
    git = _Git({"success": True, "committed": True})
    svc, repo, okf, gov = _service(tmp_path, git, notes)

    res = svc.approve_collaborator("P1", "node_peer_1234")

    assert res == {"success": True, "invitations": []}
    assert okf.trusted == [(str(gov), "node_peer_1234", "PK", "peer")]
    assert git.commits == [(str(gov), "chore(gov): authorize peer node_pee (@node) in trusted_nodes.yaml")]
    assert git.pushes == [(str(gov), "main", "origin")]
    assert notes[0][1]["decision"] == "approved"


def test_approve_does_not_push_when_commit_fails(tmp_path):
    notes: list = []
    git = _Git({"success": False, "committed": False, "error": "fatal: simulated failure"})
    svc, _, _, _ = _service(tmp_path, git, notes)

    res = svc.approve_collaborator("P1", "node_peer_1234")

    assert res["success"] is True and res["gov_commit_error"] == "fatal: simulated failure"
    assert git.pushes == []


def test_reject_and_remove_go_through_repository_port(tmp_path):
    notes: list = []
    svc, repo, _, _ = _service(tmp_path, _Git({"success": True}), notes)
    assert svc.reject_collaborator("P1", "n2") == {"success": True}
    assert notes[-1] == ("n2", {"type": "join_decision", "decision": "rejected", "project_uuid": "P1", "node_uuid": "n2"})
    assert svc.remove_collaborator("P1", "n2") == {"success": True}
    assert ("reject", "P1", "n2") in repo.calls and ("remove", "P1", "n2") in repo.calls


def test_collab_ui_adapter_no_longer_runs_git_by_subprocess():
    src = (ROOT / "src" / "app" / "ui_adapters" / "collab_mesh_ui.py").read_text(encoding="utf-8")
    assert not re.search(r"\bsubprocess\b|_subprocess_run\(", src)
