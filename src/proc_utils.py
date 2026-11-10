"""
Utilidades de ejecución de procesos sin consola visible para entornos GUI en Windows.
Evita el parpadeo de ventanas fugaces de consola (conhost) al invocar git, gh u otros binarios.
"""

from __future__ import annotations

import platform
import subprocess
from typing import Any, Dict

_CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)


def no_window_kwargs() -> Dict[str, Any]:
    """kwargs para subprocess.* que evitan abrir una consola visible en Windows. No-op en otros SO."""
    if platform.system() != "Windows":
        return {}
    res: Dict[str, Any] = {"creationflags": _CREATE_NO_WINDOW}
    startupinfo_cls = getattr(subprocess, "STARTUPINFO", None)
    if startupinfo_cls is not None:
        si = startupinfo_cls()
        flags = getattr(subprocess, "STARTF_USESHOWWINDOW", 1)
        si.dwFlags = getattr(si, "dwFlags", 0) | flags
        si.wShowWindow = 0  # SW_HIDE
        res["startupinfo"] = si
    return res


def run_hidden(args: Any, **kw: Any) -> subprocess.CompletedProcess:
    """Ejecuta subprocess.run garantizando que no se abra consola visible en Windows."""
    base = no_window_kwargs()
    if "creationflags" in kw and "creationflags" in base:
        kw["creationflags"] |= base["creationflags"]
    if "startupinfo" in kw and "startupinfo" in base:
        kw["startupinfo"].dwFlags |= base["startupinfo"].dwFlags
        kw["startupinfo"].wShowWindow = base["startupinfo"].wShowWindow
    else:
        for k, v in base.items():
            if k not in kw:
                kw[k] = v
    return subprocess.run(args, **kw)


def check_output_hidden(args: Any, **kw: Any) -> bytes | str:
    """Ejecuta subprocess.check_output garantizando que no se abra consola visible en Windows."""
    base = no_window_kwargs()
    if "creationflags" in kw and "creationflags" in base:
        kw["creationflags"] |= base["creationflags"]
    if "startupinfo" in kw and "startupinfo" in base:
        kw["startupinfo"].dwFlags |= base["startupinfo"].dwFlags
        kw["startupinfo"].wShowWindow = base["startupinfo"].wShowWindow
    else:
        for k, v in base.items():
            if k not in kw:
                kw[k] = v
    return subprocess.check_output(args, **kw)


def Popen_hidden(args: Any, **kw: Any) -> subprocess.Popen:
    """Ejecuta subprocess.Popen garantizando que no se abra consola visible en Windows."""
    base = no_window_kwargs()
    if "creationflags" in kw and "creationflags" in base:
        kw["creationflags"] |= base["creationflags"]
    if "startupinfo" in kw and "startupinfo" in base:
        kw["startupinfo"].dwFlags |= base["startupinfo"].dwFlags
        kw["startupinfo"].wShowWindow = base["startupinfo"].wShowWindow
    else:
        for k, v in base.items():
            if k not in kw:
                kw[k] = v
    return subprocess.Popen(args, **kw)
