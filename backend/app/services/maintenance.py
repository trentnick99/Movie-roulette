"""Database backup / restore / integrity checks, and local network information."""
from __future__ import annotations

import ipaddress
import re
import socket
import sqlite3
from datetime import datetime
from pathlib import Path

from .. import db as dbm

BACKUP_RE = re.compile(r"^movie_roulette-\d{8}-\d{6}(?:-[a-z0-9]+)?\.db$")


def backup(db_path: Path, backup_dir: Path, label: str = "") -> Path:
    backup_dir.mkdir(parents=True, exist_ok=True)
    suffix = f"-{re.sub(r'[^a-z0-9]', '', label.lower())[:20]}" if label else ""
    dest = backup_dir / f"movie_roulette-{datetime.now().strftime('%Y%m%d-%H%M%S')}{suffix}.db"
    n = 1
    while dest.exists():
        dest = dest.with_name(dest.stem + f"{n}.db")
        n += 1
    src = sqlite3.connect(str(db_path))
    dst = sqlite3.connect(str(dest))
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()
    return dest


def list_backups(backup_dir: Path) -> list[dict]:
    if not backup_dir.is_dir():
        return []
    out = []
    for p in sorted(backup_dir.glob("movie_roulette-*.db"), reverse=True):
        if BACKUP_RE.match(p.name):
            st = p.stat()
            out.append({"name": p.name, "size": st.st_size, "created": datetime.fromtimestamp(st.st_mtime).isoformat()})
    return out


def integrity(path: Path) -> dict:
    con = sqlite3.connect(str(path))
    try:
        res = [r[0] for r in con.execute("PRAGMA integrity_check").fetchall()]
        fk = con.execute("PRAGMA foreign_key_check").fetchall()
        tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        version = None
        if "alembic_version" in tables:
            row = con.execute("SELECT version_num FROM alembic_version").fetchone()
            version = row[0] if row else None
    finally:
        con.close()
    return {"ok": res == ["ok"] and not fk, "integrity": res[:20], "foreign_key_problems": len(fk),
            "schema_version": version, "has_movies_table": "movies" in tables}


def restore(db_path: Path, backup_dir: Path, name: str) -> dict:
    """Replace the live database with a backup (a safety backup of the current one is made first)."""
    if not BACKUP_RE.match(name):
        raise ValueError("Unknown backup.")
    src_path = backup_dir / name
    if not src_path.is_file():
        raise ValueError("Backup not found.")
    check = integrity(src_path)
    if not check["ok"] or not check["has_movies_table"]:
        raise ValueError("That backup failed its integrity check and was not restored.")
    safety = backup(db_path, backup_dir, "pre-restore")
    dbm.dispose()
    src = sqlite3.connect(str(src_path))
    dst = sqlite3.connect(str(db_path))
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()
    dbm.init_engine(db_path)
    return {"restored": name, "safety_backup": safety.name, "schema_version": check["schema_version"]}


# ------------------------------------------------------------------ network

def lan_addresses() -> list[str]:
    """Private IPv4 addresses of this computer (what phones on the same Wi-Fi should use)."""
    found: list[str] = []
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("10.255.255.255", 1))  # no packets are sent; picks the outward interface
            found.append(s.getsockname()[0])
        finally:
            s.close()
    except OSError:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            found.append(str(info[4][0]))
    except OSError:
        pass
    out: list[str] = []
    for a in found:
        try:
            ip = ipaddress.ip_address(a)
        except ValueError:
            continue
        if ip.is_private and not ip.is_loopback and not ip.is_link_local and a not in out:
            out.append(a)
    return out
