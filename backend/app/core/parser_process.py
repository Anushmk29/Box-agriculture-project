"""Killable parser boundary with per-process admission and memory/wall limits."""

import ctypes
import json
import os
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import BoundedSemaphore
from time import monotonic, sleep

from app.core.work_limits import MAX_RESULT_BYTES, WorkLimit

PARSER_SLOTS = BoundedSemaphore(2)
PARSER_SECONDS = 20
PARSER_MEMORY_MB = 512


def _windows_job(process, memory_mb):
    if sys.platform != "win32":
        return None
    from ctypes import wintypes
    class Basic(ctypes.Structure):
        _fields_ = [("user", ctypes.c_int64), ("job", ctypes.c_int64), ("flags", wintypes.DWORD),
            ("min", ctypes.c_size_t), ("max", ctypes.c_size_t), ("active", wintypes.DWORD),
            ("affinity", ctypes.c_size_t), ("priority", wintypes.DWORD), ("scheduling", wintypes.DWORD)]
    class IO(ctypes.Structure):
        _fields_ = [(name, ctypes.c_uint64) for name in ["read", "write", "other", "read_bytes", "write_bytes", "other_bytes"]]
    class Extended(ctypes.Structure):
        _fields_ = [("basic", Basic), ("io", IO), ("process_memory", ctypes.c_size_t),
            ("job_memory", ctypes.c_size_t), ("peak_process", ctypes.c_size_t), ("peak_job", ctypes.c_size_t)]
    api = ctypes.WinDLL("kernel32", use_last_error=True)
    api.CreateJobObjectW.restype = wintypes.HANDLE
    api.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    api.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    api.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    api.CloseHandle.argtypes = [wintypes.HANDLE]
    api.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
    api.QueryInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p]
    handle = api.CreateJobObjectW(None, None)
    info = Extended()
    # A Windows venv launcher starts the real interpreter as its child.
    info.basic.flags = 0x2000 | 0x100 | 0x200 | 0x8
    info.basic.active = 2
    info.process_memory = memory_mb * 1024 * 1024
    info.job_memory = memory_mb * 1024 * 1024
    if not handle or not api.SetInformationJobObject(handle, 9, ctypes.byref(info), ctypes.sizeof(info)) or not api.AssignProcessToJobObject(handle, wintypes.HANDLE(int(process._handle))):
        if handle:
            api.CloseHandle(handle)
        raise WorkLimit("Parser resource isolation is unavailable", busy=True)
    def close():
        class Accounting(ctypes.Structure):
            _fields_ = [(name, ctypes.c_int64) for name in ("user", "kernel", "period_user", "period_kernel")] + [
                (name, wintypes.DWORD) for name in ("faults", "total", "active", "terminated")]
        try:
            api.TerminateJobObject(handle, 1)
            deadline = monotonic() + 2
            # The venv launcher's child can still hold its working directory after the launcher exits.
            while True:
                accounting = Accounting()
                if not api.QueryInformationJobObject(handle, 1, ctypes.byref(accounting), ctypes.sizeof(accounting), None):
                    raise WorkLimit("Parser resource isolation is unavailable", busy=True)
                if not accounting.active:
                    break
                if monotonic() >= deadline:
                    raise WorkLimit("Parser worker cleanup exceeded its resource budget", busy=True)
                sleep(0.01)
        finally:
            api.CloseHandle(handle)
    return close


def parse_document(path: str, *, mode: str, shipment_date=None, profiles=None, original_name=None, selected_key=None):
    if mode not in {"matrix", "universal"}:
        raise ValueError("Unsupported parser mode")
    supported = {".xlsx", ".xlsm"} if mode == "matrix" else {".xlsx", ".xlsm", ".xls", ".pdf"}
    if Path(path).suffix.lower() not in supported:
        raise ValueError("Unsupported file format")
    if not PARSER_SLOTS.acquire(blocking=False):
        raise WorkLimit("Document processing is busy. Retry the original submission shortly.", busy=True)
    try:
        with TemporaryDirectory(prefix="box-parser-") as scratch:
            output = Path(scratch) / "result.json"
            env = {key: value for key, value in os.environ.items()
                   if key.upper() in {"PATH", "SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "TEMP", "TMP", "LANG"}}
            env.update(DATABASE_URL="postgresql://parser:parser@127.0.0.1:1/parser_unused",
                SUPABASE_DATABASE_URL="postgresql://parser:parser@127.0.0.1:1/parser_unused",
                ENVIRONMENT="test", FILE_STORAGE_PROVIDER="local", SECRET_KEY="parser-only-no-network-access-needed")
            env["PYTHONPATH"] = str(Path(__file__).resolve().parents[2])
            command = [sys.executable, "-m", "app.services.document_worker"]
            process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL, env=env, cwd=scratch,
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0)
            close_job = None
            try:
                close_job = _windows_job(process, PARSER_MEMORY_MB)
                request = {"path": str(Path(path).resolve()), "output": str(output), "mode": mode,
                    "shipment_date": shipment_date, "memory_mb": PARSER_MEMORY_MB, "cpu_seconds": PARSER_SECONDS}
                if profiles is not None:
                    if len(profiles) > 100 or len(json.dumps(profiles)) > 512_000:
                        raise WorkLimit("Too many template definitions for one upload")
                    request.update(profiles=profiles, original_name=original_name, selected_key=selected_key)
                try:
                    process.communicate(json.dumps(request).encode(), timeout=PARSER_SECONDS)
                except subprocess.TimeoutExpired:
                    raise WorkLimit("Document processing exceeded its 20-second budget; split or simplify the file") from None
                if process.returncode or not output.exists() or output.stat().st_size > MAX_RESULT_BYTES:
                    raise WorkLimit("Document processing exceeded its resource budget or stopped unexpectedly")
                result = json.loads(output.read_bytes())
                if result.get("error") == "limit":
                    raise WorkLimit("Document exceeds its safe processing budget; split or simplify the file")
                if result.get("error"):
                    raise ValueError("Document format is invalid or cannot be parsed")
                return result
            finally:
                try:
                    if close_job:
                        close_job()
                finally:
                    if process.poll() is None:
                        process.kill()
                    process.wait()
    finally:
        PARSER_SLOTS.release()
