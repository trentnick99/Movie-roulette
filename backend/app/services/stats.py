"""Game history and library statistics."""
from __future__ import annotations

from collections import defaultdict
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session

from ..models import (
    Game,
    GamePlayer,
    GameRound,
    Genre,
    Guess,
    Movie,
    MovieFile,
    MovieGenre,
    Player,
    ScoreEvent,
    Team,
)
from . import categories, scoring


def winners(db: Session, game: Game) -> list[dict]:
    board = scoring.scoreboard(db, game)
    rows = board["teams"] if game.team_mode and board["teams"] else board["players"]
    if not rows or rows[0]["points"] <= 0:
        return []
    return [r for r in rows if r["rank"] == 1]


def overview(db: Session) -> dict[str, Any]:
    finished = db.query(Game).filter(Game.status == "finished").all()
    rounds = db.query(GameRound).filter(GameRound.status.in_(("closed", "skipped"))).all()
    played = [r for r in rounds if r.status == "closed"]
    correct = [r for r in played if r.outcome == "correct"]
    missed = [r for r in played if r.outcome == "missed"]

    # movies
    movie_counts: dict[int, dict[str, int]] = defaultdict(lambda: {"played": 0, "correct": 0})
    for r in played:
        movie_counts[r.movie_id]["played"] += 1
        if r.outcome == "correct":
            movie_counts[r.movie_id]["correct"] += 1
    titles = {m.id: (m.title, m.release_year) for m in db.query(Movie).filter(Movie.id.in_(list(movie_counts)))}
    most_missed = sorted(((mid, c) for mid, c in movie_counts.items() if c["played"] - c["correct"] > 0),
                         key=lambda x: -(x[1]["played"] - x[1]["correct"]))[:10]

    # categories (by genre of the selected movie)
    genre_stats: dict[str, dict[str, int]] = defaultdict(lambda: {"played": 0, "correct": 0})
    gmap: dict[int, list[str]] = defaultdict(list)
    for mid, gname in (db.query(MovieGenre.movie_id, Genre.name).join(Genre, Genre.id == MovieGenre.genre_id)
                       .filter(MovieGenre.movie_id.in_([r.movie_id for r in played] or [0]))):
        gmap[mid].append(gname)
    decade_stats: dict[str, dict[str, int]] = defaultdict(lambda: {"played": 0, "correct": 0})
    years = {m.id: m.release_year for m in db.query(Movie).filter(Movie.id.in_([r.movie_id for r in played] or [0]))}
    for r in played:
        for gname in gmap.get(r.movie_id, []):
            genre_stats[gname]["played"] += 1
            genre_stats[gname]["correct"] += 1 if r.outcome == "correct" else 0
        d = categories.decade_of(years.get(r.movie_id))
        if d is not None:
            decade_stats[f"{d}s"]["played"] += 1
            decade_stats[f"{d}s"]["correct"] += 1 if r.outcome == "correct" else 0

    def rate_rows(src: dict[str, dict[str, int]]) -> list[dict]:
        rows = [{"name": k, "played": v["played"], "correct": v["correct"],
                 "success_rate": round(v["correct"] / v["played"], 3) if v["played"] else 0} for k, v in src.items()]
        return sorted(rows, key=lambda x: (x["success_rate"], -x["played"]))

    # players
    pstats = []
    for p in db.query(Player).order_by(Player.name):
        gps = db.query(GamePlayer).join(Game).filter(GamePlayer.player_id == p.id, Game.status == "finished").all()
        games_played = len(gps)
        wins = 0
        for gp in gps:
            w = winners(db, gp.game)
            if gp.game.team_mode:
                wins += 1 if any(x["kind"] == "team" and x["id"] == gp.team_id for x in w) else 0
            else:
                wins += 1 if any(x["kind"] == "player" and x["id"] == p.id for x in w) else 0
        guesses = db.query(Guess).filter(Guess.player_id == p.id, Guess.verdict != "pending").all()
        right = sum(1 for g in guesses if g.verdict == "correct")
        points = db.query(func.coalesce(func.sum(ScoreEvent.points), 0)).filter(ScoreEvent.player_id == p.id).scalar()
        first_try = sum(1 for g in guesses if g.verdict == "correct" and g.attempt_number == 1)
        pstats.append({"id": p.id, "name": p.name, "color": p.color, "games": games_played, "wins": wins,
                       "win_rate": round(wins / games_played, 3) if games_played else 0,
                       "guesses": len(guesses), "correct": right,
                       "accuracy": round(right / len(guesses), 3) if guesses else 0,
                       "first_try": first_try, "points": round(float(points or 0), 2),
                       "avg_points_per_game": round(float(points or 0) / games_played, 2) if games_played else 0})

    tstats = []
    for t in db.query(Team).order_by(Team.name):
        gids = {gp.game_id for gp in db.query(GamePlayer).join(Game)
                .filter(GamePlayer.team_id == t.id, Game.status == "finished", Game.team_mode.is_(True))}
        wins = sum(1 for gid in gids if any(x["kind"] == "team" and x["id"] == t.id
                                             for x in winners(db, db.get(Game, gid))))
        points = db.query(func.coalesce(func.sum(ScoreEvent.points), 0)).filter(ScoreEvent.team_id == t.id).scalar()
        tstats.append({"id": t.id, "name": t.name, "color": t.color, "games": len(gids), "wins": wins,
                       "win_rate": round(wins / len(gids), 3) if gids else 0, "points": round(float(points or 0), 2)})

    return {
        "totals": {
            "games": db.query(Game).filter(Game.status.in_(("finished", "active"))).count(),
            "games_finished": len(finished), "rounds": len(rounds), "rounds_played": len(played),
            "correct": len(correct), "missed": len(missed), "skipped": len(rounds) - len(played),
            "success_rate": round(len(correct) / len(played), 3) if played else 0,
            "distinct_movies_played": len(movie_counts),
            "movies": db.query(Movie).filter(Movie.metadata_status == "matched").count(),
            "files": db.query(MovieFile).filter(MovieFile.status == "ok").count(),
        },
        "most_missed_movies": [{"movie_id": mid, "title": titles.get(mid, ("?", None))[0],
                                "release_year": titles.get(mid, ("?", None))[1], **c} for mid, c in most_missed],
        "hardest_genres": rate_rows(genre_stats)[:10],
        "hardest_decades": rate_rows(decade_stats),
        "players": sorted(pstats, key=lambda x: (-x["wins"], -x["points"], x["name"])),
        "teams": sorted(tstats, key=lambda x: (-x["wins"], -x["points"], x["name"])),
    }
