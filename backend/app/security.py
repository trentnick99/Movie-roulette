"""Authentication, authorisation and filesystem guards.

Roles
- host:   full control. Obtained with the launch link the start script opens (new token per start), with the
          host password, or - only when no password is set - from a browser on the host PC itself (loopback).
- player: joined a game with its join code. Can only see player-safe game state, submit guesses and (when
          allowed) the timestamp for their own turn.

Host and player sessions use separate cookies so the host can open the player view in the same browser.
"""
from __future__ import annotations

import hashlib
import hmac
import ipaddress
import os
import secrets
import socket
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi import Depends, HTTPException, Request, WebSocket
from sqlalchemy.orm import Session

from .db import get_db
from .models import AuthSession, GamePlayer, LibraryDirectory

HOST_COOKIE = "mr_host"
PLAYER_COOKIE = "mr_player"
SESSION_DAYS = 30
HOST_PASSWORD_KEY = "host_password"
_state: dict = {"launch_token": None}


# ------------------------------------------------------------------ tokens & passwords

def new_token() -> str:
    return secrets.token_urlsafe(32)


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def hash_password(password: str, *, iterations: int = 310_000) -> str:
    salt = os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return f"pbkdf2_sha256${iterations}${salt.hex()}${dk.hex()}"


def verify_password(password: str, stored: str | None) -> bool:
    if not stored:
        return False
    try:
        algo, it, salt, digest = stored.split("$")
        if algo != "pbkdf2_sha256":
            return False
        dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt), int(it))
        return hmac.compare_digest(dk.hex(), digest)
    except (ValueError, TypeError):
        return False


def new_launch_token() -> str:
    """Generated at every start; the start script opens the app with it so the host is signed in at once."""
    _state["launch_token"] = new_token()
    return _state["launch_token"]


def check_launch_token(token: str | None) -> bool:
    cur = _state.get("launch_token")
    return bool(token and cur and hmac.compare_digest(token, cur))


# ------------------------------------------------------------------ network helpers

def is_loopback(host: str | None) -> bool:
    if not host:
        return False
    if host in ("localhost", "testclient"):
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def client_ip(conn: Request | WebSocket) -> str | None:
    return conn.client.host if conn.client else None


def _host_only(host_header: str) -> str:
    h = host_header.strip().lower()
    if h.startswith("["):
        return h[1:h.find("]")] if "]" in h else h
    return h.rsplit(":", 1)[0] if h.count(":") == 1 else h


def host_header_ok(host_header: str | None) -> bool:
    """Refuse requests whose Host header is an unknown *name* (DNS rebinding protection). IP literals are fine;
    names must be localhost or this computer's own name."""
    if not host_header:
        return False
    h = _host_only(host_header)
    if h in ("localhost", "testserver", "testclient"):
        return True
    try:
        ipaddress.ip_address(h)
        return True
    except ValueError:
        pass
    try:
        names = {socket.gethostname().lower(), socket.getfqdn().lower()}
    except OSError:
        names = set()
    names |= {n + ".local" for n in list(names)} | {n + ".lan" for n in list(names)}
    return h in names


# ------------------------------------------------------------------ sessions

@dataclass
class Principal:
    role: str
    session: AuthSession
    player_id: int | None = None
    game_id: int | None = None

    @property
    def is_host(self) -> bool:
        return self.role == "host"


def create_session(db: Session, role: str, *, player_id: int | None = None, game_id: int | None = None,
                   label: str | None = None) -> str:
    token = new_token()
    db.add(AuthSession(token_hash=token_hash(token), role=role, player_id=player_id, game_id=game_id,
                       label=(label or "")[:200]))
    db.flush()
    return token


def resolve(db: Session, token: str | None, role: str) -> Principal | None:
    if not token:
        return None
    s = db.get(AuthSession, token_hash(token))
    if s is None or s.revoked or s.role != role:
        return None
    last = s.last_seen if s.last_seen.tzinfo else s.last_seen.replace(tzinfo=UTC)
    now = datetime.now(UTC)
    if now - last > timedelta(days=SESSION_DAYS):
        return None
    if now - last > timedelta(minutes=5):
        s.last_seen = now
        db.commit()
    return Principal(role=role, session=s, player_id=s.player_id, game_id=s.game_id)


def set_cookie(response, name: str, token: str, secure: bool = False) -> None:
    response.set_cookie(name, token, max_age=SESSION_DAYS * 86400, httponly=True, samesite="strict", secure=secure,
                        path="/")


# ------------------------------------------------------------------ dependencies

def optional_host(request: Request, db: Session = Depends(get_db)) -> Principal | None:
    return resolve(db, request.cookies.get(HOST_COOKIE), "host")


def require_host(request: Request, db: Session = Depends(get_db)) -> Principal:
    p = resolve(db, request.cookies.get(HOST_COOKIE), "host")
    if p is None:
        raise HTTPException(401, detail={"code": "host_auth_required", "message": "Host sign-in required."})
    return p


def optional_player(request: Request, db: Session = Depends(get_db)) -> Principal | None:
    return resolve(db, request.cookies.get(PLAYER_COOKIE), "player")


def require_player(request: Request, db: Session = Depends(get_db)) -> Principal:
    p = resolve(db, request.cookies.get(PLAYER_COOKIE), "player")
    if p is None:
        raise HTTPException(401, detail={"code": "player_auth_required", "message": "Join the game first."})
    return p


def require_any(request: Request, db: Session = Depends(get_db)) -> Principal:
    """Host or a joined player (used for media and images)."""
    p = resolve(db, request.cookies.get(HOST_COOKIE), "host") or resolve(db, request.cookies.get(PLAYER_COOKIE),
                                                                           "player")
    if p is None:
        raise HTTPException(401, detail={"code": "auth_required", "message": "Sign in or join a game."})
    return p


def player_in_game(db: Session, p: Principal, game_id: int) -> bool:
    if p.is_host:
        return True
    if p.game_id != game_id or p.player_id is None:
        return False
    return db.query(GamePlayer).filter_by(game_id=game_id, player_id=p.player_id).first() is not None


# ------------------------------------------------------------------ filesystem guards

def norm(path: str | os.PathLike) -> str:
    """Absolute, symlink-resolved, case-normalised path for comparisons."""
    return os.path.normcase(os.path.realpath(os.path.abspath(os.fspath(path))))


def is_within(path: str | os.PathLike, root: str | os.PathLike) -> bool:
    p, r = norm(path), norm(root)
    try:
        return os.path.commonpath([p, r]) == r
    except ValueError:  # different drives on Windows
        return False


def validate_directory_input(raw: str) -> Path:
    """Validate a folder typed or picked by the host before it is added as a movie directory."""
    if not raw or not raw.strip():
        raise ValueError("Enter a folder path.")
    if "\x00" in raw:
        raise ValueError("Invalid path.")
    p = Path(os.path.expandvars(os.path.expanduser(raw.strip().strip('"'))))
    if not p.is_absolute():
        raise ValueError("Use a full folder path, for example D:\\Movies.")
    if not p.exists():
        raise ValueError("That folder does not exist or is not reachable.")
    if not p.is_dir():
        raise ValueError("That path is a file, not a folder.")
    if not os.access(p, os.R_OK):
        raise ValueError("Movie Roulette does not have permission to read that folder.")
    return Path(os.path.realpath(p))


def file_allowed(db: Session, path: str) -> bool:
    """A media file may only be read when it is inside an enabled, configured movie directory."""
    roots = [d.path for d in db.query(LibraryDirectory).filter_by(enabled=True)]
    return any(is_within(path, r) for r in roots) and os.path.isfile(path)
