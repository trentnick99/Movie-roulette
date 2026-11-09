"""Local secret storage (TMDB credential, host password hash, launch token).

Secrets live in data/secrets.json, never in the database dump, the repository or the logs.
On Windows each value is encrypted with DPAPI (CryptProtectData), which ties it to the current Windows user
account; elsewhere the file is created with owner-only permissions. Values are never returned by the API -
only whether they are set and a masked preview.
"""
from __future__ import annotations

import base64
import json
import os
import threading
from pathlib import Path

_lock = threading.Lock()


# ------------------------------------------------------------------ Windows DPAPI

def _dpapi(data: bytes, protect: bool) -> bytes:
    import ctypes
    from ctypes import wintypes

    class BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    buf = ctypes.create_string_buffer(data, len(data))
    inp = BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
    out = BLOB()
    crypt32 = ctypes.windll.crypt32  # type: ignore[attr-defined]
    fn = crypt32.CryptProtectData if protect else crypt32.CryptUnprotectData
    ok = fn(ctypes.byref(inp), None, None, None, None, 0x01, ctypes.byref(out))  # CRYPTPROTECT_UI_FORBIDDEN
    if not ok:
        raise OSError("DPAPI call failed")
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(out.pbData)  # type: ignore[attr-defined]


def _encode(value: str) -> dict:
    if os.name == "nt":
        try:
            return {"dpapi": base64.b64encode(_dpapi(value.encode("utf-8"), True)).decode("ascii")}
        except OSError:
            pass
    return {"plain": value}


def _decode(entry: dict) -> str | None:
    if "dpapi" in entry and os.name == "nt":
        try:
            return _dpapi(base64.b64decode(entry["dpapi"]), False).decode("utf-8")
        except OSError:
            return None
    return entry.get("plain")


# ------------------------------------------------------------------ store

class SecretStore:
    def __init__(self, path: Path):
        self.path = path

    def _read(self) -> dict:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def _write(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f)
        os.replace(tmp, self.path)
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass

    def get(self, name: str) -> str | None:
        with _lock:
            entry = self._read().get(name)
        return _decode(entry) if isinstance(entry, dict) else None

    def set(self, name: str, value: str | None) -> None:
        with _lock:
            data = self._read()
            if value is None or value == "":
                data.pop(name, None)
            else:
                data[name] = _encode(value)
            self._write(data)


def mask(value: str | None) -> str | None:
    if not value:
        return None
    return ("•" * 8) + value[-4:] if len(value) > 8 else "•" * len(value)
