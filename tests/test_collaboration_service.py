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


# ---------------------------------------------------------------------------
# EGM-57 (resto): flujo de unión en el núcleo y servicio registrado en CoreKernel
# ---------------------------------------------------------------------------


def _kernel_with_identity(tmp_path, name):
    from src.core.factory import ApplicationFactory
    from src.p2p.crypto import NodeCryptoManager

    crypto = NodeCryptoManager(key_path=tmp_path / f"{name}.key")
    return ApplicationFactory.create_kernel(
        db_path=str(tmp_path / f"{name}.db"), load_default_connectors=False, crypto=crypto
    )


def test_kernel_registers_collaboration_service_and_runs_join_flow_headless(tmp_path, monkeypatch):
    """Sin GUI: invitar -> unirse (stub) -> ingerir -> aprobar, solo con el servicio del kernel."""
    monkeypatch.setenv("MCP2P_NODE_UUID", "node_headless")
    owner = _kernel_with_identity(tmp_path, "owner")
    joiner = _kernel_with_identity(tmp_path, "joiner")
    assert isinstance(owner.collaboration_service, ProjectCollaborationService)
    assert owner.collaboration is owner.collaboration_service

    proj = owner.db.create_project(name="Headless", base_path=str(tmp_path / "ws"), init_git=False)
    invite = owner.collaboration_service.create_invite(proj)
    assert invite["success"] is True and invite["payload"]["sender_github"] == ""

    joined = joiner.collaboration_service.join_project(invite["token"])
    assert joined["status"] == "pending_approval"
    stub = joiner.db.get_project_by_uuid(proj["project_uuid"])
    assert stub["join_status"] == "pending_approval"
    assert Path(stub["base_path"]).parent == joiner.db.db_path.parent / "workspace"

    ingested = owner.collaboration_service.ingest_join_request(joined["request_code"], proj["project_uuid"])
    assert ingested["status"] == "pending_approval"
    node = ingested["collaborator"]["node_uuid"]
    # Sin proyecto activo inyectado, ingerir sin project_uuid falla igual que en la GUI.
    assert owner.collaboration_service.ingest_join_request(joined["request_code"]) == {
        "success": False, "error": "No hay un proyecto activo para procesar la solicitud de unión",
    }
    assert owner.collaboration_service.approve_collaborator(proj["project_uuid"], node)["success"] is True
    listed = {c["node_uuid"]: c for c in owner.collaboration_service.list_collaborators(proj["project_uuid"])}
    assert listed[node]["status"] == "approved" and listed[node]["locked_until"] is None


def test_rebind_rejects_unknown_collaborators(tmp_path):
    svc = _kernel_with_identity(tmp_path, "solo").collaboration_service
    clone = svc.rebind(live_transport="t")
    assert clone.live_transport == "t" and svc.live_transport is None
    import pytest

    with pytest.raises(AttributeError):
        svc.rebind(no_existe=1)


def test_collab_ui_adapter_no_longer_touches_collaboration_persistence_directly():
    src = (ROOT / "src" / "app" / "ui_adapters" / "collab_mesh_ui.py").read_text(encoding="utf-8")
    moved = (
        "add_or_request_collaborator", "record_join_attempt", "get_join_rate_limit_state",
        "upsert_pending_join_stub", "activate_joined_project", "register_or_update_node",
        "record_rejection", "get_collaborator", "get_unresolved_or_approved_collaborator_by_pubkey",
        "get_project_collaborators", "git_clone", "accept_pending_invitations",
        "create_signed_invite_token", "decode_and_verify_invite_token",
    )
    offenders = [m for m in moved if re.search(rf"\.{m}\(", src)]
    assert offenders == []


def test_collaboration_service_does_not_depend_on_app_layer():
    import ast

    tree = ast.parse((ROOT / "src" / "core" / "services" / "collaboration_service.py").read_text(encoding="utf-8"))
    mods = [n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module]
    mods += [a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names]
    assert not [m for m in mods if m.startswith("src.app") or m == "subprocess"]
