import json
from pathlib import Path
import pytest

from src.storage.okf_adapter import OKFAdapter, DEFAULT_OKF_SCHEMA


@pytest.fixture
def temp_gov_repo(tmp_path: Path):
    gov_dir = tmp_path / "test_gov_repo"
    gov_dir.mkdir(parents=True, exist_ok=True)
    return gov_dir


def test_initialize_project_okf(temp_gov_repo: Path):
    adapter = OKFAdapter()
    docs_okf = adapter.initialize_project_okf(
        gov_base_path=temp_gov_repo,
        project_name="Proyecto Test OKF",
        project_uuid="uuid-okf-123"
    )

    assert (docs_okf / "01_ARQUITECTURA_Y_GOBERNANZA.md").is_file()
    assert (docs_okf / "02_MAPA_DE_CODIGO_Y_FEATURES.md").is_file()
    assert (docs_okf / "backend").is_dir()
    assert (docs_okf / "frontend").is_dir()
    assert (docs_okf / "okf_schema.json").is_file()


def test_validate_okf_integrity_valid(temp_gov_repo: Path):
    adapter = OKFAdapter()
    adapter.initialize_project_okf(
        gov_base_path=temp_gov_repo,
        project_name="Proyecto Test",
        project_uuid="uuid-123"
    )

    report = adapter.validate_okf_integrity(temp_gov_repo)
    assert report["valid"] is True
    assert len(report["errors"]) == 0
    assert report["stats"]["adrs_found"] >= 1
    assert report["stats"]["user_stories_found"] >= 1


def test_validate_okf_integrity_detects_missing_directory(temp_gov_repo: Path):
    adapter = OKFAdapter()
    adapter.initialize_project_okf(
        gov_base_path=temp_gov_repo,
        project_name="Proyecto Test",
        project_uuid="uuid-123"
    )

    # Eliminar directorio frontend para inducir error
    frontend_dir = temp_gov_repo / "docs" / "okf" / "frontend"
    frontend_dir.rmdir()

    report = adapter.validate_okf_integrity(temp_gov_repo)
    assert report["valid"] is False
    assert any("Directorio obligatorio ausente: docs/okf/frontend" in err for err in report["errors"])


def test_append_adr(temp_gov_repo: Path):
    adapter = OKFAdapter()
    adapter.initialize_project_okf(
        gov_base_path=temp_gov_repo,
        project_name="Proyecto ADR",
        project_uuid="uuid-adr"
    )

    file_01 = adapter.append_adr(
        gov_base_path=temp_gov_repo,
        adr_number=2,
        title="Canal Binario A2A MessagePack",
        context="Reducción de consumo de tokens",
        decision="Adoptar MessagePack a 0 tokens",
        consequences="Transferencia ultrarrápida"
    )

    content = file_01.read_text(encoding="utf-8")
    assert "### ADR-002: Canal Binario A2A MessagePack" in content
    assert "* **Contexto**: Reducción de consumo de tokens" in content

    report = adapter.validate_okf_integrity(temp_gov_repo)
    assert report["valid"] is True
    assert report["stats"]["adrs_found"] == 2


def test_create_feature_doc(temp_gov_repo: Path):
    adapter = OKFAdapter()
    adapter.initialize_project_okf(
        gov_base_path=temp_gov_repo,
        project_name="Proyecto Features",
        project_uuid="uuid-feat"
    )

    doc_path = adapter.create_feature_doc(
        gov_base_path=temp_gov_repo,
        area="backend",
        feature_slug="telemetry_bus",
        title="Bus de Telemetría",
        content="Detalles de implementación del bus de telemetría."
    )

    assert doc_path.is_file()
    assert "Ficha Técnica: Bus de Telemetría" in doc_path.read_text(encoding="utf-8")

    report = adapter.validate_okf_integrity(temp_gov_repo)
    assert report["valid"] is True
    assert report["stats"]["backend_features"] == 1


def test_orphan_memory_snapshot_read_and_write(temp_gov_repo: Path):
    adapter = OKFAdapter()
    project_uuid = "p-orphan-999"

    snapshot_data = {
        "project_uuid": project_uuid,
        "active_developer": "Alice",
        "last_decision": "ADR-005 implementado",
        "pending_tasks": ["Tarea 1", "Tarea 2"]
    }

    result = adapter.write_orphan_memory_snapshot(
        gov_base_path=temp_gov_repo,
        project_uuid=project_uuid,
        snapshot_data=snapshot_data
    )

    assert result["branch_name"] == f"orphan-conversa-memory-{project_uuid}"
    assert Path(result["snapshot_file"]).is_file()

    read_back = adapter.read_orphan_memory_snapshot(
        gov_base_path=temp_gov_repo,
        project_uuid=project_uuid
    )

    assert read_back is not None
    assert read_back["active_developer"] == "Alice"
    assert read_back["last_decision"] == "ADR-005 implementado"
