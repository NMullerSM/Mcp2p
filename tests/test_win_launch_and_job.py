"""
Piezas puras de Windows que se verifican en Linux: resolucion PATHEXT, construccion de la linea de comandos,
bloque de entorno, layout de las estructuras del Job Object, su logica con un `kernel32` doble, la resolucion de
CLIs del catalogo con `platform="win32"` y el comando de instalacion por plataforma.
"""

from __future__ import annotations

import ctypes
import os
import sys
from pathlib import Path
from typing import Any, List

import pytest

from src.adapters.terminal import win_job
from src.adapters.terminal.cli_catalog import CliCatalogService
from src.adapters.terminal.cli_install import build_install_command
from src.adapters.terminal.win_job import JobObjectError, WindowsJobObject
from src.adapters.terminal.win_launch import (
    WindowsLaunchError,
    build_env_block,
    build_windows_launch,
    classify_executable,
    pathext_extensions,
    resolve_windows_executable,
)

ENV = {"Path": r"C:\Windows\System32;C:\npm;C:\bin", "PATHEXT": ".COM;.EXE;.BAT;.CMD;.VBS", "COMSPEC": r"C:\Windows\System32\cmd.exe"}


def _fs(*paths: str) -> Any:
    lowered = {p.lower() for p in paths}
    return lambda p: p.lower() in lowered


# -- resolucion PATHEXT ----------------------------------------------------------------------------------


def test_pathext_is_read_case_insensitively_with_a_default() -> None:
    assert pathext_extensions(ENV) == [".com", ".exe", ".bat", ".cmd", ".vbs"]
    assert pathext_extensions({}) == [".com", ".exe", ".bat", ".cmd"]


def test_bare_name_resolves_by_path_order_and_pathext_order() -> None:
    is_file = _fs(r"C:\npm\claude.cmd", r"C:\bin\claude.exe", r"C:\npm\claude.ps1")
    assert resolve_windows_executable("claude", environ=ENV, is_file=is_file) == r"C:\npm\claude.cmd"
    is_file = _fs(r"C:\npm\claude.cmd", r"C:\npm\claude.exe")
    assert resolve_windows_executable("claude", environ=ENV, is_file=is_file) == r"C:\npm\claude.exe"  # .exe antes de .cmd
    assert resolve_windows_executable("claude.cmd", environ=ENV, is_file=_fs(r"C:\npm\claude.cmd")) == r"C:\npm\claude.cmd"


def test_ps1_is_only_the_last_resort_and_the_cwd_is_never_searched() -> None:
    assert resolve_windows_executable("claude", environ=ENV, is_file=_fs(r"C:\npm\claude.ps1")) == r"C:\npm\claude.ps1"
    assert resolve_windows_executable("claude", cwd=r"C:\proyecto", environ=ENV, is_file=_fs(r"C:\proyecto\claude.exe")) is None


def test_explicit_paths_absolute_and_relative_to_cwd_and_garbage() -> None:
    is_file = _fs(r"C:\tools\a b\t.exe", r"C:\proyecto\bin\x.cmd")
    assert resolve_windows_executable(r"C:\tools\a b\t", environ=ENV, is_file=is_file) == r"C:\tools\a b\t.exe"
    assert resolve_windows_executable(r"C:\tools\a b\t.exe", environ=ENV, is_file=is_file) == r"C:\tools\a b\t.exe"
    assert resolve_windows_executable(r"bin\x", cwd=r"C:\proyecto", environ=ENV, is_file=is_file) == r"C:\proyecto\bin\x.cmd"
    assert resolve_windows_executable(r"bin\x", environ=ENV, is_file=is_file) is None
    for bad in ("", "a*b", 'a"b', "a|b", "a\x00b"):
        assert resolve_windows_executable(bad, environ=ENV, is_file=lambda p: True) is None


# -- linea de comandos -------------------------------------------------------------------------------------


def test_exe_is_launched_directly_with_windows_quoting() -> None:
    launch = build_windows_launch([r"C:\a b\t.exe", "--x", "dos palabras", 'con"comilla'])
    assert (launch.appname, launch.kind) == (r"C:\a b\t.exe", "exe")
    assert launch.cmdline == '--x "dos palabras" con\\"comilla'
    assert build_windows_launch([r"C:\a\t.exe"]).cmdline is None


def test_cmd_and_bat_go_through_cmd_exe_with_outer_quotes() -> None:
    launch = build_windows_launch([r"C:\npm\codex.cmd", "-c", 'mcp.url="http://127.0.0.1:1/mcp"', r"C:\Mi Carpeta\f.json"],
                                  comspec=r"C:\Windows\System32\cmd.exe")
    assert launch.kind == "cmd" and launch.appname == r"C:\Windows\System32\cmd.exe"
    assert launch.cmdline == '/d /s /c "C:\\npm\\codex.cmd -c mcp.url=\\"http://127.0.0.1:1/mcp\\" "C:\\Mi Carpeta\\f.json""'
    assert build_windows_launch([r"C:\x\s.bat"]).cmdline == '/d /s /c "C:\\x\\s.bat"'


@pytest.mark.parametrize("bad", ["a&b", "a|b", "a<b", "a>b", "a^b", "%TEMP%", "x!y", "a\nb", "a\rb"])
def test_cmd_scripts_reject_metacharacters_everywhere(bad: str) -> None:
    with pytest.raises(WindowsLaunchError) as err:
        build_windows_launch([r"C:\npm\claude.cmd", "ok", bad])
    assert err.value.code == "CLI_ARG_UNSAFE"
    # un .exe no pasa por cmd.exe: ahi esos caracteres son datos
    if "\n" not in bad and "\r" not in bad:
        assert build_windows_launch([r"C:\x\a.exe", bad]).cmdline is not None


def test_ps1_goes_through_powershell_with_a_process_scoped_policy() -> None:
    launch = build_windows_launch([r"C:\npm\claude.ps1", "--v"], powershell=r"C:\ps\powershell.exe")
    assert launch.kind == "ps1" and launch.appname == r"C:\ps\powershell.exe"
    assert launch.cmdline == "-NoLogo -NoProfile -ExecutionPolicy Bypass -File C:\\npm\\claude.ps1 --v"
    assert classify_executable(r"C:\x\a.CMD") == "cmd" and classify_executable("a.com") == "exe"


def test_empty_or_nul_argv_is_rejected() -> None:
    for argv in ([], [""], [r"C:\a.exe", "x\x00y"]):
        with pytest.raises(WindowsLaunchError):
            build_windows_launch(argv)


def test_env_block_is_sorted_nul_terminated_and_skips_invalid_entries() -> None:
    block = build_env_block({"b": "2", "A": "1", "=C:": r"C:\x", "": "v", "K=V": "x", "N": "a\x00b", "Z": "ñ"})
    assert block == "A=1\x00b=2\x00Z=ñ\x00"


# -- Job Object ----------------------------------------------------------------------------------------------------


def test_job_structures_have_the_win64_layout() -> None:
    if ctypes.sizeof(ctypes.c_size_t) != 8:
        pytest.skip("layout Win64")
    assert ctypes.sizeof(win_job.JOBOBJECT_BASIC_LIMIT_INFORMATION) == 64
    assert ctypes.sizeof(win_job.IO_COUNTERS) == 48
    assert ctypes.sizeof(win_job.JOBOBJECT_EXTENDED_LIMIT_INFORMATION) == 144
    assert win_job.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE == 0x2000


class FakeKernel32:
    def __init__(self, *, create_ok: bool = True, set_ok: bool = True, open_ok: bool = True, assign_ok: bool = True) -> None:
        self.calls: List[tuple] = []
        self.create_ok, self.set_ok, self.open_ok, self.assign_ok = create_ok, set_ok, open_ok, assign_ok

    def last_error(self) -> int:
        return 5

    def create_job(self) -> Any:
        self.calls.append(("create",))
        return 111 if self.create_ok else None

    def set_kill_on_close(self, job: int) -> bool:
        self.calls.append(("kill_on_close", job))
        return self.set_ok

    def open_process(self, pid: int) -> Any:
        self.calls.append(("open", pid))
        return 222 if self.open_ok else None

    def assign(self, job: int, proc: int) -> bool:
        self.calls.append(("assign", job, proc))
        return self.assign_ok

    def terminate(self, job: int, code: int) -> bool:
        self.calls.append(("terminate", job, code))
        return True

    def close_handle(self, handle: int) -> bool:
        self.calls.append(("close", handle))
        return True


def test_job_object_assigns_terminates_and_closes_idempotently() -> None:
    api = FakeKernel32()
    job = WindowsJobObject(api)
    job.assign_pid(4321)
    assert job.assigned_pids == [4321] and job.is_open
    assert job.terminate(7) is True
    job.close()
    job.close()
    assert job.terminate() is False and job.is_open is False
    assert api.calls == [("create",), ("kill_on_close", 111), ("open", 4321), ("assign", 111, 222), ("close", 222),
                         ("terminate", 111, 7), ("close", 111)]
    with pytest.raises(JobObjectError):
        job.assign_pid(1)


@pytest.mark.parametrize("kwargs,fragment", [({"create_ok": False}, "CreateJobObjectW"),
                                             ({"set_ok": False}, "KILL_ON_JOB_CLOSE")])
def test_job_object_creation_failures_raise_and_do_not_leak_handles(kwargs: Any, fragment: str) -> None:
    api = FakeKernel32(**kwargs)
    with pytest.raises(JobObjectError, match=fragment):
        WindowsJobObject(api)
    closes = [c for c in api.calls if c[0] == "close"]
    assert len(closes) == (0 if "create_ok" in kwargs else 1)


def test_job_assign_failures_close_the_process_handle() -> None:
    for kwargs, fragment in (({"open_ok": False}, "OpenProcess"), ({"assign_ok": False}, "AssignProcessToJobObject")):
        api = FakeKernel32(**kwargs)
        job = WindowsJobObject(api)
        with pytest.raises(JobObjectError, match=fragment):
            job.assign_pid(9)
        assert ("close", 222) in api.calls or "open_ok" in kwargs
        assert job.assigned_pids == []


@pytest.mark.skipif(sys.platform == "win32", reason="en Windows existe kernel32")
def test_kernel32_binding_is_unavailable_off_windows() -> None:
    with pytest.raises(JobObjectError):
        WindowsJobObject()


# -- catalogo en Windows ---------------------------------------------------------------------------------------------


def _win_service(tmp_path: Path, **kw: Any) -> CliCatalogService:
    return CliCatalogService(include_user_catalog=False, platform="win32", home=str(tmp_path),
                             environ=kw.pop("environ", {}), which=kw.pop("which", lambda n: None), **kw)


def _touch_exe(path: Path) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("x")
    path.chmod(0o755)
    return str(path)


def test_catalog_resolves_windows_binaries_through_local_bin_variants(tmp_path: Path) -> None:
    exe = _touch_exe(tmp_path / ".local" / "bin" / "claude.exe")
    res = _win_service(tmp_path).resolve("claude")
    assert res.found and res.source == "local_bin" and res.path.replace("\\", "/") == exe.replace("\\", "/")


def test_catalog_resolves_npm_cmd_shims_from_appdata_candidates(tmp_path: Path) -> None:
    appdata = tmp_path / "AppData"
    appdata.mkdir()
    # Los patrones del catalogo usan "\\": en Linux el "archivo" es un unico nombre con barras invertidas.
    cmd = _touch_exe(Path(str(appdata) + "\\npm\\gemini.cmd"))
    res = _win_service(tmp_path, environ={"APPDATA": str(appdata)}).resolve("gemini")
    assert res.found and res.source == "candidate" and res.path == cmd
    argv, _, spec = _win_service(tmp_path, environ={"APPDATA": str(appdata)}).build_launch("gemini")
    assert argv == [cmd] and spec is not None


def test_catalog_custom_shell_on_windows_uses_comspec_not_sh(tmp_path: Path) -> None:
    comspec = _touch_exe(tmp_path / "System32" / "cmd.exe")
    sh = _touch_exe(tmp_path / "git" / "sh")
    res = _win_service(tmp_path, environ={"COMSPEC": comspec, "SHELL": sh}, which=lambda n: sh if n == "sh" else None).resolve("custom_shell")
    assert res.found and res.path == comspec and res.source == "shell_env"
    # sin COMSPEC: ni siquiera se usa el `sh` del PATH; cae a las rutas candidatas de System32
    sysroot = tmp_path / "Windows"
    sysroot.mkdir()
    ps = _touch_exe(Path(str(sysroot) + "\\System32\\WindowsPowerShell\\v1.0\\powershell.exe"))
    res = _win_service(tmp_path, environ={"SystemRoot": str(sysroot)}, which=lambda n: sh).resolve("custom_shell")
    assert res.found and res.path == ps and res.source == "candidate"


def test_catalog_ignores_a_which_hit_in_the_current_directory_on_windows(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    planted = _touch_exe(tmp_path / "trampa" / "claude.exe")
    monkeypatch.chdir(tmp_path / "trampa")
    res = _win_service(tmp_path, which=lambda n: planted if n == "claude" else None).resolve("claude")
    assert res.found is False
    assert _win_service(tmp_path, which=lambda n: planted).platform == "win32"


def test_windows_candidate_paths_in_the_repo_catalog_are_well_formed() -> None:
    from src.adapters.terminal.cli_catalog import load_repo_catalog

    for spec in load_repo_catalog().profiles.values():
        for pattern in spec.candidate_paths.get("win32", ()):
            assert pattern.startswith("%") and pattern.lower().endswith((".exe", ".cmd")), (spec.id, pattern)


# -- comando de instalacion ------------------------------------------------------------------------------------------


def test_install_command_wrapper_per_platform() -> None:
    posix = build_install_command("npm install -g @openai/codex", "linux", lambda n: None)
    assert posix[1] == "-c" and posix[2].startswith("npm install -g @openai/codex; rc=$?") and posix[0].endswith("sh")
    win = build_install_command('powershell -ExecutionPolicy ByPass -c "irm https://chatgpt.com/codex/install.ps1 | iex"',
                                "win32", lambda n: r"C:\ps\powershell.exe" if n == "powershell" else None)
    assert win[:4] == [r"C:\ps\powershell.exe", "-NoLogo", "-NoProfile", "-Command"]
    assert win[4].startswith("powershell -ExecutionPolicy ByPass") and "$LASTEXITCODE" in win[4] and win[4].endswith("exit $rc")
    assert build_install_command("winget install X.Y", "win32", lambda n: None)[0] == "powershell.exe"
    # y el argv de PowerShell se lanza directo (.exe), sin pasar por cmd.exe
    launch = build_windows_launch([r"C:\ps\powershell.exe", *win[1:]])
    assert launch.kind == "exe" and launch.cmdline is not None and launch.cmdline.startswith("-NoLogo -NoProfile -Command")


@pytest.mark.skipif(os.name == "nt", reason="rutas POSIX")
def test_pathext_helper_does_not_touch_the_real_environment() -> None:
    assert isinstance(pathext_extensions(), list)


def test_packaging_collects_pywinpty_only_on_windows() -> None:
    root = Path(__file__).resolve().parent.parent
    spec = (root / "Mcp2p.spec").read_text(encoding="utf-8")
    build = (root / "build_desktop.py").read_text(encoding="utf-8")
    assert "if sys.platform == 'win32':" in spec and "collect_all('winpty')" in spec
    for dll in ("conpty.dll", "OpenConsole.exe"):
        assert dll in spec and dll in build
    assert "if is_windows:" in build and "winpty_pyinstaller_args()" in build
    for module in ("winpty_host", "win_job", "win_launch", "pty_common"):
        assert f"src.adapters.terminal.{module}" in spec and f"src.adapters.terminal.{module}" in build
