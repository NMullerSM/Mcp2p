"""El CHANGELOG cuenta cambios para el usuario y cada version publicada tiene su seccion."""
from __future__ import annotations

import importlib.util
import re
from pathlib import Path

from src.app.version import __version__

ROOT = Path(__file__).resolve().parents[1]
CHANGELOG = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")


def _load_script():
    spec = importlib.util.spec_from_file_location("changelog_section", ROOT / "scripts" / "changelog_section.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_current_version_has_release_notes():
    section = _load_script().extract_section(CHANGELOG, __version__)
    assert section, f"Falta la seccion '## [{__version__}]' en CHANGELOG.md (la release la usa como cuerpo)."


def test_extract_section_stops_at_next_version_and_link_definitions():
    text = "# C\n\n## [2.0.0] - x\n\n### Añadido\n- a\n\n## [1.0.0] - y\n- b\n\n[2.0.0]: https://x/y\n"
    mod = _load_script()
    assert mod.extract_section(text, "2.0.0") == "### Añadido\n- a"
    assert mod.extract_section(text, "1.0.0") == "- b"
    assert mod.extract_section(text, "9.9.9") is None


def test_changelog_keeps_user_facing_language():
    # Los codigos internos de auditoria y las rutas de codigo no pertenecen a las notas de version.
    notes = CHANGELOG[CHANGELOG.index("\n## ["):]  # el preambulo si puede nombrar archivos de versionado
    assert not re.search(r"\b(EGM|NEW)-\d+", notes)
    assert not re.search(r"\b(src|tests)/\w", notes)
    assert ".py`" not in notes


def test_every_released_version_links_to_its_commits():
    for ver in re.findall(r"^## \[(\d+\.\d+\.\d+)\]", CHANGELOG, re.MULTILINE):
        assert re.search(rf"^\[{re.escape(ver)}\]: https://github\.com/", CHANGELOG, re.MULTILINE), ver
