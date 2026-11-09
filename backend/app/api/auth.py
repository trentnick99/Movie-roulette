from __future__ import annotations

import time
from collections import defaultdict, deque

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.orm import Session

from .. import security as sec
from ..context import ctx
from ..db import get_db
from ..models import AuthSession, Game, GamePlayer, Player, Team
from ..services import games as G
from ..services.realtime import hub
from .common import fail

router = APIRouter(prefix="/api", tags=["auth"])

_attempts: dict[str, deque] = defaultdict(deque)


def rate_limit(request: Request, bucket: str, limit: int = 10, window: float = 60.0) -> None:
    """Refuse when this device had too many *failed* attempts recently (password / join-code guessing)."""
    q = _attempts[f"{bucket}:{sec.client_ip(request)}"]
    now = time.monotonic()
    while q and now - q[0] > window:
        q.popleft()
    if len(q) >= limit:
        fail(429, "rate_limited", "Too many attempts. Wait a minute and try again.")


def note_failure(request: Request, bucket: str) -> None:
    _attempts[f"{bucket}:{sec.client_ip(request)}"].append(time.monotonic())


class HostLogin(BaseModel):
    password: str | None = Field(None, max_length=200)
    launch_token: str | None = Field(None, max_length=200)


class JoinRequest(BaseModel):
    code: str = Field(min_length=4, max_length=12)
    name: str = Field(min_length=1, max_length=40)
    team_id: int | None = None


@router.get("/auth/me")
def me(request: Request, db: Session = Depends(get_db)):
    host = sec.resolve(db, request.cookies.get(sec.HOST_COOKIE), "host")
    player = sec.resolve(db, request.cookies.get(sec.PLAYER_COOKIE), "player")
    pinfo = None
    if player and player.player_id:
        p = db.get(Player, player.player_id)
        g = db.get(Game, player.game_id) if player.game_id else None
        if p and g:
            pinfo = {"player_id": p.id, "name": p.name, "game_id": g.id, "game_status": g.status}
    has_password = bool(ctx().secrets.get(sec.HOST_PASSWORD_KEY))
    local = sec.is_loopback(sec.client_ip(request))
    return {"host": host is not None, "player": pinfo, "host_password_set": has_password,
            "local_device": local, "can_login_without_password": local and not has_password}


@router.post("/auth/host/login")
def host_login(body: HostLogin, request: Request, response: Response, db: Session = Depends(get_db)):
    rate_limit(request, "host-login")
    stored = ctx().secrets.get(sec.HOST_PASSWORD_KEY)
    local = sec.is_loopback(sec.client_ip(request))
    ok = (body.launch_token and local and sec.check_launch_token(body.launch_token)) \
        or (body.password and sec.verify_password(body.password, stored)) \
        or (local and not stored and not body.password)
    if not ok:
        note_failure(request, "host-login")
        if not stored and not local:
            fail(403, "host_password_not_set", "Set a host password on the host computer first "
                 "(Settings > Network) to sign in as host from another device.")
        fail(401, "bad_credentials", "Wrong host password.")
    token = sec.create_session(db, "host", label=request.headers.get("user-agent", "")[:200])
    db.commit()
    sec.set_cookie(response, sec.HOST_COOKIE, token)
    return {"ok": True}


@router.post("/auth/host/logout")
def host_logout(request: Request, response: Response, db: Session = Depends(get_db)):
    p = sec.resolve(db, request.cookies.get(sec.HOST_COOKIE), "host")
    if p:
        p.session.revoked = True
        db.commit()
    response.delete_cookie(sec.HOST_COOKIE, path="/")
    return {"ok": True}


@router.post("/join")
def join(body: JoinRequest, request: Request, response: Response, db: Session = Depends(get_db)):
    rate_limit(request, "join", limit=15)
    code = body.code.strip().upper()
    game = db.query(Game).filter(Game.join_code == code, Game.status.in_(("setup", "active"))).first()
    if game is None:
        note_failure(request, "join")
        fail(404, "bad_code", "No running game has that join code.")
    name = " ".join(body.name.split())
    if not name:
        fail(422, "bad_name", "Enter your name.")
    gp = next((x for x in game.players if x.player.name.lower() == name.lower()), None)
    if gp is None:
        if not game.config.get("allow_walk_ins", True):
            fail(403, "not_invited", "Ask the host to add you to this game.")
        player = db.query(Player).filter(func.lower(Player.name) == name.lower()).first()
        if player is None:
            player = Player(name=name[:60])
            db.add(player)
            db.flush()
        team_id = None
        if game.team_mode:
            team_ids = sorted({x.team_id for x in game.players if x.team_id})
            if body.team_id and (body.team_id in team_ids or db.get(Team, body.team_id)):
                team_id = body.team_id
            elif team_ids:
                team_id = min(team_ids, key=lambda t: sum(1 for x in game.players if x.team_id == t))
            else:
                fail(409, "no_teams", "This team game has no teams yet.")
            t = db.get(Team, team_id)
            if t and t.max_size and sum(1 for x in game.players if x.team_id == team_id) >= t.max_size:
                fail(409, "team_full", f"Team {t.name} is full.")
        order = max((x.turn_order for x in game.players), default=-1) + 1
        gp = GamePlayer(game_id=game.id, player_id=player.id, team_id=team_id, turn_order=order)
        db.add(gp)
        db.flush()
    token = sec.create_session(db, "player", player_id=gp.player_id, game_id=game.id,
                               label=request.headers.get("user-agent", "")[:200])
    db.commit()
    sec.set_cookie(response, sec.PLAYER_COOKIE, token)
    hub.notify(game.id)
    return {"ok": True, "game_id": game.id, "player_id": gp.player_id, "name": gp.player.name}


@router.post("/auth/player/leave")
def leave(request: Request, response: Response, db: Session = Depends(get_db)):
    p = sec.resolve(db, request.cookies.get(sec.PLAYER_COOKIE), "player")
    if p:
        p.session.revoked = True
        db.commit()
    response.delete_cookie(sec.PLAYER_COOKIE, path="/")
    return {"ok": True}


@router.get("/play/state")
def player_state(player: sec.Principal = Depends(sec.require_player), db: Session = Depends(get_db)):
    game = db.get(Game, player.game_id) if player.game_id else None
    if game is None:
        fail(404, "no_game", "Your game no longer exists.")
    return G.game_view(db, game, is_host=False, player_id=player.player_id, online=hub.online_players(game.id))


@router.get("/auth/sessions")
def sessions(_: sec.Principal = Depends(sec.require_host), db: Session = Depends(get_db)):
    rows = db.query(AuthSession).filter_by(revoked=False).order_by(AuthSession.last_seen.desc()).limit(100).all()
    return [{"role": r.role, "player_id": r.player_id, "game_id": r.game_id, "label": r.label,
             "last_seen": r.last_seen.isoformat()} for r in rows]


@router.post("/auth/sessions/revoke-players")
def revoke_players(_: sec.Principal = Depends(sec.require_host), db: Session = Depends(get_db)):
    n = db.query(AuthSession).filter_by(role="player", revoked=False).update({"revoked": True})
    db.commit()
    return {"revoked": n}
