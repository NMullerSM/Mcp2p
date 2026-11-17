"""
Imprime las notas de una version tomadas de CHANGELOG.md (sin el titulo de la seccion).

Uso: python scripts/changelog_section.py [VERSION]
Sin VERSION usa la de src/app/version.py. Sale con codigo 1 si CHANGELOG.md no tiene una seccion `## [VERSION]`
con contenido, para que la publicacion falle antes de crear una release sin notas.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
CHANGELOG = ROOT_DIR / "CHANGELOG.md"


def extract_section(text: str, version: str) -> str | None:
    """Devuelve el cuerpo de la seccion `## [version]` o None si no existe o esta vacia."""
    heading = re.compile(rf"^## \[{re.escape(version)}\]", re.MULTILINE)
    start = heading.search(text)
    if not start:
        return None
    rest = text[start.end():]
    body_start = rest.find("\n")
    body = rest[body_start + 1:] if body_start != -1 else ""
    nxt = re.search(r"^(## \[|\[[^\]]+\]: https?://)", body, re.MULTILINE)
    if nxt:
        body = body[: nxt.start()]
    body = body.strip()
    return body or None


def main() -> int:
    if len(sys.argv) > 1:
        version = sys.argv[1].lstrip("v")
    else:
        sys.path.insert(0, str(ROOT_DIR))
        from src.app.version import __version__ as version
    section = extract_section(CHANGELOG.read_text(encoding="utf-8"), version)
    if section is None:
        print(f"ERROR: CHANGELOG.md no tiene una seccion '## [{version}]' con contenido.", file=sys.stderr)
        return 1
    print(section)
    return 0


if __name__ == "__main__":
    sys.exit(main())
