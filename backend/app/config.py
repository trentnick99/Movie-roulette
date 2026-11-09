"""Process-level configuration (paths, ports, bind address).

Everything a user changes while the app runs (scan folders, scoring, playback rules ...) lives in the database
(ApplicationSettings). This module only holds what must be known before the database is opened.
"""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parent.parent
PROJECT_DIR = BACKEND_DIR.parent


class Config(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="MR_",
        env_file=(str(PROJECT_DIR / ".env"),),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    data_dir: Path = PROJECT_DIR / "data"
    port: int = 8765
    # Only used when LAN access is enabled in Settings > Network; otherwise the server binds to 127.0.0.1.
    lan_bind: str = "0.0.0.0"
    frontend_dist: Path = PROJECT_DIR / "frontend" / "dist"
    # Optional explicit tool locations (otherwise PATH and ./tools/ffmpeg/bin are searched).
    ffmpeg_path: str | None = None
    ffprobe_path: str | None = None
    # TMDB credentials may be supplied by environment instead of the Settings screen (useful for CI / headless).
    tmdb_api_key: str | None = None
    # Only change these to point at a test double (the e2e tests run against a local fake TMDB).
    tmdb_api_base: str = "https://api.themoviedb.org/3"
    tmdb_image_base: str = "https://image.tmdb.org/t/p"
    log_level: str = "INFO"
    # Development only: allow the Vite dev server origin.
    dev_origin: str | None = None

    @property
    def db_path(self) -> Path:
        return self.data_dir / "movie_roulette.db"

    @property
    def cache_dir(self) -> Path:
        return self.data_dir / "cache"

    @property
    def image_dir(self) -> Path:
        return self.cache_dir / "images"

    @property
    def stream_dir(self) -> Path:
        return self.cache_dir / "streams"

    @property
    def backup_dir(self) -> Path:
        return self.data_dir / "backups"

    @property
    def log_dir(self) -> Path:
        return self.data_dir / "logs"

    @property
    def secrets_file(self) -> Path:
        return self.data_dir / "secrets.json"

    def ensure_dirs(self) -> None:
        for d in (self.data_dir, self.cache_dir, self.image_dir, self.stream_dir, self.backup_dir, self.log_dir):
            d.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_config() -> Config:
    cfg = Config()
    if os.environ.get("MR_DATA_DIR"):
        cfg.data_dir = Path(os.environ["MR_DATA_DIR"])
    cfg.ensure_dirs()
    return cfg
