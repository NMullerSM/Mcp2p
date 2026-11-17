"""
Job Object de Windows para garantizar que ningun proceso de un panel sobreviva al panel.

`pywinpty` solo hace `TerminateProcess` sobre el PID raiz: los nietos (node lanzado por un `.cmd`, servidores de
herramientas, `ping -t`...) quedarian huerfanos. Se asigna el proceso raiz a un Job Object con
`JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`: al cerrar el ultimo handle (o morir el host) Windows mata a todo el job. Los
descendientes que el proceso lance DESPUES de la asignacion heredan el job (jobs anidados desde Windows 8). Hay una
ventana documentada entre el `CreateProcess` y la asignacion; el conector la cubre con un barrido `psutil`.

Las estructuras usan tamanos fijos (`c_uint32`/`c_int64`/`c_size_t`) en lugar de `wintypes` para que el layout
sea el de Win64 tambien al inspeccionarlo en Linux (los tests comprueban `sizeof`).
"""

from __future__ import annotations

import ctypes
import logging
import sys
import threading
from typing import Any, Optional

logger = logging.getLogger("mcp2p.adapters.terminal.win_job")

JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
JOB_OBJECT_EXTENDED_LIMIT_INFORMATION_CLASS = 9  # JobObjectExtendedLimitInformation
PROCESS_SET_QUOTA = 0x0100
PROCESS_TERMINATE = 0x0001


class JobObjectError(OSError):
    """Fallo al crear/configurar/asignar el Job Object (el conector cae al barrido psutil)."""


class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_int64),
        ("PerJobUserTimeLimit", ctypes.c_int64),
        ("LimitFlags", ctypes.c_uint32),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", ctypes.c_uint32),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", ctypes.c_uint32),
        ("SchedulingClass", ctypes.c_uint32),
    ]


class IO_COUNTERS(ctypes.Structure):
    _fields_ = [
        ("ReadOperationCount", ctypes.c_uint64),
        ("WriteOperationCount", ctypes.c_uint64),
        ("OtherOperationCount", ctypes.c_uint64),
        ("ReadTransferCount", ctypes.c_uint64),
        ("WriteTransferCount", ctypes.c_uint64),
        ("OtherTransferCount", ctypes.c_uint64),
    ]


class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
        ("IoInfo", IO_COUNTERS),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


class Kernel32Api:
    """Enlace perezoso con `kernel32` (solo existe en Windows). Los tests inyectan un doble con la misma interfaz."""

    def __init__(self) -> None:
        if sys.platform != "win32" or not hasattr(ctypes, "WinDLL"):
            raise JobObjectError("Los Job Objects solo existen en Windows.")
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        void_p = ctypes.c_void_p
        u32 = ctypes.c_uint32
        k32.CreateJobObjectW.argtypes = [void_p, ctypes.c_wchar_p]
        k32.CreateJobObjectW.restype = void_p
        k32.SetInformationJobObject.argtypes = [void_p, ctypes.c_int, void_p, u32]
        k32.SetInformationJobObject.restype = ctypes.c_int
        k32.OpenProcess.argtypes = [u32, ctypes.c_int, u32]
        k32.OpenProcess.restype = void_p
        k32.AssignProcessToJobObject.argtypes = [void_p, void_p]
        k32.AssignProcessToJobObject.restype = ctypes.c_int
        k32.TerminateJobObject.argtypes = [void_p, ctypes.c_uint]
        k32.TerminateJobObject.restype = ctypes.c_int
        k32.CloseHandle.argtypes = [void_p]
        k32.CloseHandle.restype = ctypes.c_int
        self._k32 = k32

    def last_error(self) -> int:
        return int(ctypes.get_last_error())

    def create_job(self) -> Optional[int]:
        return self._k32.CreateJobObjectW(None, None)

    def set_kill_on_close(self, job: int) -> bool:
        info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        return bool(
            self._k32.SetInformationJobObject(
                job, JOB_OBJECT_EXTENDED_LIMIT_INFORMATION_CLASS, ctypes.byref(info), ctypes.sizeof(info)
            )
        )

    def open_process(self, pid: int) -> Optional[int]:
        return self._k32.OpenProcess(PROCESS_SET_QUOTA | PROCESS_TERMINATE, 0, int(pid))

    def assign(self, job: int, process: int) -> bool:
        return bool(self._k32.AssignProcessToJobObject(job, process))

    def terminate(self, job: int, exit_code: int) -> bool:
        return bool(self._k32.TerminateJobObject(job, int(exit_code)))

    def close_handle(self, handle: int) -> bool:
        return bool(self._k32.CloseHandle(handle))


class WindowsJobObject:
    """Un Job Object con `KILL_ON_JOB_CLOSE`. `close()` e `terminate()` son idempotentes y no lanzan."""

    def __init__(self, api: Optional[Any] = None) -> None:
        self._api = api if api is not None else Kernel32Api()
        self._lock = threading.Lock()
        handle = self._api.create_job()
        if not handle:
            raise JobObjectError(f"CreateJobObjectW fallo (error {self._api.last_error()}).")
        self._handle: Optional[int] = handle
        if not self._api.set_kill_on_close(handle):
            err = self._api.last_error()
            self.close()
            raise JobObjectError(f"SetInformationJobObject(KILL_ON_JOB_CLOSE) fallo (error {err}).")
        self.assigned_pids: list[int] = []

    @property
    def is_open(self) -> bool:
        return self._handle is not None

    def assign_pid(self, pid: int) -> None:
        """Mete el proceso `pid` en el job. Lanza `JobObjectError` si no se puede (p. ej. acceso denegado)."""
        with self._lock:
            if self._handle is None:
                raise JobObjectError("El Job Object ya esta cerrado.")
            proc = self._api.open_process(pid)
            if not proc:
                raise JobObjectError(f"OpenProcess({pid}) fallo (error {self._api.last_error()}).")
            try:
                if not self._api.assign(self._handle, proc):
                    raise JobObjectError(
                        f"AssignProcessToJobObject({pid}) fallo (error {self._api.last_error()})."
                    )
            finally:
                self._api.close_handle(proc)
            self.assigned_pids.append(int(pid))

    def terminate(self, exit_code: int = 1) -> bool:
        """Mata a todos los procesos del job (`TerminateJobObject`). False si el job ya no existe o falla."""
        with self._lock:
            if self._handle is None:
                return False
            try:
                return bool(self._api.terminate(self._handle, exit_code))
            except Exception as exc:  # pragma: no cover - defensivo
                logger.debug("TerminateJobObject fallo: %s", exc)
                return False

    def close(self) -> None:
        """Cierra el handle; con `KILL_ON_JOB_CLOSE` Windows mata lo que quede en el job."""
        with self._lock:
            handle, self._handle = self._handle, None
        if handle is not None:
            try:
                self._api.close_handle(handle)
            except Exception as exc:  # pragma: no cover - defensivo
                logger.debug("CloseHandle(job) fallo: %s", exc)
