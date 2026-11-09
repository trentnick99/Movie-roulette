"""Relational model.

Persistent library metadata (movies, files, people, categories ...) is kept separate from game data (games,
rounds, guesses, scores, playback sessions). Movie files are only ever *referenced*: nothing here renames,
moves or deletes a file on disk.
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def utcnow() -> datetime:
    return datetime.now(UTC)


TS = DateTime(timezone=True)


# ---------------------------------------------------------------- library

class LibraryDirectory(Base):
    __tablename__ = "library_directories"
    id: Mapped[int] = mapped_column(primary_key=True)
    path: Mapped[str] = mapped_column(String(1024), unique=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    excluded_paths: Mapped[list[str]] = mapped_column(JSON, default=list)
    added_at: Mapped[datetime] = mapped_column(TS, default=utcnow)
    last_scanned_at: Mapped[datetime | None] = mapped_column(TS)


class TMDBCollection(Base):
    __tablename__ = "tmdb_collections"
    id: Mapped[int] = mapped_column(primary_key=True)
    tmdb_id: Mapped[int] = mapped_column(Integer, unique=True)
    name: Mapped[str] = mapped_column(String(300))
    poster_path: Mapped[str | None] = mapped_column(String(200))
    backdrop_path: Mapped[str | None] = mapped_column(String(200))


class Movie(Base):
    __tablename__ = "movies"
    __table_args__ = (
        CheckConstraint("metadata_status IN ('unmatched','needs_review','matched','failed')", name="ck_movie_status"),
        Index("ix_movies_status", "metadata_status"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    tmdb_id: Mapped[int | None] = mapped_column(Integer, unique=True)
    title: Mapped[str] = mapped_column(String(500))
    original_title: Mapped[str | None] = mapped_column(String(500))
    release_date: Mapped[str | None] = mapped_column(String(10))
    release_year: Mapped[int | None] = mapped_column(Integer, index=True)
    overview: Mapped[str | None] = mapped_column(Text)
    runtime: Mapped[int | None] = mapped_column(Integer)  # minutes, from TMDB
    poster_path: Mapped[str | None] = mapped_column(String(200))
    backdrop_path: Mapped[str | None] = mapped_column(String(200))
    original_language: Mapped[str | None] = mapped_column(String(10))
    popularity: Mapped[float | None] = mapped_column(Float)
    vote_average: Mapped[float | None] = mapped_column(Float)
    vote_count: Mapped[int | None] = mapped_column(Integer)
    keywords: Mapped[list[str]] = mapped_column(JSON, default=list)
    collection_id: Mapped[int | None] = mapped_column(ForeignKey("tmdb_collections.id", ondelete="SET NULL"),
                                                      index=True)

    metadata_status: Mapped[str] = mapped_column(String(20), default="unmatched")
    match_confidence: Mapped[float | None] = mapped_column(Float)
    match_source: Mapped[str | None] = mapped_column(String(10))  # auto | manual
    confirmed: Mapped[bool] = mapped_column(Boolean, default=False)
    parsed_title: Mapped[str | None] = mapped_column(String(500))
    parsed_year: Mapped[int | None] = mapped_column(Integer)
    excluded_from_game: Mapped[bool] = mapped_column(Boolean, default=False)
    metadata_updated_at: Mapped[datetime | None] = mapped_column(TS)
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(TS, default=utcnow, onupdate=utcnow)

    files: Mapped[list[MovieFile]] = relationship(back_populates="movie")
    collection: Mapped[TMDBCollection | None] = relationship()
    genres: Mapped[list[Genre]] = relationship(secondary="movie_genres", order_by="Genre.name")
    credits: Mapped[list[MoviePerson]] = relationship(back_populates="movie", cascade="all, delete-orphan",
                                                      order_by="MoviePerson.credit_order")
    companies: Mapped[list[ProductionCompany]] = relationship(secondary="movie_production_companies")
    candidates: Mapped[list[MatchCandidate]] = relationship(back_populates="movie", cascade="all, delete-orphan",
                                                            order_by="MatchCandidate.score.desc()")


class MovieFile(Base):
    __tablename__ = "movie_files"
    __table_args__ = (
        CheckConstraint("status IN ('ok','missing','inaccessible','corrupt','unsupported')", name="ck_file_status"),
        Index("ix_files_fingerprint", "fingerprint"),
        Index("ix_files_status", "status"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    movie_id: Mapped[int | None] = mapped_column(ForeignKey("movies.id", ondelete="SET NULL"), index=True)
    directory_id: Mapped[int | None] = mapped_column(ForeignKey("library_directories.id", ondelete="SET NULL"))
    path: Mapped[str] = mapped_column(String(2048), unique=True)
    filename: Mapped[str] = mapped_column(String(1024))
    size: Mapped[int] = mapped_column(Integer, default=0)
    mtime: Mapped[float] = mapped_column(Float, default=0)
    fingerprint: Mapped[str | None] = mapped_column(String(64))
    duration: Mapped[float | None] = mapped_column(Float)  # seconds, measured with ffprobe
    container: Mapped[str | None] = mapped_column(String(100))
    video_codec: Mapped[str | None] = mapped_column(String(40))
    audio_codec: Mapped[str | None] = mapped_column(String(40))
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)
    pix_fmt: Mapped[str | None] = mapped_column(String(40))
    audio_streams: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    chapters: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(20), default="ok")
    error: Mapped[str | None] = mapped_column(Text)
    duplicate_of_id: Mapped[int | None] = mapped_column(ForeignKey("movie_files.id", ondelete="SET NULL"))
    first_seen: Mapped[datetime] = mapped_column(TS, default=utcnow)
    last_scanned_at: Mapped[datetime | None] = mapped_column(TS)

    movie: Mapped[Movie | None] = relationship(back_populates="files")


class Genre(Base):
    __tablename__ = "genres"
    id: Mapped[int] = mapped_column(primary_key=True)  # TMDB genre id
    name: Mapped[str] = mapped_column(String(100), unique=True)


class MovieGenre(Base):
    __tablename__ = "movie_genres"
    movie_id: Mapped[int] = mapped_column(ForeignKey("movies.id", ondelete="CASCADE"), primary_key=True)
    genre_id: Mapped[int] = mapped_column(ForeignKey("genres.id", ondelete="CASCADE"), primary_key=True, index=True)


class Person(Base):
    __tablename__ = "people"
    id: Mapped[int] = mapped_column(primary_key=True)
    tmdb_id: Mapped[int] = mapped_column(Integer, unique=True)
    name: Mapped[str] = mapped_column(String(300), index=True)
    profile_path: Mapped[str | None] = mapped_column(String(200))
    known_for_department: Mapped[str | None] = mapped_column(String(60))


class MoviePerson(Base):
    __tablename__ = "movie_people"
    __table_args__ = (UniqueConstraint("movie_id", "person_id", "role", name="uq_movie_person_role"),
                      Index("ix_movie_people_person", "person_id", "role"))
    id: Mapped[int] = mapped_column(primary_key=True)
    movie_id: Mapped[int] = mapped_column(ForeignKey("movies.id", ondelete="CASCADE"))
    person_id: Mapped[int] = mapped_column(ForeignKey("people.id", ondelete="CASCADE"))
    role: Mapped[str] = mapped_column(String(20))  # cast | director
    character: Mapped[str | None] = mapped_column(String(500))
    credit_order: Mapped[int] = mapped_column(Integer, default=0)

    movie: Mapped[Movie] = relationship(back_populates="credits")
    person: Mapped[Person] = relationship()


class ProductionCompany(Base):
    __tablename__ = "production_companies"
    id: Mapped[int] = mapped_column(primary_key=True)
    tmdb_id: Mapped[int] = mapped_column(Integer, unique=True)
    name: Mapped[str] = mapped_column(String(300), index=True)
    logo_path: Mapped[str | None] = mapped_column(String(200))
    origin_country: Mapped[str | None] = mapped_column(String(10))


class MovieProductionCompany(Base):
    __tablename__ = "movie_production_companies"
    movie_id: Mapped[int] = mapped_column(ForeignKey("movies.id", ondelete="CASCADE"), primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("production_companies.id", ondelete="CASCADE"),
                                            primary_key=True, index=True)


class MatchCandidate(Base):
    __tablename__ = "match_candidates"
    __table_args__ = (UniqueConstraint("movie_id", "tmdb_id", name="uq_candidate"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    movie_id: Mapped[int] = mapped_column(ForeignKey("movies.id", ondelete="CASCADE"), index=True)
    tmdb_id: Mapped[int] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(String(500))
    original_title: Mapped[str | None] = mapped_column(String(500))
    release_year: Mapped[int | None] = mapped_column(Integer)
    overview: Mapped[str | None] = mapped_column(Text)
    poster_path: Mapped[str | None] = mapped_column(String(200))
    popularity: Mapped[float | None] = mapped_column(Float)
    score: Mapped[float] = mapped_column(Float, default=0)
    signals: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)

    movie: Mapped[Movie] = relationship(back_populates="candidates")


class TMDBCache(Base):
    __tablename__ = "tmdb_cache"
    key: Mapped[str] = mapped_column(String(500), primary_key=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    fetched_at: Mapped[datetime] = mapped_column(TS, default=utcnow)


class Category(Base):
    __tablename__ = "categories"
    __table_args__ = (
        UniqueConstraint("kind", "ref_id", name="uq_category_entity"),
        CheckConstraint("kind IN ('genre','decade','collection','studio','custom')", name="ck_category_kind"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(20), index=True)
    name: Mapped[str] = mapped_column(String(200))
    slug: Mapped[str] = mapped_column(String(220), unique=True)
    ref_id: Mapped[int | None] = mapped_column(Integer)  # genre id / decade start / collection id / company id
    rules: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)  # custom categories only
    description: Mapped[str | None] = mapped_column(Text)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    hidden: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)


class MovieCategory(Base):
    """Explicit membership. For automatic categories only 'exclude' rows are stored (membership itself is derived
    from TMDB data); custom categories store both hand-picked 'include' rows and 'exclude' rows."""
    __tablename__ = "movie_categories"
    __table_args__ = (CheckConstraint("mode IN ('include','exclude')", name="ck_mc_mode"),)
    category_id: Mapped[int] = mapped_column(ForeignKey("categories.id", ondelete="CASCADE"), primary_key=True)
    movie_id: Mapped[int] = mapped_column(ForeignKey("movies.id", ondelete="CASCADE"), primary_key=True, index=True)
    mode: Mapped[str] = mapped_column(String(10), default="include")
    added_at: Mapped[datetime] = mapped_column(TS, default=utcnow)


class LibraryScan(Base):
    __tablename__ = "library_scans"
    id: Mapped[int] = mapped_column(primary_key=True)
    trigger: Mapped[str] = mapped_column(String(20), default="manual")
    status: Mapped[str] = mapped_column(String(20), default="running")  # running|completed|failed|cancelled
    phase: Mapped[str] = mapped_column(String(40), default="discovering")
    started_at: Mapped[datetime] = mapped_column(TS, default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(TS)
    progress_current: Mapped[int] = mapped_column(Integer, default=0)
    progress_total: Mapped[int] = mapped_column(Integer, default=0)
    counts: Mapped[dict[str, int]] = mapped_column(JSON, default=dict)
    message: Mapped[str | None] = mapped_column(Text)


# ---------------------------------------------------------------- people playing

class Team(Base):
    __tablename__ = "teams"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    color: Mapped[str] = mapped_column(String(20), default="#e0b04a")
    max_size: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)


class Player(Base):
    __tablename__ = "players"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(60), unique=True)
    color: Mapped[str] = mapped_column(String(20), default="#e0b04a")
    team_id: Mapped[int | None] = mapped_column(ForeignKey("teams.id", ondelete="SET NULL"))
    archived: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)

    team: Mapped[Team | None] = relationship()


class Game(Base):
    __tablename__ = "games"
    __table_args__ = (CheckConstraint("status IN ('setup','active','finished','abandoned')", name="ck_game_status"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    mode: Mapped[str] = mapped_column(String(30))
    status: Mapped[str] = mapped_column(String(20), default="setup", index=True)
    team_mode: Mapped[bool] = mapped_column(Boolean, default=False)
    config: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    join_code: Mapped[str | None] = mapped_column(String(12), index=True)
    turn_index: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(TS)
    finished_at: Mapped[datetime | None] = mapped_column(TS)

    players: Mapped[list[GamePlayer]] = relationship(back_populates="game", cascade="all, delete-orphan",
                                                     order_by="GamePlayer.turn_order")
    rounds: Mapped[list[GameRound]] = relationship(back_populates="game", cascade="all, delete-orphan",
                                                   order_by="GameRound.number")


class GamePlayer(Base):
    __tablename__ = "game_players"
    __table_args__ = (UniqueConstraint("game_id", "player_id", name="uq_game_player"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    game_id: Mapped[int] = mapped_column(ForeignKey("games.id", ondelete="CASCADE"), index=True)
    player_id: Mapped[int] = mapped_column(ForeignKey("players.id", ondelete="CASCADE"))
    team_id: Mapped[int | None] = mapped_column(ForeignKey("teams.id", ondelete="SET NULL"))
    turn_order: Mapped[int] = mapped_column(Integer, default=0)
    joined_at: Mapped[datetime] = mapped_column(TS, default=utcnow)

    game: Mapped[Game] = relationship(back_populates="players")
    player: Mapped[Player] = relationship()
    team: Mapped[Team | None] = relationship()


class GameRound(Base):
    __tablename__ = "game_rounds"
    __table_args__ = (
        UniqueConstraint("game_id", "number", name="uq_round_number"),
        CheckConstraint("status IN ('timestamp','ready','revealed','closed','skipped')", name="ck_round_status"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    game_id: Mapped[int] = mapped_column(ForeignKey("games.id", ondelete="CASCADE"), index=True)
    number: Mapped[int] = mapped_column(Integer)
    token: Mapped[str] = mapped_column(String(64), unique=True)  # the only id players ever see
    movie_id: Mapped[int] = mapped_column(ForeignKey("movies.id", ondelete="RESTRICT"), index=True)
    movie_file_id: Mapped[int | None] = mapped_column(ForeignKey("movie_files.id", ondelete="SET NULL"))
    status: Mapped[str] = mapped_column(String(20), default="timestamp")
    turn_player_id: Mapped[int | None] = mapped_column(ForeignKey("players.id", ondelete="SET NULL"))
    turn_team_id: Mapped[int | None] = mapped_column(ForeignKey("teams.id", ondelete="SET NULL"))
    category_label: Mapped[str | None] = mapped_column(String(300))
    difficulty: Mapped[str] = mapped_column(String(10), default="medium")
    allowed_min: Mapped[float] = mapped_column(Float, default=0)
    allowed_max: Mapped[float] = mapped_column(Float, default=0)
    timestamp_source: Mapped[str | None] = mapped_column(String(10))  # manual | random | host
    suggested_seconds: Mapped[float | None] = mapped_column(Float)
    start_seconds: Mapped[float | None] = mapped_column(Float)
    clip_seconds: Mapped[float | None] = mapped_column(Float)  # None = continuous
    max_seconds: Mapped[float | None] = mapped_column(Float)  # continuous mode cap
    hints_used: Mapped[list[str]] = mapped_column(JSON, default=list)
    replays_used: Mapped[int] = mapped_column(Integer, default=0)
    outcome: Mapped[str | None] = mapped_column(String(20))  # correct | missed | skipped
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)
    revealed_at: Mapped[datetime | None] = mapped_column(TS)
    closed_at: Mapped[datetime | None] = mapped_column(TS)

    game: Mapped[Game] = relationship(back_populates="rounds")
    movie: Mapped[Movie] = relationship()
    movie_file: Mapped[MovieFile | None] = relationship()
    guesses: Mapped[list[Guess]] = relationship(back_populates="round", cascade="all, delete-orphan",
                                                order_by="Guess.submitted_at")
    playback: Mapped[PlaybackSession | None] = relationship(back_populates="round", cascade="all, delete-orphan",
                                                            uselist=False)


class Guess(Base):
    __tablename__ = "guesses"
    __table_args__ = (CheckConstraint("verdict IN ('pending','correct','incorrect')", name="ck_guess_verdict"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    round_id: Mapped[int] = mapped_column(ForeignKey("game_rounds.id", ondelete="CASCADE"), index=True)
    player_id: Mapped[int | None] = mapped_column(ForeignKey("players.id", ondelete="SET NULL"))
    team_id: Mapped[int | None] = mapped_column(ForeignKey("teams.id", ondelete="SET NULL"))
    text: Mapped[str] = mapped_column(String(300))
    attempt_number: Mapped[int] = mapped_column(Integer, default=1)
    verdict: Mapped[str] = mapped_column(String(10), default="pending")
    submitted_at: Mapped[datetime] = mapped_column(TS, default=utcnow)
    judged_at: Mapped[datetime | None] = mapped_column(TS)

    round: Mapped[GameRound] = relationship(back_populates="guesses")


class ScoreEvent(Base):
    __tablename__ = "score_events"
    id: Mapped[int] = mapped_column(primary_key=True)
    game_id: Mapped[int] = mapped_column(ForeignKey("games.id", ondelete="CASCADE"), index=True)
    round_id: Mapped[int | None] = mapped_column(ForeignKey("game_rounds.id", ondelete="CASCADE"), index=True)
    player_id: Mapped[int | None] = mapped_column(ForeignKey("players.id", ondelete="SET NULL"))
    team_id: Mapped[int | None] = mapped_column(ForeignKey("teams.id", ondelete="SET NULL"))
    points: Mapped[float] = mapped_column(Float)
    reason: Mapped[str] = mapped_column(String(40))
    note: Mapped[str | None] = mapped_column(String(300))
    # Prevents accidental double scoring (double-clicks, retried requests, two host screens)
    idempotency_key: Mapped[str] = mapped_column(String(200), unique=True)
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)


class PlaybackSession(Base):
    __tablename__ = "playback_sessions"
    __table_args__ = (CheckConstraint("state IN ('idle','playing','paused','ended')", name="ck_playback_state"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    round_id: Mapped[int] = mapped_column(ForeignKey("game_rounds.id", ondelete="CASCADE"), unique=True)
    state: Mapped[str] = mapped_column(String(10), default="idle")
    media_status: Mapped[str] = mapped_column(String(12), default="preparing")  # preparing|ready|error
    media_error: Mapped[str | None] = mapped_column(Text)
    position_base: Mapped[float] = mapped_column(Float, default=0)  # seconds into the clip when last (re)started
    started_at: Mapped[float | None] = mapped_column(Float)  # server epoch seconds when playing began
    length: Mapped[float | None] = mapped_column(Float)  # playable length of this clip/stream
    plays: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = mapped_column(TS, default=utcnow, onupdate=utcnow)

    round: Mapped[GameRound] = relationship(back_populates="playback")


class AuthSession(Base):
    __tablename__ = "auth_sessions"
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    role: Mapped[str] = mapped_column(String(10))  # host | player
    player_id: Mapped[int | None] = mapped_column(ForeignKey("players.id", ondelete="CASCADE"))
    game_id: Mapped[int | None] = mapped_column(ForeignKey("games.id", ondelete="CASCADE"))
    label: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)
    last_seen: Mapped[datetime] = mapped_column(TS, default=utcnow)
    revoked: Mapped[bool] = mapped_column(Boolean, default=False)


class ApplicationSetting(Base):
    __tablename__ = "application_settings"
    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[Any] = mapped_column(JSON)
    updated_at: Mapped[datetime] = mapped_column(TS, default=utcnow, onupdate=utcnow)
