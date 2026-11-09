"""Game engine: games, rounds, timestamps, playback state, guesses, hints, reveal and role-filtered views.

Round lifecycle:  timestamp -> ready -> revealed -> closed      (or -> skipped at any point)
- timestamp: a movie has been picked; the guesser/host chooses where the scene starts.
- ready:     timestamp confirmed, the clip is being prepared / played; players guess.
- revealed:  the answer is shown to everyone.
- closed:    the round is over; the next one can start.

Players only ever see the round *token*. Movie identity (title, poster, year, ids, file name ...) is added to
the player view only after the host reveals the round.
"""
from __future__ import annotations

import secrets
import string
import time
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from ..models import (
    Game,
    GamePlayer,
    GameRound,
    Guess,
    Movie,
    MovieFile,
    PlaybackSession,
    Player,
    ScoreEvent,
    Team,
    utcnow,
)
from . import categories, scoring, selection, settings
from . import timestamps as ts
from .scanner import primary_file

MODES = {
    "category": "Category Roulette",
    "library": "Full Library Roulette",
    "franchise": "Franchise Challenge",
    "decade": "Decade Challenge",
    "person": "Director / Actor Challenge",
    "custom": "Custom Game",
    "difficulty": "Difficulty Challenge",
}
_MODE_FILTERS = {"category": {"category"}, "franchise": {"collection"}, "decade": {"decade"}, "person": {"person"}}
ACTIVE_ROUND = ("timestamp", "ready", "revealed")


class GameError(Exception):
    def __init__(self, message: str, code: str = "game_error", status: int = 409, extra: dict | None = None):
        super().__init__(message)
        self.message, self.code, self.status, self.extra = message, code, status, extra or {}


# ------------------------------------------------------------------ helpers

def new_round_token() -> str:
    return secrets.token_urlsafe(24)


def new_join_code(db: Session, length: int = 6) -> str:
    alphabet = "".join(c for c in string.ascii_uppercase + string.digits if c not in "O0I1L")
    for _ in range(50):
        code = "".join(secrets.choice(alphabet) for _ in range(length))
        if not db.query(Game).filter(Game.join_code == code, Game.status.in_(("setup", "active"))).first():
            return code
    raise GameError("Could not create a join code.", "join_code")


def current_round(game: Game) -> GameRound | None:
    for r in reversed(game.rounds):
        if r.status in ACTIVE_ROUND:
            return r
    return None


def last_round(game: Game) -> GameRound | None:
    return game.rounds[-1] if game.rounds else None


def preset_for(game: Game, difficulty: str) -> settings.DifficultyPreset:
    data = (game.config.get("difficulty_presets") or {}).get(difficulty)
    if data:
        return settings.DifficultyPreset.model_validate(data)
    return settings.GameSettings().difficulty_presets[difficulty]


def game_rules(game: Game) -> settings.GameSettings:
    return settings.GameSettings.model_validate(game.config.get("rules") or {})


def scoring_cfg(game: Game) -> settings.ScoringSettings:
    return settings.ScoringSettings.model_validate(game.config.get("scoring") or {})


def playback_cfg(game: Game) -> dict[str, Any]:
    return game.config.get("playback") or {}


# ------------------------------------------------------------------ creating & starting games

def create_game(db: Session, data: dict[str, Any]) -> Game:
    mode = data.get("mode", "library")
    if mode not in MODES:
        raise GameError("Unknown game mode.", "bad_mode", 422)
    filters = [f for f in (data.get("filters") or []) if isinstance(f, dict)]
    for f in filters:
        if f.get("type") not in categories.FILTER_TYPES or f.get("id") is None:
            raise GameError("Invalid category filter.", "bad_filter", 422)
    need = _MODE_FILTERS.get(mode)
    if need and not any(f["type"] in need for f in filters):
        raise GameError(f"{MODES[mode]} needs at least one {', '.join(sorted(need))} selection.", "missing_filter",
                        422)
    if mode == "library":
        filters = []
    if mode == "custom" and not filters:
        raise GameError("Pick at least one category or filter for a custom game.", "missing_filter", 422)
    logic = data.get("logic", "all") if data.get("logic") in ("all", "any") else "all"

    g_set = settings.game(db)
    s_set = settings.scoring(db)
    p_set = settings.playback(db)
    difficulty = data.get("difficulty") or g_set.default_difficulty
    if difficulty not in ("easy", "medium", "hard"):
        raise GameError("Unknown difficulty.", "bad_difficulty", 422)
    rules = g_set.model_copy(update={k: v for k, v in (data.get("rules") or {}).items()
                                     if k in settings.GameSettings.model_fields})
    rules = settings.GameSettings.model_validate(rules.model_dump())
    scoring_over = settings.ScoringSettings.model_validate({**s_set.model_dump(), **(data.get("scoring") or {})})
    mode_pb = data.get("playback_mode") or p_set.default_mode
    if mode_pb not in ("clip", "continuous"):
        raise GameError("Unknown playback mode.", "bad_playback", 422)
    preset = rules.difficulty_presets[difficulty]
    clip = data.get("clip_seconds") or (preset.clip_seconds if mode == "difficulty" else p_set.default_clip_seconds)
    try:
        clip = float(clip)
    except (TypeError, ValueError) as e:
        raise GameError("Clip length must be a number of seconds.", "bad_clip", 422) from e
    if not 3 <= clip <= 600:
        raise GameError("Clip length must be between 3 and 600 seconds.", "bad_clip", 422)
    cont_max = float(data.get("continuous_max_seconds") or p_set.continuous_max_seconds)
    if not 30 <= cont_max <= 3600:
        raise GameError("Continuous playback limit must be between 30 seconds and 60 minutes.", "bad_clip", 422)

    team_mode = bool(data.get("team_mode"))
    entries = data.get("players") or []
    if not entries:
        raise GameError("Add at least one player.", "no_players", 422)
    seen: set[int] = set()
    game = Game(
        name=(data.get("name") or f"Game night {datetime.now().strftime('%b %d')}").strip()[:120],
        mode=mode, team_mode=team_mode, status="setup",
        join_code=new_join_code(db, settings.network(db).join_code_length),
        config={
            "filters": filters, "logic": logic, "difficulty": difficulty,
            "difficulty_presets": {k: v.model_dump() for k, v in rules.difficulty_presets.items()},
            "rules": rules.model_dump(), "scoring": scoring_over.model_dump(),
            "playback": {"mode": mode_pb, "clip_seconds": clip, "continuous_max_seconds": cont_max,
                         "max_resolution": p_set.max_resolution, "preset": p_set.encoder_preset,
                         "video_on_player_devices": bool(data.get("video_on_player_devices",
                                                                  p_set.video_on_player_devices)),
                         "audio_language": p_set.preferred_audio_language},
            "repeat_policy": data.get("repeat_policy") or rules.repeat_policy,
            "rounds_planned": int(data.get("rounds_planned") or rules.default_rounds),
            "allow_walk_ins": bool(data.get("allow_walk_ins", True)),
            "filter_labels": [categories.describe_filter(db, f) for f in filters],
        })
    if game.config["repeat_policy"] not in ("game", "session", "days30", "days90", "none"):
        raise GameError("Unknown repeat rule.", "bad_repeat", 422)
    db.add(game)
    db.flush()
    for i, e in enumerate(entries):
        pid = int(e["player_id"]) if isinstance(e, dict) else int(e)
        if pid in seen:
            continue
        seen.add(pid)
        player = db.get(Player, pid)
        if player is None:
            raise GameError("Unknown player.", "bad_player", 422)
        team_id = (e.get("team_id") if isinstance(e, dict) else None) or (player.team_id if team_mode else None)
        if team_mode and team_id is None:
            raise GameError(f"Assign {player.name} to a team (team mode).", "no_team", 422)
        if team_id is not None and db.get(Team, int(team_id)) is None:
            raise GameError("Unknown team.", "bad_team", 422)
        db.add(GamePlayer(game_id=game.id, player_id=pid, team_id=int(team_id) if team_mode and team_id else None,
                          turn_order=i))
    db.flush()
    if team_mode:
        _check_team_sizes(db, game)
    db.refresh(game)
    return game


def _check_team_sizes(db: Session, game: Game) -> None:
    counts: dict[int, int] = {}
    for gp in game.players:
        if gp.team_id:
            counts[gp.team_id] = counts.get(gp.team_id, 0) + 1
    for tid, n in counts.items():
        t = db.get(Team, tid)
        if t is not None and t.max_size and n > t.max_size:
            raise GameError(f"Team {t.name} allows at most {t.max_size} players.", "team_full", 422)


def start_game(db: Session, game: Game) -> Game:
    if game.status != "setup":
        raise GameError("This game has already started.", "bad_state")
    if not game.players:
        raise GameError("Add at least one player.", "no_players", 422)
    game.status = "active"
    game.started_at = utcnow()
    db.flush()
    return game


def finish_game(db: Session, game: Game, abandon: bool = False) -> Game:
    if game.status in ("finished", "abandoned"):
        return game
    for r in game.rounds:
        if r.status in ACTIVE_ROUND:
            r.status, r.closed_at = "closed", utcnow()
            r.outcome = r.outcome or "missed"
            if r.playback:
                r.playback.state = "ended"
    game.status = "abandoned" if abandon else "finished"
    game.finished_at = utcnow()
    game.join_code = None
    db.flush()
    return game


# ------------------------------------------------------------------ rounds

def _turn(game: Game, number: int) -> GamePlayer | None:
    if not game.players:
        return None
    if game.team_mode:
        teams = sorted({gp.team_id for gp in game.players if gp.team_id})
        if teams:
            tid = teams[(number - 1) % len(teams)]
            members = [gp for gp in game.players if gp.team_id == tid]
            return members[((number - 1) // len(teams)) % len(members)]
    return game.players[(number - 1) % len(game.players)]


def round_window(game: Game, rnd: GameRound, f: MovieFile) -> ts.Window:
    pb = playback_cfg(game)
    clip = None if pb.get("mode") == "continuous" else float(pb.get("clip_seconds") or 30)
    return ts.allowed_window(f.duration, clip, game_rules(game), preset_for(game, rnd.difficulty), f.chapters)


def new_round(db: Session, game: Game, *, allow_repeat: bool = False, history_reset_at: str | None = None) -> GameRound:
    if game.status == "setup":
        start_game(db, game)
    if game.status != "active":
        raise GameError("This game is over.", "bad_state")
    cur = current_round(game)
    if cur is not None:
        raise GameError("Finish the current round first.", "round_open")
    policy = "none" if allow_repeat else game.config.get("repeat_policy", "game")
    try:
        movie_id = selection.pick(db, game.config.get("filters") or [], game.config.get("logic", "all"), game=game,
                                  policy=policy, history_reset_at=history_reset_at)
    except selection.PoolExhausted as e:
        raise GameError(e.info["message"], "pool_exhausted", 409, e.info) from e
    movie = db.get(Movie, movie_id)
    assert movie is not None
    f = primary_file(movie, settings.library(db).duplicate_preference)
    if f is None:
        raise GameError("The selected movie has no playable file.", "no_file")
    number = (game.rounds[-1].number + 1) if game.rounds else 1
    gp = _turn(game, number)
    labels = game.config.get("filter_labels") or []
    rnd = GameRound(game_id=game.id, number=number, token=new_round_token(), movie_id=movie.id, movie_file_id=f.id,
                    status="timestamp", turn_player_id=gp.player_id if gp else None,
                    turn_team_id=gp.team_id if gp else None, difficulty=game.config.get("difficulty", "medium"),
                    category_label=(" + " if game.config.get("logic") == "all" else " / ").join(labels)
                    or MODES.get(game.mode))
    db.add(rnd)
    db.flush()
    try:
        w = round_window(game, rnd, f)
    except ts.TimestampError as e:
        raise GameError(str(e), "bad_media") from e
    rnd.allowed_min, rnd.allowed_max = w.start, w.end
    pb = playback_cfg(game)
    if pb.get("mode") == "continuous":
        rnd.clip_seconds, rnd.max_seconds = None, float(pb.get("continuous_max_seconds") or 600)
    else:
        rnd.clip_seconds = w.clip_seconds
    game.turn_index = number
    db.flush()
    db.refresh(game)
    return rnd


def get_round(db: Session, token: str) -> GameRound:
    rnd = db.query(GameRound).filter_by(token=token).first() if token else None
    if rnd is None:
        raise GameError("Round not found.", "not_found", 404)
    return rnd


def _window_of(rnd: GameRound) -> ts.Window:
    f = rnd.movie_file
    return ts.Window(start=rnd.allowed_min, end=rnd.allowed_max, clip_seconds=rnd.clip_seconds,
                     duration=float(f.duration if f and f.duration else 0))


def can_pick_timestamp(game: Game, rnd: GameRound, *, is_host: bool, player_id: int | None) -> bool:
    rules = game_rules(game)
    if is_host:
        return True
    if not rules.player_timestamp_from_device or player_id is None:
        return False
    if game.team_mode and rnd.turn_team_id is not None:
        gp = next((p for p in game.players if p.player_id == player_id), None)
        return gp is not None and gp.team_id == rnd.turn_team_id
    return rnd.turn_player_id == player_id


def suggest_random(db: Session, game: Game, rnd: GameRound) -> float:
    if not game_rules(game).timestamp_random:
        raise GameError("Random timestamps are turned off for this game.", "disabled", 403)
    if rnd.status != "timestamp":
        raise GameError("The timestamp is already chosen.", "bad_state")
    rnd.suggested_seconds = ts.random_timestamp(_window_of(rnd))
    db.flush()
    return rnd.suggested_seconds


def confirm_timestamp(db: Session, game: Game, rnd: GameRound, seconds: float, source: str, *,
                      is_host: bool) -> GameRound:
    rules = game_rules(game)
    if rnd.status == "ready" and is_host and rules.host_override:
        pass  # host override of an already chosen start
    elif rnd.status != "timestamp":
        raise GameError("The timestamp can no longer be changed.", "bad_state")
    if source == "manual" and not rules.timestamp_manual and not is_host:
        raise GameError("Manual timestamps are turned off for this game.", "disabled", 403)
    if source == "random":
        if not rules.timestamp_random:
            raise GameError("Random timestamps are turned off for this game.", "disabled", 403)
        if rnd.suggested_seconds is None or abs(float(seconds) - rnd.suggested_seconds) > 0.01:
            raise GameError("Accept the suggested time or generate a new one.", "stale_suggestion")
    try:
        seconds = ts.validate(seconds, _window_of(rnd))
    except ts.TimestampError as e:
        raise GameError(str(e), "bad_timestamp", 422) from e
    rnd.start_seconds = seconds
    rnd.timestamp_source = "host" if (is_host and source == "manual" and rnd.status == "ready") else source
    rnd.status = "ready"
    pb = rnd.playback or PlaybackSession(round_id=rnd.id)
    pb.state, pb.position_base, pb.started_at, pb.media_status, pb.media_error = "idle", 0, None, "preparing", None
    pb.length = rnd.clip_seconds if rnd.clip_seconds else min(
        float(rnd.max_seconds or 600), max(1.0, _window_of(rnd).duration - seconds))
    if rnd.playback is None:
        db.add(pb)
    db.flush()
    db.refresh(rnd)
    return rnd


def clear_timestamp(db: Session, game: Game, rnd: GameRound) -> GameRound:
    if rnd.status not in ("timestamp", "ready"):
        raise GameError("The round is already revealed.", "bad_state")
    if rnd.status == "ready" and not game_rules(game).host_override:
        raise GameError("Changing the timestamp after it was confirmed is turned off.", "disabled", 403)
    rnd.status, rnd.start_seconds, rnd.timestamp_source, rnd.suggested_seconds = "timestamp", None, None, None
    if rnd.playback is not None:
        db.delete(rnd.playback)
    db.flush()
    db.refresh(rnd)
    return rnd


# ------------------------------------------------------------------ playback state

def position(pb: PlaybackSession, now: float | None = None) -> float:
    now = time.time() if now is None else now
    pos = pb.position_base
    if pb.state == "playing" and pb.started_at:
        pos += max(0.0, now - pb.started_at)
    return min(pos, pb.length or pos)


def settle(pb: PlaybackSession | None, now: float | None = None) -> bool:
    """Lazily end playback that ran past the clip length. Returns True when the state changed."""
    if pb is None or pb.state != "playing" or not pb.length:
        return False
    if position(pb, now) >= pb.length - 0.05:
        pb.state, pb.position_base, pb.started_at = "ended", pb.length, None
        return True
    return False


def control_playback(db: Session, game: Game, rnd: GameRound, action: str, value: float | None = None, *,
                     force: bool = False) -> PlaybackSession:
    pb = rnd.playback
    if pb is None or rnd.status not in ("ready", "revealed"):
        raise GameError("Choose a timestamp first.", "bad_state")
    if pb.media_status != "ready" and action in ("play", "replay"):
        raise GameError("The clip is still being prepared." if pb.media_status == "preparing"
                        else (pb.media_error or "The clip could not be prepared."), "media_not_ready")
    settle(pb)
    now = time.time()
    if action == "play":
        if pb.state == "playing":
            return pb
        if pb.state == "ended":
            return control_playback(db, game, rnd, "replay", force=force)
        if pb.state == "idle":
            pb.plays += 1
        pb.state, pb.started_at = "playing", now
    elif action == "pause":
        if pb.state == "playing":
            pb.position_base, pb.state, pb.started_at = position(pb, now), "paused", None
    elif action == "replay":
        allowed = preset_for(game, rnd.difficulty).replays
        if rnd.status == "ready" and not force and rnd.replays_used >= allowed and pb.plays > 0:
            raise GameError(f"No replays left ({allowed} allowed at this difficulty).", "no_replays")
        if pb.plays > 0:
            rnd.replays_used += 1
        pb.plays += 1
        pb.position_base, pb.state, pb.started_at = 0.0, "playing", now
    elif action == "stop":
        pb.position_base, pb.state, pb.started_at = position(pb, now), "ended", None
    elif action == "seek":
        if value is None:
            raise GameError("Seek needs a position.", "bad_request", 422)
        v = max(0.0, min(float(value), float(pb.length or 0)))
        pb.position_base = v
        pb.started_at = now if pb.state == "playing" else None
        if pb.state == "ended":
            pb.state = "paused"
    else:
        raise GameError("Unknown playback action.", "bad_request", 422)
    db.flush()
    return pb


# ------------------------------------------------------------------ guesses, hints, reveal

def submit_guess(db: Session, game: Game, rnd: GameRound, text: str, player_id: int | None) -> Guess:
    if game.status != "active":
        raise GameError("This game is not running.", "bad_state")
    if rnd.status != "ready":
        raise GameError("Guesses are only open while a round is playing.", "bad_state")
    text = " ".join((text or "").split())[:300]
    if not text:
        raise GameError("Type a guess first.", "empty", 422)
    gp = next((p for p in game.players if p.player_id == player_id), None)
    if gp is None:
        raise GameError("That player is not in this game.", "not_in_game", 403)
    rules = game_rules(game)
    my_turn = (rnd.turn_player_id is None or rnd.turn_player_id == player_id
               or (game.team_mode and gp.team_id is not None and gp.team_id == rnd.turn_team_id))
    if rules.guess_mode == "turn_only" and not my_turn:
        raise GameError("Only the player (or team) whose turn it is can guess this round.", "not_your_turn", 403)
    if any(g.verdict == "correct" and (g.player_id == player_id or (game.team_mode and gp.team_id
                                                                      and g.team_id == gp.team_id))
           for g in rnd.guesses):
        raise GameError("You already got this one.", "already_correct")
    attempts = sum(1 for g in rnd.guesses if g.player_id == player_id) + 1
    g = Guess(round_id=rnd.id, player_id=player_id, team_id=gp.team_id, text=text, attempt_number=attempts)
    db.add(g)
    db.flush()
    db.refresh(rnd)
    return g


def judge_guess(db: Session, game: Game, rnd: GameRound, guess_id: int, verdict: str) -> Guess:
    if verdict not in ("correct", "incorrect", "pending"):
        raise GameError("Verdict must be correct or incorrect.", "bad_request", 422)
    g = db.get(Guess, guess_id)
    if g is None or g.round_id != rnd.id:
        raise GameError("Guess not found.", "not_found", 404)
    if rnd.status not in ("ready", "revealed"):
        raise GameError("This round is closed.", "bad_state")
    if g.verdict == verdict:
        return g  # idempotent
    cfg = scoring_cfg(game)
    # changing a previous judgement removes what it awarded
    if g.verdict == "correct":
        who = f"t{g.team_id}" if game.team_mode and g.team_id else f"p{g.player_id}"
        scoring.revoke_round_events(db, rnd, f"r{rnd.id}:correct:{who}")
        if game.team_mode and g.team_id:
            scoring.revoke_round_events(db, rnd, f"r{rnd.id}:team_bonus:t{g.team_id}")
    if g.verdict == "incorrect":
        scoring.revoke_round_events(db, rnd, f"r{rnd.id}:incorrect:g{g.id}")
    g.verdict, g.judged_at = verdict, utcnow()
    db.flush()
    if verdict == "correct":
        try:
            scoring.award_correct(db, game, rnd, g, cfg)
        except scoring.DuplicateScore as e:
            g.verdict = "pending"
            db.flush()
            raise GameError("Points for this answer were already awarded.", "duplicate_score") from e
        if rnd.outcome != "correct":
            rnd.outcome = "correct"
        pb = rnd.playback
        if pb is not None and rnd.clip_seconds is None and pb.state == "playing":  # continuous mode stops
            pb.position_base, pb.state, pb.started_at = position(pb), "ended", None
    elif verdict == "incorrect":
        scoring.penalise_incorrect(db, game, rnd, g, cfg)
    if verdict != "correct" and not any(x.verdict == "correct" for x in rnd.guesses):
        rnd.outcome = None
    db.flush()
    return g


def hint_texts(movie: Movie) -> list[str]:
    out: list[str] = []
    if movie.release_year:
        out.append(f"Released in the {categories.decade_of(movie.release_year)}s")
    if movie.genres:
        out.append("Genre: " + ", ".join(g.name for g in movie.genres[:2]))
    directors = [c.person.name for c in movie.credits if c.role == "director"]
    if directors:
        out.append(f"Director's name starts with “{directors[0][0]}”")
    cast = [c for c in movie.credits if c.role == "cast"]
    if cast:
        out.append(f"Starring {cast[0].person.name}")
    if movie.release_year:
        out.append(f"Released in {movie.release_year}")
    return out


def use_hint(db: Session, game: Game, rnd: GameRound, *, force: bool = False) -> str:
    if rnd.status != "ready":
        raise GameError("Hints are available while the round is playing.", "bad_state")
    rules = game_rules(game)
    if not rules.hints_enabled and not force:
        raise GameError("Hints are turned off for this game.", "disabled", 403)
    limit = preset_for(game, rnd.difficulty).hints
    used = list(rnd.hints_used or [])
    if len(used) >= limit and not force:
        raise GameError(f"No hints left ({limit} at this difficulty).", "no_hints")
    pool = hint_texts(rnd.movie)
    if len(used) >= len(pool):
        raise GameError("No more hints available for this movie.", "no_hints")
    used.append(pool[len(used)])
    rnd.hints_used = used
    db.flush()
    return used[-1]


def reveal(db: Session, game: Game, rnd: GameRound) -> GameRound:
    if rnd.status == "revealed":
        return rnd
    if rnd.status not in ("timestamp", "ready"):
        raise GameError("This round cannot be revealed now.", "bad_state")
    rnd.status, rnd.revealed_at = "revealed", utcnow()
    if not any(g.verdict == "correct" for g in rnd.guesses):
        rnd.outcome = "missed"
    pb = rnd.playback
    if pb is not None and pb.state == "playing" and rnd.clip_seconds is None:
        pb.position_base, pb.state, pb.started_at = position(pb), "ended", None
    db.flush()
    return rnd


def award_bonus(db: Session, game: Game, rnd: GameRound, kind: str, *, player_id: int | None,
                team_id: int | None, points: float | None = None) -> ScoreEvent:
    cfg = scoring_cfg(game)
    defaults = {"year": cfg.year_bonus, "actor": cfg.actor_bonus, "team": cfg.team_bonus}
    if kind not in ("year", "actor", "team", "round"):
        raise GameError("Unknown bonus.", "bad_request", 422)
    if points is None:
        if kind not in defaults:
            raise GameError("Enter the bonus points.", "bad_request", 422)
        points = defaults[kind]
    if player_id is None and team_id is None:
        raise GameError("Choose who gets the bonus.", "bad_request", 422)
    if player_id is not None and not any(p.player_id == player_id for p in game.players):
        raise GameError("That player is not in this game.", "not_in_game", 422)
    if team_id is None and player_id is not None:
        team_id = scoring.team_of(db, game, player_id)
    who = f"p{player_id}" if player_id is not None else f"t{team_id}"
    try:
        return scoring.add_event(db, game=game, rnd=rnd, key=f"r{rnd.id}:bonus_{kind}:{who}", points=float(points),
                                 reason=f"{kind}_bonus", player_id=player_id, team_id=team_id)
    except scoring.DuplicateScore as e:
        raise GameError("That bonus was already given this round.", "duplicate_score") from e


def adjust_score(db: Session, game: Game, *, player_id: int | None, team_id: int | None, points: float,
                 note: str | None, key: str) -> ScoreEvent:
    if not key or len(key) < 8:
        raise GameError("Missing request id.", "bad_request", 422)
    if player_id is None and team_id is None:
        raise GameError("Choose a player or team.", "bad_request", 422)
    if team_id is None and player_id is not None:
        team_id = scoring.team_of(db, game, player_id)
    try:
        return scoring.add_event(db, game=game, key=f"g{game.id}:manual:{key}", points=float(points),
                                 reason="manual", player_id=player_id, team_id=team_id, note=note)
    except scoring.DuplicateScore as e:
        raise GameError("This adjustment was already applied.", "duplicate_score") from e


def close_round(db: Session, game: Game, rnd: GameRound, *, skip: bool = False) -> GameRound:
    if rnd.status in ("closed", "skipped"):
        return rnd
    if skip:
        rnd.status, rnd.outcome = "skipped", "skipped"
    else:
        if rnd.status != "revealed":
            reveal(db, game, rnd)
        rnd.status = "closed"
    rnd.closed_at = utcnow()
    if rnd.playback is not None:
        rnd.playback.state, rnd.playback.started_at = "ended", None
    db.flush()
    return rnd


# ------------------------------------------------------------------ views

def _iso(d: datetime | None) -> str | None:
    if d is None:
        return None
    return (d if d.tzinfo else d.replace(tzinfo=UTC)).isoformat()


def movie_reveal(movie: Movie, rnd: GameRound | None = None) -> dict[str, Any]:
    directors = [c.person.name for c in movie.credits if c.role == "director"]
    cast = [c.person.name for c in movie.credits if c.role == "cast"][:5]
    return {
        "title": movie.title, "original_title": movie.original_title, "release_year": movie.release_year,
        "poster_path": movie.poster_path, "backdrop_path": movie.backdrop_path,
        "vote_average": movie.vote_average, "genres": [g.name for g in movie.genres], "directors": directors,
        "cast": cast, "overview": movie.overview, "tmdb_id": movie.tmdb_id, "runtime": movie.runtime,
        "collection": movie.collection.name if movie.collection else None,
        "start_seconds": rnd.start_seconds if rnd else None,
    }


def _playback_view(rnd: GameRound, show_stream: bool) -> dict[str, Any] | None:
    pb = rnd.playback
    if pb is None:
        return None
    return {"state": pb.state, "media_status": pb.media_status, "media_error": pb.media_error,
            "position": round(position(pb), 3), "position_base": pb.position_base, "started_at": pb.started_at,
            "length": pb.length, "server_time": time.time(), "plays": pb.plays,
            "stream_url": f"/api/stream/{rnd.token}/index.m3u8" if show_stream and pb.media_status == "ready"
            else None}


def _guess_view(g: Guess, names: dict[int, str], *, full: bool) -> dict[str, Any]:
    d = {"id": g.id, "player_id": g.player_id, "player": names.get(g.player_id or -1, "?"), "team_id": g.team_id,
         "verdict": g.verdict, "attempt": g.attempt_number, "submitted_at": _iso(g.submitted_at)}
    if full:
        d["text"] = g.text
    return d


def round_view(db: Session, game: Game, rnd: GameRound, *, is_host: bool, player_id: int | None) -> dict[str, Any]:
    names = {gp.player_id: gp.player.name for gp in game.players}
    revealed = rnd.status in ("revealed", "closed") or (rnd.status == "skipped" and rnd.revealed_at is not None)
    pb_cfg = playback_cfg(game)
    show_stream = is_host or bool(pb_cfg.get("video_on_player_devices", True))
    can_pick = can_pick_timestamp(game, rnd, is_host=is_host, player_id=player_id)
    rules = game_rules(game)
    v: dict[str, Any] = {
        "token": rnd.token, "number": rnd.number, "status": rnd.status, "category_label": rnd.category_label,
        "difficulty": rnd.difficulty, "turn_player_id": rnd.turn_player_id,
        "turn_player": names.get(rnd.turn_player_id or -1), "turn_team_id": rnd.turn_team_id,
        "window": {"min": rnd.allowed_min, "max": rnd.allowed_max} if (can_pick or is_host) else None,
        "clip_seconds": rnd.clip_seconds, "max_seconds": rnd.max_seconds,
        "mode": "continuous" if rnd.clip_seconds is None else "clip",
        "start_seconds": rnd.start_seconds, "timestamp_source": rnd.timestamp_source,
        "suggested_seconds": rnd.suggested_seconds if can_pick else None,
        "can_pick_timestamp": can_pick and rnd.status == "timestamp",
        "timestamp_options": {"manual": rules.timestamp_manual or is_host, "random": rules.timestamp_random},
        "hints": list(rnd.hints_used or []),
        "hints_left": max(0, preset_for(game, rnd.difficulty).hints - len(rnd.hints_used or []))
        if rules.hints_enabled else 0,
        "replays_left": max(0, preset_for(game, rnd.difficulty).replays - rnd.replays_used),
        "playback": _playback_view(rnd, show_stream),
        "outcome": rnd.outcome,
        "guesses": [_guess_view(g, names, full=is_host or revealed or g.player_id == player_id)
                    for g in rnd.guesses],
        "revealed": revealed,
        "answer": movie_reveal(rnd.movie, rnd) if revealed else None,
    }
    if is_host:
        f = rnd.movie_file
        v["host"] = {"movie": movie_reveal(rnd.movie, rnd), "movie_id": rnd.movie_id,
                     "duration": f.duration if f else None,
                     "file_name": f.filename if f else None, "next_hint": _next_hint(game, rnd)}
    return v


def _next_hint(game: Game, rnd: GameRound) -> str | None:
    pool = hint_texts(rnd.movie)
    n = len(rnd.hints_used or [])
    return pool[n] if n < len(pool) else None


def game_view(db: Session, game: Game, *, is_host: bool, player_id: int | None = None,
              online: dict[int, int] | None = None) -> dict[str, Any]:
    for r in game.rounds:
        if settle(r.playback):
            db.commit()
    cur = current_round(game) or (last_round(game) if game.status == "active" else None)
    board = scoring.scoreboard(db, game)
    online = online or {}
    players = [{"id": gp.player_id, "name": gp.player.name, "color": gp.player.color, "team_id": gp.team_id,
                "team": gp.team.name if gp.team else None, "online": online.get(gp.player_id, 0) > 0}
               for gp in game.players]
    teams = []
    if game.team_mode:
        for tid in sorted({gp.team_id for gp in game.players if gp.team_id}):
            t = db.get(Team, tid)
            if t:
                teams.append({"id": t.id, "name": t.name, "color": t.color})
    history = []
    for r in game.rounds:
        if r.status in ("closed", "skipped") or (cur is not None and r.id != cur.id and r.status == "revealed"):
            revealed = r.revealed_at is not None
            item = {"number": r.number, "status": r.status, "outcome": r.outcome,
                    "start_seconds": r.start_seconds,
                    "winners": [g.player_id for g in r.guesses if g.verdict == "correct"]}
            if revealed or is_host:
                item["title"] = r.movie.title
                item["release_year"] = r.movie.release_year
                item["poster_path"] = r.movie.poster_path
            history.append(item)
    view = {
        "id": game.id, "name": game.name, "mode": game.mode, "mode_label": MODES.get(game.mode, game.mode),
        "status": game.status, "team_mode": game.team_mode,
        "difficulty": game.config.get("difficulty"), "playback_mode": playback_cfg(game).get("mode"),
        "clip_seconds": playback_cfg(game).get("clip_seconds"),
        "rounds_planned": game.config.get("rounds_planned"), "rounds_played": sum(
            1 for r in game.rounds if r.status in ("closed", "skipped")),
        "filter_labels": game.config.get("filter_labels") or [], "logic": game.config.get("logic"),
        "players": players, "teams": teams, "scoreboard": board,
        "round": round_view(db, game, cur, is_host=is_host, player_id=player_id) if cur else None,
        "history": history, "started_at": _iso(game.started_at), "finished_at": _iso(game.finished_at),
        "video_on_player_devices": bool(playback_cfg(game).get("video_on_player_devices", True)),
        "guess_mode": game_rules(game).guess_mode,
    }
    if is_host:
        view["join_code"] = game.join_code
        view["config"] = game.config
    return view
