"""
EGM-53 (defensa en profundidad): el JS del webview es un principal de confianza total
(PyWebView expone recursivamente `ApiBridge`), así que cualquier vía de inyección de script
en el frontend equivale a acceso al proceso Python. Estas guardas fijan dos garantías:

1. `frontend/src/**` no renderiza HTML crudo ni evalúa cadenas como código.
2. `frontend/index.html` declara una CSP sin `unsafe-inline` en scripts. `unsafe-eval` solo se admite en
   `script-src`: PyWebView crea cada función de `window.pywebview.api` con `new Function`
   (`webview/js/api.js`) y sin ello la API queda vacía y la GUI no carga proyectos ni PAT.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "frontend"

FORBIDDEN_PATTERNS = {
    "dangerouslySetInnerHTML": re.compile(r"dangerouslySetInnerHTML"),
    "rehype-raw": re.compile(r"rehype-raw"),
    "innerHTML/outerHTML assignment": re.compile(r"\.(inner|outer)HTML\s*="),
    "insertAdjacentHTML": re.compile(r"insertAdjacentHTML"),
    "document.write": re.compile(r"document\.write"),
    "eval(": re.compile(r"(?<![\w.])eval\s*\("),
    "new Function(": re.compile(r"new\s+Function\s*\("),
}


def _source_files():
    for ext in ("*.js", "*.jsx", "*.ts", "*.tsx", "*.mjs"):
        yield from (FRONTEND / "src").rglob(ext)


def test_frontend_sources_never_render_raw_html_or_eval():
    offenders = []
    for path in _source_files():
        text = path.read_text(encoding="utf-8", errors="replace")
        for label, rx in FORBIDDEN_PATTERNS.items():
            if rx.search(text):
                offenders.append(f"{path.relative_to(ROOT)}: {label}")
    assert not offenders, "Vías de inyección de HTML/JS prohibidas (EGM-53):\n" + "\n".join(offenders)


def test_frontend_dependencies_exclude_raw_html_renderers():
    pkg = (FRONTEND / "package.json").read_text(encoding="utf-8")
    for dep in ("rehype-raw", "html-react-parser", "react-html-parser"):
        assert f'"{dep}"' not in pkg, f"Dependencia de HTML crudo prohibida: {dep}"


def _csp_directives() -> dict[str, list[str]]:
    html = (FRONTEND / "index.html").read_text(encoding="utf-8")
    m = re.search(r'<meta\s+http-equiv="Content-Security-Policy"\s+content="([^"]+)"', html)
    assert m, "frontend/index.html debe declarar una CSP (EGM-53)"
    out: dict[str, list[str]] = {}
    for part in m.group(1).split(";"):
        tokens = part.split()
        if tokens:
            out[tokens[0]] = tokens[1:]
    return out


def test_index_html_declares_strict_csp():
    csp = _csp_directives()
    assert csp.get("default-src") == ["'self'"]
    script_src = csp.get("script-src", csp["default-src"])
    assert script_src == ["'self'", "'unsafe-eval'"], f"script-src inesperado: {script_src}"
    for directive, sources in csp.items():
        if directive != "script-src":
            assert "'unsafe-eval'" not in sources, directive
        if directive != "style-src":
            assert "'unsafe-inline'" not in sources, directive
    assert csp.get("object-src") == ["'none'"]
    assert csp.get("base-uri") == ["'none'"]
