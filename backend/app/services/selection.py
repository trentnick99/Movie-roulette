"""Random movie selection with category restrictions and repeat rules.

Selection is uniform over the eligible set (OS-backed randomness over a sorted id list), so popularity, rating,
file name and insertion order have no influence. A movie outside the chosen filters is never picked silently:
when nothing is left the caller gets a PoolExhausted error explaining why, and decides what to do.
"""
from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

from ..models import Game, GameRound, Movie, MovieFile
from . import categories

_PROCESS_STARTED = datetime.now(UTC)
_rng = random.SystemRandom()


def process_started() -> datetime:
    return _PROCESS_STARTED


class PoolExhausted(Exception):
    def __init__(self, info: dict[str, Any]):
        super().__init__(info.get("message", "No eligible movies"))
        self.info = info


@dataclass
class Pool:
    eligible: list[int]
    in_filters: int
    blocked_by_repeat: int
    unplayable: int
    excluded: int


def _aware(d: datetime) -> datetime:
    return d if d.tzinfo else d.replace(tzinfo=UTC)


def repeat_blocked(db: Session, game: Game | None, policy: str, history_reset_at: str | None) -> set[int]:
    reset = datetime.fromisoformat(history_reset_at) if history_reset_at else None
    if reset is not None:
        reset = _aware(reset)
    since: datetime | None = None
    q = db.query(GameRound.movie_id)
    if policy == "none":
        return set()
    if policy == "game":
        if game is None:
            return set()
        return {m for (m,) in q.filter(GameRound.game_id == game.id).distinct()}
    if policy == "session":
        since = _PROCESS_STARTED
    elif policy == "days30":
        since = datetime.now(UTC) - timedelta(days=30)
    elif policy == "days90":
        since = datetime.now(UTC) - timedelta(days=90)
    if reset is not None and (since is None or reset > since):
        since = reset
    blocked = {m for (m,) in q.filter(GameRound.created_at >= since, GameRound.status != "skipped").distinct()} \
        if since else set()
    if game is not None:  # never repeat within the running game, whatever the wider rule
        blocked |= {m for (m,) in db.query(GameRound.movie_id).filter(GameRound.game_id == game.id).distinct()}
    return blocked


def build_pool(db: Session, filters: list[dict[str, Any]], logic: str, *, game: Game | None, policy: str,
               history_reset_at: str | None) -> Pool:
    in_filters = categories.evaluate_filters(db, filters, logic)
    q = db.query(Movie.id, Movie.excluded_from_game).filter(Movie.metadata_status == "matched")
    rows = q.all()
    all_ids = {r[0] for r in rows} if in_filters is None else {r[0] for r in rows if r[0] in in_filters}
    excluded = {r[0] for r in rows if r[1]} & all_ids
    playable = {m for (m,) in db.query(MovieFile.movie_id).filter(MovieFile.status == "ok",
                                                                    MovieFile.duration > 0,
                                                                    MovieFile.movie_id.isnot(None)).distinct()}
    candidates = (all_ids - excluded) & playable
    unplayable = len((all_ids - excluded) - playable)
    blocked = repeat_blocked(db, game, policy, history_reset_at) & candidates
    eligible = sorted(candidates - blocked)
    return Pool(eligible=eligible, in_filters=len(all_ids), blocked_by_repeat=len(blocked), unplayable=unplayable,
                excluded=len(excluded))


def pick(db: Session, filters: list[dict[str, Any]], logic: str, *, game: Game | None, policy: str,
         history_reset_at: str | None, rng: random.Random | None = None) -> int:
    pool = build_pool(db, filters, logic, game=game, policy=policy, history_reset_at=history_reset_at)
    if not pool.eligible:
        if pool.in_filters == 0:
            msg = "No matched movies fit the chosen categories."
            options = ["change_filters"]
        elif pool.blocked_by_repeat:
            msg = (f"All {pool.blocked_by_repeat} playable movie(s) in these categories were already played under "
                   "the current repeat rule.")
            options = ["allow_repeats", "change_filters", "reset_history"]
        else:
            msg = "The movies in these categories have no playable file or are excluded from games."
            options = ["change_filters"]
        raise PoolExhausted({"code": "pool_exhausted", "message": msg, "in_filters": pool.in_filters,
                             "blocked_by_repeat": pool.blocked_by_repeat, "unplayable": pool.unplayable,
                             "excluded": pool.excluded, "options": options})
    return (rng or _rng).choice(pool.eligible)
