"""Mediciones locales de CLIs (WP-5d): archivo en el directorio de datos y su aplicación sobre el catálogo.

Una medición solo la escribe `CliMeasurementService` tras una prueba correcta que el propio usuario lanzó. Al cargar el
catálogo se re-validan SIEMPRE los bloques con los mismos analizadores del catálogo del repo y se comprueba que el
programa medido sigue siendo el mismo (ruta, tamaño y fecha). Cualquier defecto descarta la medición en silencio: el CLI
vuelve a «solo bandeja».
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import tempfile
import threading
from dataclasses import replace
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Tuple

from src.proc_utils import no_window_kwargs
from src.core.domain.cli_measurement import HOOK_INSTALL_BY_CLI, MEASURABLE_CLIS, SCHEMA_VERSION

logger = logging.getLogger("mcp2p.adapters.terminal.cli_measurement_store")

MEASUREMENTS_FILENAME = "cli_measurements.json"
ENV_MEASUREMENTS_FILE = "MCP2P_MEASUREMENTS_FILE"
MAX_MEASUREMENTS_BYTES = 256 * 1024


def default_measurements_path() -> Path:
    override = os.environ.get(ENV_MEASUREMENTS_FILE)
    if override:
        return Path(override)
    from src.runtime.paths import get_user_data_dir

    return Path(get_user_data_dir()) / MEASUREMENTS_FILENAME


def _key(cli_id: str, platform: str) -> str:
    return f"{cli_id}:{platform}"


class MeasurementStore:
    """Archivo JSON `{schema, records, attempts}`, escritura atómica y permisos `0600`.

    `records`: `"<cli>:<plataforma>"` -> medición correcta. `attempts`: `"<cli>:<plataforma>"` -> último intento
    AUTOMÁTICO fallido (sello del programa y motivo) para no repetir con el mismo programa y gastar tokens otra vez.
    """

    def __init__(self, path: Optional[Path] = None) -> None:
        self._path = Path(path) if path is not None else default_measurements_path()
        self._lock = threading.Lock()
        self._cache: Optional[Tuple[Tuple[int, int], Tuple[Dict[str, Any], Dict[str, Any]]]] = None

    @property
    def path(self) -> Path:
        return self._path

    def _load(self) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        try:
            st = self._path.stat()
        except OSError:
            self._cache = None
            return {}, {}
        stamp = (st.st_mtime_ns, st.st_size)
        if self._cache is not None and self._cache[0] == stamp:
            return self._cache[1]
        records: Dict[str, Any] = {}
        attempts: Dict[str, Any] = {}
        try:
            if st.st_size <= MAX_MEASUREMENTS_BYTES:
                data = json.loads(self._path.read_text(encoding="utf-8"))
                if isinstance(data, dict) and data.get("schema") == SCHEMA_VERSION:
                    if isinstance(data.get("records"), dict):
                        records = {str(k): v for k, v in data["records"].items() if isinstance(v, dict)}
                    if isinstance(data.get("attempts"), dict):
                        attempts = {str(k): v for k, v in data["attempts"].items() if isinstance(v, dict)}
        except (OSError, ValueError):
            records, attempts = {}, {}
        self._cache = (stamp, (records, attempts))
        return records, attempts

    def _read(self) -> Dict[str, Any]:
        return self._load()[0]

    def records(self) -> Dict[str, Dict[str, Any]]:
        with self._lock:
            return dict(self._read())

    def get(self, cli_id: str, platform: str) -> Optional[Dict[str, Any]]:
        return self.records().get(_key(cli_id, platform))

    def _write(self, records: Mapping[str, Any], attempts: Mapping[str, Any]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_name(self._path.name + ".tmp")
        payload = json.dumps(
            {"schema": SCHEMA_VERSION, "records": dict(records), "attempts": dict(attempts)},
            indent=2,
            ensure_ascii=False,
        )
        fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(payload + "\n")
        os.replace(str(tmp), str(self._path))
        self._cache = None

    def put(self, record: Mapping[str, Any]) -> None:
        with self._lock:
            records, attempts = (dict(x) for x in self._load())
            key = _key(str(record["cli_id"]), str(record["platform"]))
            records[key] = dict(record)
            attempts.pop(key, None)  # una medición correcta cierra el historial de fallos
            self._write(records, attempts)

    def forget(self, cli_id: str, platform: str) -> bool:
        with self._lock:
            records, attempts = (dict(x) for x in self._load())
            key = _key(cli_id, platform)
            removed = records.pop(key, None) is not None
            had_attempt = attempts.pop(key, None) is not None
            if removed or had_attempt:
                self._write(records, attempts)
            return removed

    def get_attempt(self, cli_id: str, platform: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            return self._load()[1].get(_key(cli_id, platform))

    def put_attempt(self, cli_id: str, platform: str, attempt: Mapping[str, Any]) -> None:
        with self._lock:
            records, attempts = (dict(x) for x in self._load())
            attempts[_key(cli_id, platform)] = dict(attempt)
            self._write(records, attempts)


def binary_stamp(path: str) -> Optional[Tuple[int, int]]:
    """`(mtime_ns, size)` del programa o None si no existe."""
    try:
        st = os.stat(path)
    except OSError:
        return None
    return st.st_mtime_ns, st.st_size


def detect_cli_version(path: str, *, timeout_s: float = 10.0) -> Optional[str]:
    """Salida de `<programa> --version` (sin shell, sin entrada, en una carpeta neutra) o None si no responde."""
    try:
        done = subprocess.run(
            [path, "--version"],
            capture_output=True,
            text=True,
            timeout=timeout_s,
            stdin=subprocess.DEVNULL,
            cwd=tempfile.gettempdir(),
            check=False,
            **no_window_kwargs(),
        )
    except (OSError, subprocess.SubprocessError, ValueError):
        return None
    text = ((done.stdout or "") + " " + (done.stderr or "")).strip()
    return text[:200] or None


def validate_record(record: Mapping[str, Any], spec: Any, platform: str) -> Tuple[Optional[Dict[str, Any]], str]:
    """`(bloques, motivo)`: los bloques `activity`/`delivery` ya analizados, o None y por qué se descarta (`stale`, `invalid`)."""
    from src.adapters.terminal.cli_catalog import CatalogValidationError, _parse_activity_block, _parse_delivery_block

    cli_id = str(record.get("cli_id") or "")
    if (
        record.get("schema") != SCHEMA_VERSION
        or cli_id != getattr(spec, "id", None)
        or cli_id not in MEASURABLE_CLIS
        or record.get("platform") != platform
        or getattr(spec, "kind", "") == "shell"
    ):
        return None, "invalid"
    binary = record.get("binary")
    if not isinstance(binary, dict) or not isinstance(binary.get("path"), str):
        return None, "invalid"
    stamp = binary_stamp(binary["path"])
    if stamp is None or stamp != (binary.get("mtime_ns"), binary.get("size")):
        return None, "stale"
    try:
        activity = _parse_activity_block(
            record.get("activity"), where=f"medicion.{cli_id}", source="measured", kind=spec.kind
        )
        delivery = _parse_delivery_block(
            record.get("delivery"), where=f"medicion.{cli_id}", source="measured", kind=spec.kind
        )
    except CatalogValidationError:
        return None, "invalid"
    if activity["hook_install"] != HOOK_INSTALL_BY_CLI.get(cli_id):
        return None, "invalid"
    for block in (activity, delivery):
        if block["status"] != "verified" or tuple(block["platforms"]) != (platform,):
            return None, "invalid"
    if "turn_ended" not in set(activity["events"].values()):
        return None, "invalid"
    return {"activity": activity, "delivery": delivery}, "ok"


def apply_measurements(catalog: Any, store: MeasurementStore, platform: str) -> Any:
    """Catálogo con los bloques medidos en este equipo (solo CLIs cuya medición sigue siendo válida)."""
    records = store.records()
    if not records:
        return catalog
    from src.adapters.terminal.cli_catalog import CliCatalog

    profiles = dict(catalog.profiles)
    changed = False
    for cli_id in MEASURABLE_CLIS:
        record = records.get(_key(cli_id, platform))
        spec = profiles.get(cli_id)
        if record is None or spec is None:
            continue
        blocks, _why = validate_record(record, spec, platform)
        if blocks is None:
            continue
        profiles[cli_id] = replace(spec, activity=blocks["activity"], delivery=blocks["delivery"])
        changed = True
    if not changed:
        return catalog
    return CliCatalog(catalog_version=catalog.catalog_version, profiles=profiles, warnings=catalog.warnings)
