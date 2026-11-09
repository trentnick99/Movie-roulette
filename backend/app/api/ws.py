"""WebSocket endpoint. Authenticated with the same session cookies as the HTTP API; the Origin header must
belong to this server (a foreign web page cannot open a socket with the user's cookies)."""
from __future__ import annotations

import asyncio
import json
import logging
import time
from urllib.parse import urlparse

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from .. import db as dbm
from .. import security as sec
from ..models import Game, Player
from ..services.realtime import Conn, hub

log = logging.getLogger(__name__)
router = APIRouter()


def _origin_ok(ws: WebSocket) -> bool:
    origin = ws.headers.get("origin")
    if not origin:
        return True  # non-browser clients
    host = ws.headers.get("host", "")
    return urlparse(origin).netloc.lower() == host.lower()


def _authenticate(ws: WebSocket, game_id: int, role: str) -> tuple[bool, int | None, str]:
    with dbm.session_scope() as s:
        if s.get(Game, game_id) is None:
            return False, None, ""
        if role == "host":
            p = sec.resolve(s, ws.cookies.get(sec.HOST_COOKIE), "host")
            return p is not None, None, "host screen"
        p = sec.resolve(s, ws.cookies.get(sec.PLAYER_COOKIE), "player")
        if p is None or not sec.player_in_game(s, p, game_id):
            return False, None, ""
        player = s.get(Player, p.player_id)
        return True, p.player_id, player.name if player else "player"


@router.websocket("/ws/game/{game_id}")
async def game_socket(ws: WebSocket, game_id: int, role: str = "player"):
    if role not in ("host", "player") or not _origin_ok(ws) or not sec.host_header_ok(ws.headers.get("host")):
        await ws.close(code=4403)
        return
    ok, player_id, label = await asyncio.to_thread(_authenticate, ws, game_id, role)
    if not ok:
        await ws.close(code=4401)
        return
    await ws.accept()
    conn = Conn(ws=ws, game_id=game_id, role=role, player_id=player_id, label=label, ip=sec.client_ip(ws))
    hub.add(conn)
    hub.notify(game_id)  # everyone sees the new device as online; this socket gets its first snapshot
    try:
        while True:
            raw = await ws.receive_text()
            try:
                msg = json.loads(raw)
            except ValueError:
                continue
            kind = msg.get("type") if isinstance(msg, dict) else None
            if kind == "ping":
                await ws.send_text(json.dumps({"type": "pong", "t": msg.get("t"), "server_time": time.time()}))
            elif kind == "sync":
                await hub.send_state(conn)
    except WebSocketDisconnect:
        pass
    except Exception:
        log.debug("socket error", exc_info=True)
    finally:
        hub.remove(conn)
        hub.notify(game_id)
