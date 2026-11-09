"""User-editable application settings, stored per section in the application_settings table.

Each section is a Pydantic model with defaults, so a fresh database works without any setup and every update is
validated before it is saved.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy.orm import Session

from ..models import ApplicationSetting

Difficulty = Literal["easy", "medium", "hard"]
RepeatPolicy = Literal["game", "session", "days30", "days90", "none"]


class LibrarySettings(BaseModel):
    auto_rescan_minutes: int = Field(0, ge=0, le=10080)  # 0 = off
    min_duration_minutes: float = Field(20, ge=0, le=600)  # shorter files are treated as samples/extras
    auto_accept_confidence: float = Field(85, ge=50, le=100)
    auto_accept_margin: float = Field(8, ge=0, le=50)
    skip_folder_names: list[str] = Field(default_factory=lambda: [
        "extras", "featurettes", "trailers", "sample", "samples", "behind the scenes", "deleted scenes", "bonus",
        "$recycle.bin", "system volume information"])
    duplicate_preference: Literal["highest_resolution", "largest", "first_found"] = "highest_resolution"


class TMDBSettings(BaseModel):
    language: str = Field("en-US", max_length=10)
    include_adult: bool = False
    cache_days: int = Field(30, ge=1, le=365)
    cast_limit: int = Field(15, ge=1, le=50)


class PlaybackSettings(BaseModel):
    default_mode: Literal["clip", "continuous"] = "clip"
    default_clip_seconds: int = Field(30, ge=3, le=600)
    continuous_max_seconds: int = Field(600, ge=30, le=3600)
    max_resolution: Literal[480, 720, 1080] = 1080
    encoder_preset: Literal["ultrafast", "superfast", "veryfast", "faster", "fast"] = "veryfast"
    default_volume: float = Field(0.8, ge=0, le=1)
    video_on_player_devices: bool = True
    preferred_audio_language: str = Field("eng", max_length=8)


class DifficultyPreset(BaseModel):
    clip_seconds: int = Field(ge=3, le=600)
    replays: int = Field(ge=0, le=20)
    hints: int = Field(ge=0, le=5)
    range_start_pct: float = Field(0, ge=0, le=100)
    range_end_pct: float = Field(100, ge=0, le=100)
    multiplier: float = Field(1.0, ge=0, le=10)

    @model_validator(mode="after")
    def _range(self) -> DifficultyPreset:
        if self.range_end_pct <= self.range_start_pct:
            raise ValueError("range_end_pct must be greater than range_start_pct")
        return self


def _presets() -> dict[str, DifficultyPreset]:
    return {
        "easy": DifficultyPreset(clip_seconds=45, replays=3, hints=3, range_start_pct=10, range_end_pct=90,
                                 multiplier=1),
        "medium": DifficultyPreset(clip_seconds=30, replays=1, hints=1, range_start_pct=0, range_end_pct=100,
                                   multiplier=1.5),
        "hard": DifficultyPreset(clip_seconds=10, replays=0, hints=0, range_start_pct=0, range_end_pct=100,
                                 multiplier=2),
    }


class GameSettings(BaseModel):
    timestamp_manual: bool = True
    timestamp_random: bool = True
    host_override: bool = True
    player_timestamp_from_device: bool = True
    min_timestamp_seconds: float = Field(0, ge=0)
    max_timestamp_seconds: float | None = Field(None, ge=0)
    exclude_opening: bool = False
    opening_seconds: float = Field(180, ge=0, le=3600)
    exclude_ending: bool = False
    ending_seconds: float = Field(600, ge=0, le=3600)
    use_chapters: bool = True
    default_difficulty: Difficulty = "medium"
    difficulty_presets: dict[str, DifficultyPreset] = Field(default_factory=_presets)
    repeat_policy: RepeatPolicy = "game"
    hints_enabled: bool = True
    guess_mode: Literal["everyone", "turn_only"] = "everyone"
    default_rounds: int = Field(10, ge=1, le=200)
    default_category_ids: list[int] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check(self) -> GameSettings:
        if not (self.timestamp_manual or self.timestamp_random):
            raise ValueError("enable manual or random timestamp selection (or both)")
        for k in ("easy", "medium", "hard"):
            if k not in self.difficulty_presets:
                raise ValueError(f"missing difficulty preset '{k}'")
        return self


class ScoringSettings(BaseModel):
    correct: float = Field(10, ge=-1000, le=1000)
    first_attempt_bonus: float = Field(5, ge=-1000, le=1000)
    year_bonus: float = Field(3, ge=-1000, le=1000)
    actor_bonus: float = Field(3, ge=-1000, le=1000)
    team_bonus: float = Field(0, ge=-1000, le=1000)
    hint_penalty: float = Field(2, ge=0, le=1000)
    incorrect_penalty: float = Field(0, ge=0, le=1000)
    apply_difficulty_multiplier: bool = True


class NetworkSettings(BaseModel):
    lan_enabled: bool = False
    join_code_length: int = Field(6, ge=4, le=10)


class AppearanceSettings(BaseModel):
    theme: Literal["cinema", "midnight", "noir"] = "cinema"
    animations: bool = True
    poster_size: Literal["small", "medium", "large"] = "medium"
    show_ratings_in_library: bool = True


class HistorySettings(BaseModel):
    reset_at: str | None = None  # ISO timestamp; rounds before it are ignored by repeat rules

    @field_validator("reset_at")
    @classmethod
    def _iso(cls, v: str | None) -> str | None:
        if v is None:
            return v
        from datetime import datetime

        datetime.fromisoformat(v)
        return v


SECTIONS: dict[str, type[BaseModel]] = {
    "library": LibrarySettings,
    "tmdb": TMDBSettings,
    "playback": PlaybackSettings,
    "game": GameSettings,
    "scoring": ScoringSettings,
    "network": NetworkSettings,
    "appearance": AppearanceSettings,
    "history": HistorySettings,
}


def get_section(db: Session, name: str) -> BaseModel:
    model = SECTIONS[name]
    row = db.get(ApplicationSetting, name)
    if row is None or not isinstance(row.value, dict):
        return model()
    try:
        return model.model_validate(row.value)
    except Exception:  # a stored value from an older version that no longer validates: fall back field by field
        base = model().model_dump()
        base.update({k: v for k, v in row.value.items() if k in base})
        try:
            return model.model_validate(base)
        except Exception:
            return model()


def update_section(db: Session, name: str, patch: dict) -> BaseModel:
    current = get_section(db, name).model_dump()
    current.update(patch)
    value = SECTIONS[name].model_validate(current)
    row = db.get(ApplicationSetting, name)
    if row is None:
        db.add(ApplicationSetting(key=name, value=value.model_dump(mode="json")))
    else:
        row.value = value.model_dump(mode="json")
    db.flush()
    return value


def all_sections(db: Session) -> dict[str, dict]:
    return {k: get_section(db, k).model_dump(mode="json") for k in SECTIONS}


# typed helpers ---------------------------------------------------------------

def library(db: Session) -> LibrarySettings:
    return get_section(db, "library")  # type: ignore[return-value]


def tmdb(db: Session) -> TMDBSettings:
    return get_section(db, "tmdb")  # type: ignore[return-value]


def playback(db: Session) -> PlaybackSettings:
    return get_section(db, "playback")  # type: ignore[return-value]


def game(db: Session) -> GameSettings:
    return get_section(db, "game")  # type: ignore[return-value]


def scoring(db: Session) -> ScoringSettings:
    return get_section(db, "scoring")  # type: ignore[return-value]


def network(db: Session) -> NetworkSettings:
    return get_section(db, "network")  # type: ignore[return-value]


def history(db: Session) -> HistorySettings:
    return get_section(db, "history")  # type: ignore[return-value]
