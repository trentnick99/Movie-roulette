"""WebSocket hub: pushes role-filtered game state to every connected screen.

Every change triggers a fresh snapshot per connection (host view or that player's view), so a client that
reconnects simply receives the current state - nothing to replay. Snapshots are built from the database, which
remains the single source of truth.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any

from fastapi import WebSocket

from .. import db as dbm
from ..models import Game, GameRound
from . import games as G

log = logging.getLogger(__name__)


@dataclass(eq=False)
class Conn:
    ws: WebSocket
    game_id: int
    role: str
    player_id: int | None
    label: str
    connected_at: float = field(default_factory=time.time)
    ip: str | None = None


class Hub:
    def __init__(self) -> None:
        self.conns: set[Conn] = set()
        self.loop: asyncio.AbstractEventLoop | None = None
        self._end_timers: dict[str, asyncio.TimerHandle] = {}
        self._pending: set[int] = set()

    def bind(self, loop: asyncio.AbstractEventLoop) -> None:
        self.loop = loop
        self._pending.clear()
        self._end_timers.clear()
        self.conns.clear()

    # ---------------------------------------------------------- bookkeeping
    def add(self, c: Conn) -> None:
        self.conns.add(c)

    def remove(self, c: Conn) -> None:
        self.conns.discard(c)

    def online_players(self, game_id: int) -> dict[int, int]:
        out: dict[int, int] = {}
        for c in self.conns:
            if c.game_id == game_id and c.player_id is not None:
                out[c.player_id] = out.get(c.player_id, 0) + 1
        return out

    def devices(self) -> list[dict[str, Any]]:
        return [{"game_id": c.game_id, "role": c.role, "player_id": c.player_id, "label": c.label, "ip": c.ip,
                 "connected_for": int(time.time() - c.connected_at)} for c in self.conns]

    def count(self, role: str | None = None) -> int:
        return sum(1 for c in self.conns if role is None or c.role == role)

    # ---------------------------------------------------------- sending
    def snapshot(self, game_id: int, role: str, player_id: int | None) -> dict[str, Any] | None:
        with dbm.session_scope() as s:
            game = s.get(Game, game_id)
            if game is None:
                return None
            return G.game_view(s, game, is_host=(role == "host"), player_id=player_id,
                               online=self.online_players(game_id))

    async def send_state(self, c: Conn) -> None:
        try:
            state = await asyncio.to_thread(self.snapshot, c.game_id, c.role, c.player_id)
            if state is not None:
                await c.ws.send_text(json.dumps({"type": "state", "state": state, "server_time": time.time()}))
        except Exception:
            log.debug("send to %s failed", c.label, exc_info=True)
            self.remove(c)

    async def broadcast(self, game_id: int) -> None:
        self._pending.discard(game_id)
        targets = [c for c in list(self.conns) if c.game_id == game_id]
        if targets:
            await asyncio.gather(*(self.send_state(c) for c in targets))
        self._schedule_end_timer(game_id)

    def notify(self, game_id: int) -> None:
        """Thread-safe: request a broadcast for a game (coalesced)."""
        loop = self.loop
        if loop is None or loop.is_closed():
            return
        if game_id in self._pending:
            return
        self._pending.add(game_id)

        def _go() -> None:
            asyncio.ensure_future(self.broadcast(game_id))

        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if running is loop:
            loop.call_soon(_go)
        else:
            loop.call_soon_threadsafe(_go)

    def notify_round_token(self, token: str) -> None:
        with dbm.session_scope() as s:
            r = s.query(GameRound).filter_by(token=token).first()
            gid = r.game_id if r else None
        if gid is not None:
            self.notify(gid)

    # ---------------------------------------------------------- clip end timers
    def _schedule_end_timer(self, game_id: int) -> None:
        """When a clip is playing, wake up when it should end so every screen gets the 'ended' state."""
        if self.loop is None:
            return
        with dbm.session_scope() as s:
            game = s.get(Game, game_id)
            rnd = G.current_round(game) if game else None
            pb = rnd.playback if rnd else None
            remaining = None
            if pb is not None and pb.state == "playing" and pb.length:
                remaining = max(0.0, pb.length - G.position(pb)) + 0.15
        key = f"g{game_id}"
        old = self._end_timers.pop(key, None)
        if old is not None:
            old.cancel()
        if remaining is not None:
            self._end_timers[key] = self.loop.call_later(remaining, lambda: self.notify(game_id))

    async def close_all(self) -> None:
        for c in list(self.conns):
            try:
                await c.ws.close(code=1001)
            except Exception:
                pass
        self.conns.clear()


hub = Hub()
