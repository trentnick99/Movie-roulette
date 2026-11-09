from __future__ import annotations

import re
from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import AfterValidator, BaseModel, Field
from sqlalchemy import func
from sqlalchemy.orm import Session

from .. import security as sec
from ..db import get_db
from ..models import Game, GamePlayer, Player, Team
from .common import fail, iso

router = APIRouter(prefix="/api", tags=["players"], dependencies=[Depends(sec.require_host)])
COLOR = re.compile(r"^#[0-9a-fA-F]{6}$")
PALETTE = ["#e0b04a", "#5fa8d3", "#e4572e", "#76b041", "#b07bd6", "#f2a541", "#4ecdc4", "#ef6f9d", "#9aa5b1",
           "#c9a227"]


def _color(v: str | None) -> str | None:
    if v is not None and not COLOR.match(v):
        raise ValueError("color must look like #a1b2c3")
    return v


Color = Annotated[str | None, AfterValidator(_color)]


class PlayerIn(BaseModel):
    name: str = Field(min_length=1, max_length=40)
    color: Color = None
    team_id: int | None = None


class PlayerPatch(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=40)
    color: Color = None
    team_id: int | None = None
    clear_team: bool = False
    archived: bool | None = None


class TeamIn(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    color: Color = None
    max_size: int | None = Field(None, ge=1, le=50)


class TeamPatch(TeamIn):
    name: str | None = Field(None, min_length=1, max_length=60)
    clear_max_size: bool = False


def pview(p: Player) -> dict:
    return {"id": p.id, "name": p.name, "color": p.color, "team_id": p.team_id,
            "team": p.team.name if p.team else None, "archived": p.archived, "created_at": iso(p.created_at)}


def tview(db: Session, t: Team) -> dict:
    members = db.query(Player).filter_by(team_id=t.id, archived=False).order_by(Player.name).all()
    return {"id": t.id, "name": t.name, "color": t.color, "max_size": t.max_size,
            "members": [{"id": p.id, "name": p.name, "color": p.color} for p in members]}


def _name_taken(db: Session, model, name: str, exclude: int | None = None) -> bool:
    q = db.query(model).filter(func.lower(model.name) == name.lower())
    if exclude:
        q = q.filter(model.id != exclude)
    return q.first() is not None


def _check_team(db: Session, team_id: int | None, exclude_player: int | None = None) -> None:
    if team_id is None:
        return
    t = db.get(Team, team_id)
    if t is None:
        fail(422, "bad_team", "Team not found.")
    if t.max_size:
        n = db.query(Player).filter(Player.team_id == t.id, Player.archived.is_(False))
        if exclude_player:
            n = n.filter(Player.id != exclude_player)
        if n.count() >= t.max_size:
            fail(409, "team_full", f"Team {t.name} already has {t.max_size} players.")


@router.get("/players")
def list_players(db: Session = Depends(get_db), include_archived: bool = False):
    q = db.query(Player)
    if not include_archived:
        q = q.filter(Player.archived.is_(False))
    return [pview(p) for p in q.order_by(func.lower(Player.name))]


@router.post("/players")
def add_player(body: PlayerIn, db: Session = Depends(get_db)):
    name = " ".join(body.name.split())
    if _name_taken(db, Player, name):
        fail(409, "exists", "A player with that name already exists.")
    _check_team(db, body.team_id)
    n = db.query(Player).count()
    p = Player(name=name, color=body.color or PALETTE[n % len(PALETTE)], team_id=body.team_id)
    db.add(p)
    db.commit()
    return pview(p)


@router.patch("/players/{pid}")
def edit_player(pid: int, body: PlayerPatch, db: Session = Depends(get_db)):
    p = db.get(Player, pid)
    if p is None:
        fail(404, "not_found", "Player not found.")
    if body.name is not None:
        name = " ".join(body.name.split())
        if _name_taken(db, Player, name, p.id):
            fail(409, "exists", "A player with that name already exists.")
        p.name = name
    if body.color is not None:
        p.color = body.color
    if body.clear_team:
        p.team_id = None
    elif body.team_id is not None:
        _check_team(db, body.team_id, p.id)
        p.team_id = body.team_id
    if body.archived is not None:
        p.archived = body.archived
    db.commit()
    return pview(p)


@router.delete("/players/{pid}")
def remove_player(pid: int, db: Session = Depends(get_db)):
    """Players that took part in games are archived (their history stays); others are deleted."""
    p = db.get(Player, pid)
    if p is None:
        fail(404, "not_found", "Player not found.")
    active = (db.query(GamePlayer).join(Game).filter(GamePlayer.player_id == p.id,
                                                     Game.status.in_(("setup", "active"))).first())
    if active:
        fail(409, "in_game", "That player is in a running game. Finish the game first.")
    if db.query(GamePlayer).filter_by(player_id=p.id).first():
        p.archived = True
        db.commit()
        return {"ok": True, "archived": True}
    db.delete(p)
    db.commit()
    return {"ok": True, "archived": False}


@router.get("/teams")
def list_teams(db: Session = Depends(get_db)):
    return [tview(db, t) for t in db.query(Team).order_by(func.lower(Team.name))]


@router.post("/teams")
def add_team(body: TeamIn, db: Session = Depends(get_db)):
    name = " ".join(body.name.split())
    if _name_taken(db, Team, name):
        fail(409, "exists", "A team with that name already exists.")
    n = db.query(Team).count()
    t = Team(name=name, color=body.color or PALETTE[(n + 3) % len(PALETTE)], max_size=body.max_size)
    db.add(t)
    db.commit()
    return tview(db, t)


@router.patch("/teams/{tid}")
def edit_team(tid: int, body: TeamPatch, db: Session = Depends(get_db)):
    t = db.get(Team, tid)
    if t is None:
        fail(404, "not_found", "Team not found.")
    if body.name is not None:
        name = " ".join(body.name.split())
        if _name_taken(db, Team, name, t.id):
            fail(409, "exists", "A team with that name already exists.")
        t.name = name
    if body.color is not None:
        t.color = body.color
    if body.clear_max_size:
        t.max_size = None
    elif body.max_size is not None:
        members = db.query(Player).filter_by(team_id=t.id, archived=False).count()
        if members > body.max_size:
            fail(409, "team_full", f"The team already has {members} players.")
        t.max_size = body.max_size
    db.commit()
    return tview(db, t)


@router.delete("/teams/{tid}")
def remove_team(tid: int, db: Session = Depends(get_db)):
    t = db.get(Team, tid)
    if t is None:
        fail(404, "not_found", "Team not found.")
    active = (db.query(GamePlayer).join(Game).filter(GamePlayer.team_id == t.id,
                                                     Game.status.in_(("setup", "active"))).first())
    if active:
        fail(409, "in_game", "That team is in a running game.")
    db.query(Player).filter_by(team_id=t.id).update({"team_id": None})
    db.delete(t)
    db.commit()
    return {"ok": True}
