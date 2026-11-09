"""Scoring rules and scoreboard.

Every point change is a ScoreEvent with a unique idempotency key, so double clicks, retried requests or two
host screens can never award the same thing twice.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..models import Game, GamePlayer, GameRound, Guess, Player, ScoreEvent, Team
from .settings import ScoringSettings


class DuplicateScore(Exception):
    pass


def multiplier(game: Game, rnd: GameRound, cfg: ScoringSettings) -> float:
    if not cfg.apply_difficulty_multiplier:
        return 1.0
    presets = (game.config or {}).get("difficulty_presets") or {}
    p = presets.get(rnd.difficulty) or {}
    try:
        return float(p.get("multiplier", 1.0))
    except (TypeError, ValueError):
        return 1.0


def correct_points(game: Game, rnd: GameRound, cfg: ScoringSettings, first_attempt: bool) -> tuple[float, dict]:
    base = cfg.correct + (cfg.first_attempt_bonus if first_attempt else 0.0)
    mult = multiplier(game, rnd, cfg)
    penalty = cfg.hint_penalty * len(rnd.hints_used or [])
    pts = max(0.0, round(base * mult - penalty, 2))
    return pts, {"base": cfg.correct, "first_attempt_bonus": cfg.first_attempt_bonus if first_attempt else 0,
                 "multiplier": mult, "hint_penalty": penalty}


def add_event(db: Session, *, game: Game, key: str, points: float, reason: str, rnd: GameRound | None = None,
              player_id: int | None = None, team_id: int | None = None, note: str | None = None) -> ScoreEvent:
    if db.query(ScoreEvent).filter_by(idempotency_key=key).first() is not None:
        raise DuplicateScore(key)
    ev = ScoreEvent(game_id=game.id, round_id=rnd.id if rnd else None, player_id=player_id, team_id=team_id,
                    points=points, reason=reason, note=(note or None) and note[:300], idempotency_key=key[:200])
    db.add(ev)
    try:
        db.flush()
    except IntegrityError as e:  # a concurrent request won the race
        db.rollback()
        raise DuplicateScore(key) from e
    return ev


def team_of(db: Session, game: Game, player_id: int | None) -> int | None:
    if player_id is None or not game.team_mode:
        return None
    gp = db.query(GamePlayer).filter_by(game_id=game.id, player_id=player_id).first()
    return gp.team_id if gp else None


def award_correct(db: Session, game: Game, rnd: GameRound, guess: Guess, cfg: ScoringSettings) -> list[ScoreEvent]:
    earlier = (db.query(Guess).filter(Guess.round_id == rnd.id, Guess.id != guess.id,
                                      Guess.submitted_at <= guess.submitted_at))
    if game.team_mode and guess.team_id is not None:
        earlier = earlier.filter(Guess.team_id == guess.team_id)
    else:
        earlier = earlier.filter(Guess.player_id == guess.player_id)
    first = earlier.filter(Guess.verdict == "incorrect").count() == 0 and guess.attempt_number == 1
    pts, detail = correct_points(game, rnd, cfg, first)
    who = f"t{guess.team_id}" if game.team_mode and guess.team_id else f"p{guess.player_id}"
    events = [add_event(db, game=game, rnd=rnd, key=f"r{rnd.id}:correct:{who}", points=pts, reason="correct",
                        player_id=guess.player_id, team_id=guess.team_id,
                        note=("first attempt" if first else None))]
    if game.team_mode and guess.team_id and cfg.team_bonus:
        try:
            events.append(add_event(db, game=game, rnd=rnd, key=f"r{rnd.id}:team_bonus:t{guess.team_id}",
                                    points=cfg.team_bonus, reason="team_bonus", team_id=guess.team_id))
        except DuplicateScore:
            pass
    del detail
    return events


def penalise_incorrect(db: Session, game: Game, rnd: GameRound, guess: Guess, cfg: ScoringSettings) -> None:
    if cfg.incorrect_penalty > 0:
        try:
            add_event(db, game=game, rnd=rnd, key=f"r{rnd.id}:incorrect:g{guess.id}", points=-cfg.incorrect_penalty,
                      reason="incorrect", player_id=guess.player_id, team_id=guess.team_id)
        except DuplicateScore:
            pass


def revoke_round_events(db: Session, rnd: GameRound, keys_prefix: str) -> int:
    n = 0
    for ev in db.query(ScoreEvent).filter(ScoreEvent.round_id == rnd.id,
                                          ScoreEvent.idempotency_key.like(keys_prefix + "%")).all():
        db.delete(ev)
        n += 1
    db.flush()
    return n


@dataclass
class Standing:
    kind: str
    id: int
    name: str
    color: str
    points: float
    correct: int
    rank: int = 0
    tied: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "id": self.id, "name": self.name, "color": self.color,
                "points": round(self.points, 2), "correct": self.correct, "rank": self.rank, "tied": self.tied}


def _rank(rows: list[Standing]) -> list[Standing]:
    rows.sort(key=lambda r: (-r.points, -r.correct, r.name.lower()))
    rank = 0
    prev: float | None = None
    for i, r in enumerate(rows):
        if prev is None or r.points != prev:
            rank = i + 1
            prev = r.points
        r.rank = rank
    counts: dict[int, int] = {}
    for r in rows:
        counts[r.rank] = counts.get(r.rank, 0) + 1
    for r in rows:
        r.tied = counts[r.rank] > 1
    return rows


def scoreboard(db: Session, game: Game) -> dict[str, list[dict]]:
    events = db.query(ScoreEvent).filter_by(game_id=game.id).all()
    players: list[Standing] = []
    for gp in game.players:
        p: Player = gp.player
        evs = [e for e in events if e.player_id == p.id]
        players.append(Standing("player", p.id, p.name, p.color, sum(e.points for e in evs),
                                sum(1 for e in evs if e.reason == "correct")))
    teams: list[Standing] = []
    if game.team_mode:
        team_ids = sorted({gp.team_id for gp in game.players if gp.team_id})
        for tid in team_ids:
            t = db.get(Team, tid)
            if t is None:
                continue
            evs = [e for e in events if e.team_id == tid]
            teams.append(Standing("team", t.id, t.name, t.color, sum(e.points for e in evs),
                                  sum(1 for e in evs if e.reason == "correct")))
    return {"players": [s.as_dict() for s in _rank(players)], "teams": [s.as_dict() for s in _rank(teams)]}
