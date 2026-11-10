#!/usr/bin/env python3
"""Sincroniza (por defecto) o verifica (--check) todas las copias de la versión de MCP2P
contra la fuente única de verdad src/app/version.py. --check sale con 1 si hay desincronía."""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VERSION_PY = ROOT / "src" / "app" / "version.py"
PYPROJECT = ROOT / "pyproject.toml"
VERSION_JS = ROOT / "frontend" / "src" / "version.js"
PACKAGE_JSON = ROOT / "frontend" / "package.json"
PACKAGE_LOCK = ROOT / "frontend" / "package-lock.json"
SEMVER_RE = re.compile(r"\d+\.\d+\.\d+")


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def _write(p: Path, text: str) -> None:
    p.write_text(text, encoding="utf-8", newline="\n")


def source_version() -> str:
    m = re.search(r'^__version__\s*=\s*["\']([^"\']+)["\']', _read(VERSION_PY), re.M)
    if not m or not SEMVER_RE.fullmatch(m.group(1)):
        sys.exit(f"ERROR: __version__ ausente o no SemVer en {VERSION_PY}")
    return m.group(1)


def current() -> dict:
    out = {}
    m = re.search(r'^version\s*=\s*"([^"]+)"', _read(PYPROJECT), re.M)
    out["pyproject.toml"] = m.group(1) if m else None
    m = re.search(r'APP_VERSION\s*=\s*"([^"]+)"', _read(VERSION_JS))
    out["frontend/src/version.js"] = m.group(1) if m else None
    out["frontend/package.json"] = json.loads(_read(PACKAGE_JSON)).get("version")
    if PACKAGE_LOCK.exists():
        lock = json.loads(_read(PACKAGE_LOCK))
        out["frontend/package-lock.json"] = lock.get("version")
        out['frontend/package-lock.json packages[""]'] = lock.get("packages", {}).get("", {}).get("version")
    return out


def sync(v: str) -> None:
    _write(PYPROJECT, re.sub(r'^(version\s*=\s*)"[^"]+"', lambda m: f'{m.group(1)}"{v}"', _read(PYPROJECT), count=1, flags=re.M))
    _write(VERSION_JS, re.sub(r'(APP_VERSION\s*=\s*)"[^"]+"', lambda m: f'{m.group(1)}"{v}"', _read(VERSION_JS), count=1))
    pkg = json.loads(_read(PACKAGE_JSON))
    pkg["version"] = v
    _write(PACKAGE_JSON, json.dumps(pkg, indent=2, ensure_ascii=False) + "\n")
    if PACKAGE_LOCK.exists():
        lock = json.loads(_read(PACKAGE_LOCK))
        lock["version"] = v
        if "" in lock.get("packages", {}):
            lock["packages"][""]["version"] = v
        _write(PACKAGE_LOCK, json.dumps(lock, indent=2, ensure_ascii=False) + "\n")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="solo verificar; exit 1 si hay desincronía")
    args = ap.parse_args()
    v = source_version()
    if not args.check:
        sync(v)
    bad = {k: val for k, val in current().items() if val != v}
    if bad:
        for k, val in bad.items():
            print(f"DESINCRONIZADO: {k} = {val!r} (esperado {v!r} según src/app/version.py)", file=sys.stderr)
        return 1
    print(f"OK: todas las copias en {v}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
