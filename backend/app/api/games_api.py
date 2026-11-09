"""Game API. Host endpoints control everything; player endpoints are limited to their own game and use only the
round token. Every successful change is pushed to all connected screens over the WebSocket hub."""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from .. import security as sec
from ..context import ctx
from ..db import get_db
from ..models import Game, GameRound
from ..services import games as G
from ..services import selection, settings
from ..services import timestamps as tsmod
from ..services.realtime import hub
from ..services.streaming import StreamError
from .common import fail, iso

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["games"])


def _err(e: G.GameError):
    extra = {k: v for k, v in e.extra.items() if k not in ("code", "message")}
    fail(e.status, e.code, e.message, **extra)


def _game(db: Session, game_id: int) -> Game:
    g = db.get(Game, game_id)
    if g is None:
        fail(404, "not_found", "Game not found.")
    return g


def _host_view(db: Session, game: Game) -> dict:
    return G.game_view(db, game, is_host=True, online=hub.online_players(game.id))


def _done(db: Session, game: Game) -> dict:
    db.commit()
    db.refresh(game)
    hub.notify(game.id)
    return _host_view(db, game)


# ------------------------------------------------------------------ host: games

class FilterIn(BaseModel):
    type: str = Field(pattern="^(category|genre|decade|collection|person|studio)$")
    id: int
    role: str | None = Field(None, pattern="^(cast|director)$")


class GamePlayerIn(BaseModel):
    player_id: int
    team_id: int | None = None


class GameIn(BaseModel):
    name: str | None = Field(None, max_length=120)
    mode: str = "library"
    filters: list[FilterIn] = Field(default_factory=list, max_length=20)
    logic: str = Field("all", pattern="^(all|any)$")
    difficulty: str | None = Field(None, pattern="^(easy|medium|hard)$")
    playback_mode: str | None = Field(None, pattern="^(clip|continuous)$")
    clip_seconds: float | None = None
    continuous_max_seconds: float | None = None
    repeat_policy: str | None = Field(None, pattern="^(game|session|days30|days90|none)$")
    rounds_planned: int | None = Field(None, ge=1, le=200)
    team_mode: bool = False
    players: list[GamePlayerIn] = Field(min_length=1, max_length=50)
    allow_walk_ins: bool = True
    video_on_player_devices: bool | None = None
    rules: dict[str, Any] = Field(default_factory=dict)
    scoring: dict[str, Any] = Field(default_factory=dict)


@router.post("/games", dependencies=[Depends(sec.require_host)])
def create_game(body: GameIn, db: Session = Depends(get_db)):
    data = body.model_dump()
    data["players"] = [p.model_dump() for p in body.players]
    data["filters"] = [{k: v for k, v in f.model_dump().items() if v is not None} for f in body.filters]
    if body.video_on_player_devices is None:
        data.pop("video_on_player_devices")
    try:
        game = G.create_game(db, data)
    except G.GameError as e:
        db.rollback()
        _err(e)
    except ValueError as e:
        db.rollback()
        fail(422, "invalid", str(e))
    db.commit()
    return _host_view(db, game)


class PoolIn(BaseModel):
    filters: list[FilterIn] = Field(default_factory=list, max_length=20)
    logic: str = Field("all", pattern="^(all|any)$")
    repeat_policy: str = Field("game", pattern="^(game|session|days30|days90|none)$")


@router.post("/games/pool-preview", dependencies=[Depends(sec.require_host)])
def pool_preview(body: PoolIn, db: Session = Depends(get_db)):
    """How many movies a filter selection would draw from (shown while setting up a game)."""
    filters = [{k: v for k, v in f.model_dump().items() if v is not None} for f in body.filters]
    try:
        pool = selection.build_pool(db, filters, body.logic, game=None, policy=body.repeat_policy,
                                    history_reset_at=settings.history(db).reset_at)
    except ValueError as e:
        fail(422, "invalid", str(e))
    return {"eligible": len(pool.eligible), "in_filters": pool.in_filters,
            "blocked_by_repeat": pool.blocked_by_repeat, "unplayable": pool.unplayable, "excluded": pool.excluded}


@router.get("/games", dependencies=[Depends(sec.require_host)])
def list_games(db: Session = Depends(get_db), status: str | None = None, mode: str | None = None,
               player_id: int | None = None, q: str | None = None, limit: int = 100):
    from ..models import GamePlayer
    from ..services import scoring

    query = db.query(Game)
    if status:
        query = query.filter(Game.status == status)
    if mode:
        query = query.filter(Game.mode == mode)
    if player_id:
        query = query.filter(Game.id.in_(db.query(GamePlayer.game_id).filter(GamePlayer.player_id == player_id)))
    if q:
        query = query.filter(Game.name.ilike(f"%{q}%"))
    out = []
    for g in query.order_by(Game.created_at.desc()).limit(min(limit, 500)):
        board = scoring.scoreboard(db, g)
        top = (board["teams"] if g.team_mode and board["teams"] else board["players"])[:3]
        out.append({"id": g.id, "name": g.name, "mode": g.mode, "mode_label": G.MODES.get(g.mode, g.mode),
                    "status": g.status, "team_mode": g.team_mode, "created_at": iso(g.created_at),
                    "started_at": iso(g.started_at), "finished_at": iso(g.finished_at),
                    "rounds": sum(1 for r in g.rounds if r.status in ("closed", "skipped")),
                    "players": [gp.player.name for gp in g.players], "leaders": top,
                    "difficulty": g.config.get("difficulty"), "join_code": g.join_code})
    return out


@router.get("/games/active", dependencies=[Depends(sec.require_host)])
def active_game(db: Session = Depends(get_db)):
    g = db.query(Game).filter(Game.status.in_(("setup", "active"))).order_by(Game.created_at.desc()).first()
    return {"game": _host_view(db, g) if g else None}


@router.get("/games/{game_id}", dependencies=[Depends(sec.require_host)])
def get_game(game_id: int, db: Session = Depends(get_db)):
    return _host_view(db, _game(db, game_id))


@router.get("/games/{game_id}/review", dependencies=[Depends(sec.require_host)])
def review_game(game_id: int, db: Session = Depends(get_db)):
    """Full round-by-round record of a game (history screen)."""
    from ..models import ScoreEvent

    g = _game(db, game_id)
    names = {gp.player_id: gp.player.name for gp in g.players}
    rounds = []
    for r in g.rounds:
        rounds.append({
            "number": r.number, "status": r.status, "outcome": r.outcome, "difficulty": r.difficulty,
            "category_label": r.category_label, "start_seconds": r.start_seconds, "clip_seconds": r.clip_seconds,
            "timestamp_source": r.timestamp_source, "hints_used": r.hints_used, "turn_player": names.get(
                r.turn_player_id or -1), "movie": G.movie_reveal(r.movie, r),
            "guesses": [{"player": names.get(x.player_id or -1, "?"), "text": x.text, "verdict": x.verdict,
                         "attempt": x.attempt_number} for x in r.guesses],
            "points": [{"player": names.get(e.player_id or -1), "team_id": e.team_id, "points": e.points,
                        "reason": e.reason} for e in db.query(ScoreEvent).filter_by(round_id=r.id)],
        })
    view = _host_view(db, g)
    view["rounds_detail"] = rounds
    view["adjustments"] = [{"player": names.get(e.player_id or -1), "team_id": e.team_id, "points": e.points,
                            "note": e.note, "at": iso(e.created_at)}
                           for e in db.query(ScoreEvent).filter_by(game_id=g.id, round_id=None)]
    return view


@router.post("/games/{game_id}/start", dependencies=[Depends(sec.require_host)])
def start(game_id: int, db: Session = Depends(get_db)):
    g = _game(db, game_id)
    try:
        G.start_game(db, g)
    except G.GameError as e:
        _err(e)
    return _done(db, g)


@router.post("/games/{game_id}/finish", dependencies=[Depends(sec.require_host)])
def finish(game_id: int, db: Session = Depends(get_db), abandon: bool = False):
    g = _game(db, game_id)
    for r in g.rounds:
        ctx().streams.stop(r.token, remove=True)
    G.finish_game(db, g, abandon=abandon)
    return _done(db, g)


class NewRound(BaseModel):
    allow_repeat: bool = False


@router.post("/games/{game_id}/rounds", dependencies=[Depends(sec.require_host)])
def new_round(game_id: int, body: NewRound | None = None, db: Session = Depends(get_db)):
    g = _game(db, game_id)
    try:
        G.new_round(db, g, allow_repeat=bool(body and body.allow_repeat),
                    history_reset_at=settings.history(db).reset_at)
    except G.GameError as e:
        db.rollback()
        _err(e)
    return _done(db, g)


class ScoreAdjust(BaseModel):
    player_id: int | None = None
    team_id: int | None = None
    points: float = Field(ge=-1000, le=1000)
    note: str | None = Field(None, max_length=300)
    request_id: str = Field(min_length=8, max_length=80)


@router.post("/games/{game_id}/score", dependencies=[Depends(sec.require_host)])
def adjust(game_id: int, body: ScoreAdjust, db: Session = Depends(get_db)):
    g = _game(db, game_id)
    try:
        G.adjust_score(db, g, player_id=body.player_id, team_id=body.team_id, points=body.points, note=body.note,
                       key=body.request_id)
    except G.GameError as e:
        db.rollback()
        _err(e)
    return _done(db, g)


class AddPlayer(BaseModel):
    player_id: int
    team_id: int | None = None


@router.post("/games/{game_id}/players", dependencies=[Depends(sec.require_host)])
def add_player(game_id: int, body: AddPlayer, db: Session = Depends(get_db)):
    from ..models import GamePlayer, Player

    g = _game(db, game_id)
    if g.status not in ("setup", "active"):
        fail(409, "bad_state", "This game is over.")
    if db.get(Player, body.player_id) is None:
        fail(404, "not_found", "Player not found.")
    if any(p.player_id == body.player_id for p in g.players):
        fail(409, "exists", "Already in this game.")
    if g.team_mode and not body.team_id:
        fail(422, "no_team", "Choose a team for this player.")
    order = max((p.turn_order for p in g.players), default=-1) + 1
    db.add(GamePlayer(game_id=g.id, player_id=body.player_id, team_id=body.team_id if g.team_mode else None,
                      turn_order=order))
    db.flush()
    db.refresh(g)
    return _done(db, g)


@router.delete("/games/{game_id}/players/{player_id}", dependencies=[Depends(sec.require_host)])
def remove_game_player(game_id: int, player_id: int, db: Session = Depends(get_db)):
    from ..models import GamePlayer, ScoreEvent

    g = _game(db, game_id)
    gp = db.query(GamePlayer).filter_by(game_id=g.id, player_id=player_id).first()
    if gp is None:
        fail(404, "not_found", "Not in this game.")
    if db.query(ScoreEvent).filter_by(game_id=g.id, player_id=player_id).first():
        fail(409, "has_points", "That player already has points in this game.")
    db.delete(gp)
    db.flush()
    db.refresh(g)
    return _done(db, g)


# ------------------------------------------------------------------ rounds (host or player)

def _round_ctx(db: Session, token: str, request: Request, host_only: bool = False):
    rnd = db.query(GameRound).filter_by(token=token).first()
    if rnd is None:
        fail(404, "not_found", "Round not found.")
    game = rnd.game
    host = sec.resolve(db, request.cookies.get(sec.HOST_COOKIE), "host")
    if host is not None:
        return game, rnd, host
    if host_only:
        fail(403, "host_only", "Only the host can do that.")
    player = sec.resolve(db, request.cookies.get(sec.PLAYER_COOKIE), "player")
    if player is None or not sec.player_in_game(db, player, game.id):
        fail(403, "forbidden", "You are not in this game.")
    return game, rnd, player


def _round_done(db: Session, game: Game, p: sec.Principal) -> dict:
    db.commit()
    db.refresh(game)
    hub.notify(game.id)
    return G.game_view(db, game, is_host=p.is_host, player_id=p.player_id, online=hub.online_players(game.id))


def _start_stream(db: Session, game: Game, rnd: GameRound) -> None:
    f = rnd.movie_file
    if f is None or not sec.file_allowed(db, f.path):
        rnd.playback.media_status = "error"
        rnd.playback.media_error = "The movie file is missing or outside the configured movie folders."
        return
    from ..services.media_tools import pick_audio_stream

    pb = G.playback_cfg(game)
    try:
        ctx().streams.start(rnd.token, f.path, float(rnd.start_seconds or 0), float(rnd.playback.length or 30),
                            continuous=rnd.clip_seconds is None,
                            audio_index=pick_audio_stream(f.audio_streams or [], pb.get("audio_language")),
                            max_height=int(pb.get("max_resolution") or 1080), preset=pb.get("preset") or "veryfast")
    except StreamError as e:
        rnd.playback.media_status, rnd.playback.media_error = "error", str(e)


@router.post("/rounds/{token}/timestamp/random")
def random_ts(token: str, request: Request, db: Session = Depends(get_db)):
    game, rnd, p = _round_ctx(db, token, request)
    if not G.can_pick_timestamp(game, rnd, is_host=p.is_host, player_id=p.player_id):
        fail(403, "not_your_turn", "It is not your turn to choose the scene.")
    try:
        G.suggest_random(db, game, rnd)
    except G.GameError as e:
        _err(e)
    return _round_done(db, game, p)


class TimestampIn(BaseModel):
    seconds: float | None = Field(None, ge=0)
    text: str | None = Field(None, max_length=20)
    source: str = Field("manual", pattern="^(manual|random)$")


@router.post("/rounds/{token}/timestamp")
def set_ts(token: str, body: TimestampIn, request: Request, db: Session = Depends(get_db)):
    game, rnd, p = _round_ctx(db, token, request)
    if not G.can_pick_timestamp(game, rnd, is_host=p.is_host, player_id=p.player_id):
        fail(403, "not_your_turn", "It is not your turn to choose the scene.")
    seconds = body.seconds
    if seconds is None:
        try:
            seconds = tsmod.parse_hms(body.text or "")
        except tsmod.TimestampError as e:
            fail(422, "bad_timestamp", str(e))
    try:
        G.confirm_timestamp(db, game, rnd, seconds, body.source, is_host=p.is_host)
    except G.GameError as e:
        _err(e)
    db.commit()  # the playback row must exist before FFmpeg reports back from its thread
    _start_stream(db, game, rnd)
    _reconcile_media(rnd)
    return _round_done(db, game, p)


def _reconcile_media(rnd: GameRound) -> None:
    """Copy the stream job's status onto the round (covers a clip that finished before the commit)."""
    job = ctx().streams.get(rnd.token)
    pb = rnd.playback
    if job is None or pb is None:
        return
    if job.status == "ready" and pb.media_status != "ready":
        pb.media_status, pb.media_error = "ready", None
    elif job.status == "error" and pb.media_status != "error":
        pb.media_status, pb.media_error = "error", job.error


@router.post("/rounds/{token}/timestamp/clear")
def clear_ts(token: str, request: Request, db: Session = Depends(get_db)):
    game, rnd, p = _round_ctx(db, token, request)
    if not p.is_host and not (rnd.status == "timestamp" and G.can_pick_timestamp(game, rnd, is_host=False,
                                                                                  player_id=p.player_id)):
        fail(403, "host_only", "Only the host can change a confirmed timestamp.")
    if rnd.status == "timestamp":
        rnd.suggested_seconds = None
        return _round_done(db, game, p)
    try:
        G.clear_timestamp(db, game, rnd)
    except G.GameError as e:
        _err(e)
    ctx().streams.stop(rnd.token, remove=True)
    return _round_done(db, game, p)


class PlaybackIn(BaseModel):
    action: str = Field(pattern="^(play|pause|stop|replay|seek)$")
    position: float | None = None
    force: bool = False


@router.post("/rounds/{token}/playback")
def playback(token: str, body: PlaybackIn, request: Request, db: Session = Depends(get_db)):
    game, rnd, p = _round_ctx(db, token, request, host_only=True)
    try:
        G.control_playback(db, game, rnd, body.action, body.position, force=body.force)
    except G.GameError as e:
        _err(e)
    return _round_done(db, game, p)


class GuessIn(BaseModel):
    text: str = Field(min_length=1, max_length=300)
    player_id: int | None = None  # host entering a guess on a player's behalf


@router.post("/rounds/{token}/guesses")
def guess(token: str, body: GuessIn, request: Request, db: Session = Depends(get_db)):
    game, rnd, p = _round_ctx(db, token, request)
    pid = body.player_id if p.is_host else p.player_id
    if pid is None:
        fail(422, "no_player", "Choose which player is guessing.")
    try:
        G.submit_guess(db, game, rnd, body.text, pid)
    except G.GameError as e:
        _err(e)
    return _round_done(db, game, p)


class JudgeIn(BaseModel):
    verdict: str = Field(pattern="^(correct|incorrect|pending)$")


@router.post("/rounds/{token}/guesses/{guess_id}/judge")
def judge(token: str, guess_id: int, body: JudgeIn, request: Request, db: Session = Depends(get_db)):
    game, rnd, p = _round_ctx(db, token, request, host_only=True)
    try:
        G.judge_guess(db, game, rnd, guess_id, body.verdict)
    except G.GameError as e:
        db.rollback()
        _err(e)
    return _round_done(db, game, p)


class QuickCorrect(BaseModel):
    player_id: int


@router.post("/rounds/{token}/correct")
def quick_correct(token: str, body: QuickCorrect, request: Request, db: Session = Depends(get_db)):
    """Host marks a player correct for a spoken answer (no typed guess)."""
    game, rnd, p = _round_ctx(db, token, request, host_only=True)
    try:
        g = G.submit_guess(db, game, rnd, "(answered aloud)", body.player_id)
        G.judge_guess(db, game, rnd, g.id, "correct")
    except G.GameError as e:
        db.rollback()
        _err(e)
    return _round_done(db, game, p)


@router.post("/rounds/{token}/hint")
def hint(token: str, request: Request, db: Session = Depends(get_db), force: bool = False):
    game, rnd, p = _round_ctx(db, token, request, host_only=True)
    try:
        G.use_hint(db, game, rnd, force=force)
    except G.GameError as e:
        _err(e)
    return _round_done(db, game, p)


@router.post("/rounds/{token}/reveal")
def reveal(token: str, request: Request, db: Session = Depends(get_db)):
    game, rnd, p = _round_ctx(db, token, request, host_only=True)
    try:
        G.reveal(db, game, rnd)
    except G.GameError as e:
        _err(e)
    return _round_done(db, game, p)


class BonusIn(BaseModel):
    kind: str = Field(pattern="^(year|actor|team|round)$")
    player_id: int | None = None
    team_id: int | None = None
    points: float | None = Field(None, ge=-1000, le=1000)


@router.post("/rounds/{token}/bonus")
def bonus(token: str, body: BonusIn, request: Request, db: Session = Depends(get_db)):
    game, rnd, p = _round_ctx(db, token, request, host_only=True)
    try:
        G.award_bonus(db, game, rnd, body.kind, player_id=body.player_id, team_id=body.team_id, points=body.points)
    except G.GameError as e:
        db.rollback()
        _err(e)
    return _round_done(db, game, p)


@router.post("/rounds/{token}/close")
def close(token: str, request: Request, db: Session = Depends(get_db), skip: bool = False):
    game, rnd, p = _round_ctx(db, token, request, host_only=True)
    G.close_round(db, game, rnd, skip=skip)
    ctx().streams.stop(rnd.token, remove=True)
    return _round_done(db, game, p)
