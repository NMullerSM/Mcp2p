"""
Resolucion y lanzamiento de ejecutables en Windows (puro: usa `ntpath` y predicados inyectables, de modo que
se prueba en Linux con rutas de Windows simuladas).

- `resolve_windows_executable`: busqueda al estilo `CreateProcess`/`PATHEXT` SIN el directorio actual
  (`claude` -> `claude.exe` / `claude.cmd`; los `.ps1` de npm solo como ultimo recurso).
- `build_windows_launch`: convierte un argv en `(appname, cmdline)` para `winpty.PTY.spawn`. Los `.exe`/`.com` se
  lanzan directos; los `.cmd`/`.bat` (shims de npm) a traves de `cmd.exe /d /s /c "..."`, rechazando los
  metacaracteres de cmd (`& | < > ^ % !`) para que un argumento no pueda inyectar comandos; los `.ps1` a traves
  de `powershell.exe -File` con la politica de ejecucion acotada a ese proceso.
- `build_env_block`: bloque de entorno `K=V\\0K=V\\0` (ordenado sin distinguir mayusculas, como exige Windows).
"""

from __future__ import annotations

import ntpath
import os
import subprocess
from dataclasses import dataclass
from typing import Callable, Dict, List, Mapping, Optional, Sequence

DEFAULT_PATHEXT = ".COM;.EXE;.BAT;.CMD"
_DIRECT_EXTS = (".exe", ".com")
_CMD_EXTS = (".cmd", ".bat")
_PS1_EXT = ".ps1"
# Metacaracteres de cmd.exe que NO se admiten en argumentos de un .cmd/.bat (ni entre comillas: `%` se expande
# incluso ahi y los demas se rechazan por conservadurismo).
CMD_UNSAFE_CHARS = frozenset("&|<>^%!\r\n\x00")


class WindowsLaunchError(ValueError):
    """El argv no se puede lanzar de forma segura en Windows."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def pathext_extensions(environ: Optional[Mapping[str, str]] = None) -> List[str]:
    """Extensiones ejecutables en minusculas, en el orden de `PATHEXT` (con un valor por defecto razonable)."""
    env = os.environ if environ is None else environ
    raw = ""
    for key, value in env.items():
        if key.upper() == "PATHEXT":
            raw = value
            break
    exts = [e.strip().lower() for e in (raw or DEFAULT_PATHEXT).split(";") if e.strip().startswith(".")]
    return exts or [e.lower() for e in DEFAULT_PATHEXT.split(";")]


def _env_path_dirs(environ: Mapping[str, str]) -> List[str]:
    raw = ""
    for key, value in environ.items():
        if key.upper() == "PATH":
            raw = value
            break
    return [d.strip().strip('"') for d in raw.split(";") if d.strip()]


def resolve_windows_executable(
    binary: str,
    *,
    cwd: Optional[str] = None,
    environ: Optional[Mapping[str, str]] = None,
    is_file: Callable[[str], bool] = os.path.isfile,
) -> Optional[str]:
    """Ruta completa del ejecutable `binary` o None. No busca en el directorio actual salvo ruta relativa explicita."""
    env = os.environ if environ is None else environ
    if not binary or any(ch in binary for ch in '*?"<>|\x00'):
        return None
    exts = pathext_extensions(env)
    ordered = [e for e in exts if e != _PS1_EXT] + [_PS1_EXT]  # .ps1 solo si no hay nada mejor
    known = set(ordered) | set(_DIRECT_EXTS) | set(_CMD_EXTS)
    base_ext = ntpath.splitext(binary)[1].lower()

    def _try_in(directory: Optional[str]) -> Optional[str]:
        stem = ntpath.join(directory, binary) if directory else binary
        if base_ext in known and is_file(stem):
            return stem
        for ext in ordered:
            candidate = stem + ext
            if is_file(candidate):
                return candidate
        return None

    has_dir = ("\\" in binary) or ("/" in binary) or ntpath.splitdrive(binary)[0] != ""
    if has_dir:
        if ntpath.isabs(binary) or ntpath.splitdrive(binary)[0]:
            return _try_in(None)
        return _try_in(cwd) if cwd else None

    for directory in _env_path_dirs(env):
        hit = _try_in(directory)
        if hit:
            return hit
    return None


@dataclass(frozen=True)
class WindowsLaunch:
    appname: str
    cmdline: Optional[str]
    kind: str  # "exe" | "cmd" | "ps1"

    @property
    def display(self) -> List[str]:
        return [self.appname, *([self.cmdline] if self.cmdline else [])]


def classify_executable(path: str) -> str:
    ext = ntpath.splitext(path)[1].lower()
    if ext in _CMD_EXTS:
        return "cmd"
    if ext == _PS1_EXT:
        return "ps1"
    return "exe"


def _quote_cmd_arg(arg: str) -> str:
    bad = sorted({c for c in arg if c in CMD_UNSAFE_CHARS})
    if bad:
        shown = "".join(c if c.isprintable() else repr(c)[1:-1] for c in bad)
        raise WindowsLaunchError(
            "CLI_ARG_UNSAFE",
            f"Argumento no admitido para un script .cmd/.bat (contiene {shown!r}): {arg[:80]!r}",
        )
    return subprocess.list2cmdline([arg])


def build_windows_launch(
    argv: Sequence[str],
    *,
    comspec: Optional[str] = None,
    powershell: str = "powershell.exe",
) -> WindowsLaunch:
    """`argv[0]` ya debe ser la ruta completa resuelta (ver `resolve_windows_executable`)."""
    if not argv or not argv[0]:
        raise WindowsLaunchError("CLI_BINARY_NOT_FOUND", "argv vacio.")
    exe = str(argv[0])
    args = [str(a) for a in argv[1:]]
    for a in [exe, *args]:
        if "\x00" in a:
            raise WindowsLaunchError("CLI_ARG_UNSAFE", "Argumento con NUL.")
    kind = classify_executable(exe)
    if kind == "exe":
        return WindowsLaunch(exe, subprocess.list2cmdline(args) if args else None, "exe")
    if kind == "cmd":
        shell = comspec or os.environ.get("COMSPEC") or "cmd.exe"
        inner = " ".join([_quote_cmd_arg(exe), *(_quote_cmd_arg(a) for a in args)])
        # `/s` quita solo las comillas externas; `/d` ignora AutoRun del registro.
        return WindowsLaunch(shell, f'/d /s /c "{inner}"', "cmd")
    ps_args = ["-NoLogo", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", exe, *args]
    return WindowsLaunch(powershell, subprocess.list2cmdline(ps_args), "ps1")


def build_env_block(env: Mapping[str, str]) -> str:
    """Bloque de entorno para `PTY.spawn(env=...)`: `K=V\\0` por entrada, ordenado sin distinguir mayusculas."""
    items: Dict[str, str] = {}
    for key, value in env.items():
        k, v = str(key), str(value)
        if not k or "=" in k or "\x00" in k or "\x00" in v:
            continue
        items[k] = v
    ordered = sorted(items.items(), key=lambda kv: kv[0].upper())
    return "".join(f"{k}={v}\x00" for k, v in ordered)
