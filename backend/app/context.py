"""Process-wide services (created once at startup)."""
from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from .config import get_config
from .services import settings
from .services.realtime import hub
from .services.secrets_store import SecretStore
from .services.streaming import StreamManager
from .services.tmdb import TMDBClient

log = logging.getLogger(__name__)

TMDB_SECRET = "tmdb_credential"


class AppContext:
    def __init__(self) -> None:
        cfg = get_config()
        self.cfg = cfg
        self.secrets = SecretStore(cfg.secrets_file)
        self.streams = StreamManager(cfg.stream_dir, on_change=self._stream_changed)
        self.restart_requested = False
        self.bound_host = "127.0.0.1"  # set by the launcher
        self.lan_allowed = False
        self.server = None  # uvicorn.Server, for in-place restarts

    # TMDB ---------------------------------------------------------------
    def tmdb_credential(self) -> str | None:
        return self.secrets.get(TMDB_SECRET) or self.cfg.tmdb_api_key

    def tmdb_source(self) -> str | None:
        if self.secrets.get(TMDB_SECRET):
            return "settings"
        if self.cfg.tmdb_api_key:
            return "environment"
        return None

    def tmdb_client(self, db: Session) -> TMDBClient | None:
        cred = self.tmdb_credential()
        if not cred:
            return None
        t = settings.tmdb(db)
        return TMDBClient(cred, language=t.language, include_adult=t.include_adult, cache_days=t.cache_days,
                          base_url=self.cfg.tmdb_api_base)

    # streams -------------------------------------------------------------
    def _stream_changed(self, token: str, status: str) -> None:
        """Called from the FFmpeg watcher thread: record the media status and push it to all screens."""
        from . import db as dbm
        from .models import GameRound

        job = self.streams.get(token)
        with dbm.session_scope() as s:
            rnd = s.query(GameRound).filter_by(token=token).first()
            if rnd is None or rnd.playback is None:
                return
            rnd.playback.media_status = "ready" if status == "ready" else "error"
            rnd.playback.media_error = job.error if (job and status == "error") else None
            gid = rnd.game_id
        hub.notify(gid)


_ctx: AppContext | None = None


def ctx() -> AppContext:
    global _ctx
    if _ctx is None:
        _ctx = AppContext()
    return _ctx


def reset_ctx() -> None:
    global _ctx
    _ctx = None
