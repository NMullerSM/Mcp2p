"""EGM-55: `src/storage/db.py` sin dependencia invertida ni efectos de archivos/git propios.

1. Guarda AST: `db.py` no importa la capa `app`, ni `OKFAdapter`, ni `subprocess`/`proc_utils`,
   ni contiene comandos git.
2. Caracterización: un `DatabaseManager(db_path)` construido a secas (como hacen tools.py,
   ui_adapters y muchos tests) conserva los efectos históricos: `create_project` andamia OKF y el
   doble repo git, y toda mutación de tarjeta escribe su YAML y lo comitea.
3. Inyección: con colaboradores propios, `db.py` no toca OKF ni git.
"""

from __future__ import annotations

import ast
import subprocess
from pathlib import Path

import pytest

from src.storage.db import DatabaseManager

DB_SOURCE = Path(__file__).resolve().parent.parent / "src" / "storage" / "db.py"

FORBIDDEN_MODULE_PREFIXES = ("src.app", "src.storage.okf_adapter", "subprocess", "src.proc_utils")


def _git_env(monkeypatch):
    monkeypatch.setenv("GIT_AUTHOR_NAME", "MCP2P Test")
    monkeypatch.setenv("GIT_AUTHOR_EMAIL", "test@mcp2p.local")
    monkeypatch.setenv("GIT_COMMITTER_NAME", "MCP2P Test")
    monkeypatch.setenv("GIT_COMMITTER_EMAIL", "test@mcp2p.local")


def _git_log(repo: Path) -> str:
    return subprocess.run(
        ["git", "log", "--format=%s", "-n", "20"], cwd=repo, capture_output=True, text=True
    ).stdout


def test_db_module_has_no_app_okf_or_git_dependency():
    tree = ast.parse(DB_SOURCE.read_text(encoding="utf-8"))
    offending = []
    for node in ast.walk(tree):
        modules = []
        if isinstance(node, ast.Import):
            modules = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules = [node.module]
        for mod in modules:
            if any(mod == p or mod.startswith(p + ".") for p in FORBIDDEN_MODULE_PREFIXES):
                offending.append((node.lineno, mod))
        if isinstance(node, (ast.List, ast.Tuple)) and node.elts:
            first = node.elts[0]
            if isinstance(first, ast.Constant) and first.value == "git":
                offending.append((node.lineno, "comando git"))
        if isinstance(node, ast.Attribute) and node.attr in {"system", "popen", "Popen", "OKFAdapter"}:
            offending.append((node.lineno, node.attr))
    assert offending == [], offending


def test_bare_database_manager_still_scaffolds_repos_and_projects_cards(tmp_path, monkeypatch):
    _git_env(monkeypatch)
    db = DatabaseManager(str(tmp_path / "bare.db"))
    proj = db.create_project(name="Bare", base_path=str(tmp_path / "ws"), init_git=True)
    app_dir, gov_dir = Path(proj["code_repo_path"]), Path(proj["gov_repo_path"])

    # Andamiaje: OKF estándar + doble repo git + rama huérfana en worktree + hooks.
    assert (gov_dir / ".okf_project.json").exists()
    assert (app_dir / ".git").is_dir() and (gov_dir / ".git").is_dir()
    assert (app_dir / "README.md").exists()
    assert (gov_dir / ".git" / "orphan_worktree" / "README_CONVERSA.md").exists()
    assert (gov_dir / ".git" / "hooks" / "post-commit").exists()
    assert "chore(init): initialize bare development workspace" in _git_log(app_dir)
    assert f"init(okf): initialize governance master branch for {proj['project_uuid']}" in _git_log(gov_dir)
    assert len(list(gov_dir.rglob("*.yaml"))) + len(list(gov_dir.rglob("*.md"))) > 1

    # Proyección: crear y mover una tarjeta escribe su YAML y lo comitea.
    db.create_card(proj["project_uuid"], "CARD-EGM55-01", "Titulo EGM55", description="Cuerpo")
    yaml_files = list(gov_dir.rglob("CARD-EGM55-01*.yaml"))
    assert yaml_files, "create_card dejó de proyectar el YAML OKF"
    assert "sync(kanban): CARD-EGM55-01 [backlog] Titulo EGM55" in _git_log(gov_dir)
    db.update_card_status(proj["project_uuid"], "CARD-EGM55-01", "in_progress")
    assert "in_progress" in yaml_files[0].read_text(encoding="utf-8")
    assert "sync(kanban): CARD-EGM55-01 [in_progress] Titulo EGM55" in _git_log(gov_dir)


def test_injected_collaborators_replace_every_disk_effect(tmp_path, monkeypatch):
    from src.storage import okf_adapter as okf_mod

    def _forbidden(*_a, **_k):
        raise AssertionError("db.py no debe construir OKFAdapter con colaboradores inyectados")

    monkeypatch.setattr(okf_mod, "OKFAdapter", _forbidden)

    calls = {"provision": [], "project": []}

    class SpyProvisioner:
        def provision_project(self, **kwargs):
            calls["provision"].append(kwargs)

    class SpyProjector:
        def project_card(self, gov_repo_path, card):
            calls["project"].append((Path(gov_repo_path), card["card_id"], card["status"]))

    db = DatabaseManager(
        str(tmp_path / "spy.db"), card_projector=SpyProjector(), project_provisioner=SpyProvisioner()
    )
    proj = db.create_project(name="Spy", base_path=str(tmp_path / "ws"), init_git=True)
    gov_dir = Path(proj["gov_repo_path"])

    seed, created = calls["provision"]
    assert seed["init_git"] is False and seed["name"] == "Proyecto Semilla"
    assert created == {
        "name": "Spy",
        "project_uuid": proj["project_uuid"],
        "clean_name": "spy",
        "app_dir": Path(proj["code_repo_path"]),
        "gov_dir": gov_dir,
        "init_git": True,
    }
    assert not (gov_dir / ".git").exists() and not any(gov_dir.iterdir())

    db.create_card(proj["project_uuid"], "CARD-SPY-01", "T")
    db.update_card_status(proj["project_uuid"], "CARD-SPY-01", "in_progress")
    assert calls["project"] == [
        (gov_dir, "CARD-SPY-01", "backlog"),
        (gov_dir, "CARD-SPY-01", "in_progress"),
    ]
    assert not any(gov_dir.iterdir())


def test_projection_failure_is_logged_and_sql_write_survives(tmp_path, caplog):
    class Boom:
        def project_card(self, gov_repo_path, card):
            raise OSError("proyector caído")

    from src.core.services.project_provisioning_service import NullWorkspaceEffects

    db = DatabaseManager(
        str(tmp_path / "boom.db"), card_projector=Boom(), project_provisioner=NullWorkspaceEffects()
    )
    proj = db.create_project(name="Boom", base_path=str(tmp_path / "ws"))
    caplog.set_level("WARNING", logger="mcp2p.storage.db")
    card = db.create_card(proj["project_uuid"], "CARD-BOOM-01", "T")
    assert card["card_id"] == "CARD-BOOM-01"
    assert db.get_card_by_id(proj["project_uuid"], "CARD-BOOM-01") is not None
    assert any("proyector caído" in r.getMessage() for r in caplog.records)


def test_factory_wires_workspace_effects_explicitly(tmp_path):
    from src.core.factory import ApplicationFactory
    from src.core.ports import CardProjectionPort, GitRepositoryScaffoldPort, ProjectProvisioningPort
    from src.core.services.project_provisioning_service import OkfCardProjector, ProjectProvisioningService
    from src.adapters.provisioning.git_scaffolder import SubprocessGitRepoScaffolder

    kernel = ApplicationFactory.create_kernel(
        db_path=str(tmp_path / "factory.db"), load_default_connectors=False
    )
    assert isinstance(kernel.db._card_projector, OkfCardProjector)
    assert isinstance(kernel.db._project_provisioner, ProjectProvisioningService)
    assert isinstance(kernel.db._card_projector, CardProjectionPort)
    assert isinstance(kernel.db._project_provisioner, ProjectProvisioningPort)
    assert isinstance(SubprocessGitRepoScaffolder(), GitRepositoryScaffoldPort)


def test_app_identity_and_paths_remain_compatible_reexports():
    import src.app.identity as app_identity
    import src.app.paths as app_paths
    import src.runtime.identity as rt_identity
    import src.runtime.paths as rt_paths

    assert app_identity.local_node_uuid is rt_identity.local_node_uuid
    for name in ("get_bundle_dir", "get_default_db_path", "get_user_data_dir", "is_system_installed"):
        assert getattr(app_paths, name) is getattr(rt_paths, name)
    assert Path(rt_paths.get_bundle_dir()) == Path(__file__).resolve().parent.parent


def test_okf_holder_requires_exactly_one_source():
    from src.core.services.project_provisioning_service import OkfCardProjector

    with pytest.raises(ValueError):
        OkfCardProjector()
    with pytest.raises(ValueError):
        OkfCardProjector(object(), okf_factory=object)
