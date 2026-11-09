"""Round streams and cached TMDB artwork.

There is deliberately no endpoint that takes a file path: round streams are looked up by round token, and the
source file was validated against the configured movie folders when the clip was prepared.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from .. import security as sec
from ..context import ctx
from ..db import get_db
from ..models import GameRound
from ..services import games as G
from ..services.tmdb import TMDBError, fetch_image
from .common import fail

router = APIRouter(prefix="/api", tags=["media"])

HLS_TYPES = {".m3u8": "application/vnd.apple.mpegurl", ".ts": "video/mp2t"}


@router.get("/stream/{token}/{name}")
def stream_file(token: str, name: str, request: Request, db: Session = Depends(get_db)):
    rnd = db.query(GameRound).filter_by(token=token).first()
    if rnd is None or rnd.status not in ("ready", "revealed"):
        fail(404, "not_found", "Stream not available.")
    host = sec.resolve(db, request.cookies.get(sec.HOST_COOKIE), "host")
    if host is None:
        player = sec.resolve(db, request.cookies.get(sec.PLAYER_COOKIE), "player")
        if player is None or not sec.player_in_game(db, player, rnd.game_id):
            fail(403, "forbidden", "Join the game to watch.")
        if not G.playback_cfg(rnd.game).get("video_on_player_devices", True):
            fail(403, "video_off", "Video is shown on the main screen only.")
    path = ctx().streams.file_path(token, name)
    if path is None:
        fail(404, "not_found", "Not ready yet.")
    ext = path.suffix
    headers = {"Cache-Control": "no-store" if ext == ".m3u8" else "private, max-age=3600",
               "X-Content-Type-Options": "nosniff"}
    return FileResponse(path, media_type=HLS_TYPES.get(ext, "application/octet-stream"), headers=headers)


@router.get("/images/{size}/{name}")
def image(size: str, name: str, _: sec.Principal = Depends(sec.require_any)):
    try:
        path = fetch_image(ctx().cfg.image_dir, size, "/" + name, base=ctx().cfg.tmdb_image_base)
    except TMDBError as e:
        fail(404 if e.code in ("not_found", "bad_image") else 502, e.code, e.message)
    media = "image/png" if path.suffix == ".png" else "image/svg+xml" if path.suffix == ".svg" else "image/jpeg"
    return FileResponse(path, media_type=media, headers={"Cache-Control": "private, max-age=2592000"})
