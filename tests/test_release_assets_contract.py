"""
Contrato entre la publicacion (workflow + instalador) y el actualizador: si los nombres de los archivos de la
release cambian, `pickUpdateAsset` (UpdateModal.jsx) y la verificacion SHA256SUMS dejan de encontrarlos y el boton
Actualizar deja de funcionar sin que nada lo avise. Estas pruebas fijan ese contrato.
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = (ROOT / ".github" / "workflows" / "build-and-release.yml").read_text(encoding="utf-8")
ISS = (ROOT / "ejecutables" / "windows" / "installer" / "mcp2p_setup.iss").read_text(encoding="utf-8")
MODAL = (ROOT / "frontend" / "src" / "components" / "UpdateModal.jsx").read_text(encoding="utf-8")


def test_windows_assets_match_what_the_modal_picks():
    # pickUpdateAsset: instalador = cualquier .exe; portable = nombre con "windows" y .zip
    assert "OutputBaseFilename=Mcp2p-Setup-v{#MyAppVersion}" in ISS
    assert "Mcp2p-Setup-*.exe" in WORKFLOW
    assert "Mcp2p-Windows-Portable.zip" in WORKFLOW
    assert "endsWith('.exe')" in MODAL and "includes('windows')" in MODAL


def test_linux_assets_match_what_the_modal_picks():
    # pickUpdateAsset: .deb y .tar.gz cuyo nombre incluye "linux"
    assert "mcp2p_*_amd64.deb" in WORKFLOW
    assert "Mcp2p-Linux-Portable.tar.gz" in WORKFLOW
    assert "endsWith('.deb')" in MODAL and "includes('linux')" in MODAL


def test_release_publishes_checksums_of_every_asset_and_marks_latest():
    # El actualizador pide SHA256SUMS junto al binario y la app consulta releases/latest.
    assert "sha256sum * > SHA256SUMS" in WORKFLOW
    assert "files: release_assets/*" in WORKFLOW
    assert "make_latest: true" in WORKFLOW
    assert "prerelease: false" in WORKFLOW and "draft: false" in WORKFLOW


def test_release_only_publishes_after_both_builds_pass():
    assert "needs: [build-windows, build-linux]" in WORKFLOW
