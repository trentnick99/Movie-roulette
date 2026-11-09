"""Settings, status, network, backups and maintenance."""
from __future__ import annotations

import platform
import shutil
import sys
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Body, Depends, Request
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import func
from sqlalchemy.orm import Session

from .. import __version__
from .. import security as sec
from ..context import TMDB_SECRET, ctx
from ..db import get_db
from ..models import Category, Game, LibraryScan, Movie, MovieFile, TMDBCache
from ..services import categories as cats
from ..services import maintenance, settings
from ..services.media_tools import find_tool, tool_version
from ..services.realtime import hub
from ..services.scanner import runner
from ..services.secrets_store import mask
from ..services.streaming import disk_usage
from ..services.tmdb import TMDBClient, TMDBError, image_cache_size
from .common import fail, iso

public = APIRouter(prefix="/api", tags=["system"])
router = APIRouter(prefix="/api", tags=["system"], dependencies=[Depends(sec.require_host)])


@public.get("/health")
def health():
    return {"ok": True, "app": "movie-roulette", "version": __version__}


# ------------------------------------------------------------------ dashboard status

@router.get("/status")
def status(db: Session = Depends(get_db)):
    last = db.query(LibraryScan).order_by(LibraryScan.id.desc()).first()
    matched = db.query(Movie).filter(Movie.metadata_status == "matched").count()
    recent = (db.query(Movie).filter(Movie.metadata_status == "matched").order_by(Movie.created_at.desc())
              .limit(12).all())
    games = db.query(Game).order_by(Game.created_at.desc()).limit(5).all()
    visible_cats = [c for c in cats.ordered_categories(db, include_hidden=False)]
    counts = cats.category_counts(db, visible_cats)
    return {
        "version": __version__,
        "library": {
            "movies": matched,
            "needs_review": db.query(Movie).filter(Movie.metadata_status == "needs_review").count(),
            "unmatched": db.query(Movie).filter(Movie.metadata_status.in_(("unmatched", "failed"))).count(),
            "files": db.query(MovieFile).filter(MovieFile.status == "ok").count(),
            "problem_files": db.query(MovieFile).filter(MovieFile.status != "ok").count(),
            "categories": sum(1 for c in visible_cats if counts.get(c.id)),
        },
        "scan": {"running": runner.busy, "status": last.status if last else None, "phase": last.phase if last else None,
                 "current": last.progress_current if last else 0, "total": last.progress_total if last else 0,
                 "finished_at": iso(last.finished_at) if last else None,
                 "message": last.message if last else None},
        "tmdb": {"configured": bool(ctx().tmdb_credential()), "source": ctx().tmdb_source()},
        "media": {"ffmpeg": bool(find_tool("ffmpeg")), "ffprobe": bool(find_tool("ffprobe"))},
        "connected_players": hub.count("player"),
        "recent_movies": [{"id": m.id, "title": m.title, "release_year": m.release_year,
                           "poster_path": m.poster_path} for m in recent],
        "recent_games": [{"id": g.id, "name": g.name, "status": g.status, "mode": g.mode,
                          "created_at": iso(g.created_at)} for g in games],
        "random_category": _random_category(db, visible_cats, counts),
    }


def _random_category(db: Session, visible: list[Category], counts: dict[int, int]) -> dict | None:
    import random

    pool = [c for c in visible if counts.get(c.id, 0) >= 3]
    if not pool:
        return None
    c = random.SystemRandom().choice(pool)
    return {"id": c.id, "name": c.name, "kind": c.kind, "count": counts[c.id]}


# ------------------------------------------------------------------ settings

@router.get("/settings")
def get_settings(db: Session = Depends(get_db)):
    cred = ctx().tmdb_credential()
    return {
        "sections": settings.all_sections(db),
        "tmdb_key": {"set": bool(cred), "masked": mask(cred), "source": ctx().tmdb_source()},
        "host_password_set": bool(ctx().secrets.get(sec.HOST_PASSWORD_KEY)),
    }


@router.patch("/settings/{section}")
def patch_settings(section: str, patch: dict[str, Any] = Body(...), db: Session = Depends(get_db)):
    if section not in settings.SECTIONS or section == "history":
        fail(404, "not_found", "Unknown settings section.")
    try:
        value = settings.update_section(db, section, patch)
    except ValidationError as e:
        db.rollback()
        msgs = "; ".join(f"{'.'.join(str(x) for x in err['loc'])}: {err['msg']}" for err in e.errors()[:5])
        fail(422, "invalid", msgs or "Invalid settings.")
    db.commit()
    out = {"section": section, "value": value.model_dump(mode="json")}
    if section == "network":
        out["restart_required"] = _lan_restart_needed(db)
    return out


class KeyIn(BaseModel):
    key: str = Field(min_length=10, max_length=600)


@router.post("/settings/tmdb/key")
def set_tmdb_key(body: KeyIn, db: Session = Depends(get_db)):
    """Saves the key only after TMDB accepts it."""
    t = settings.tmdb(db)
    client = TMDBClient(body.key.strip(), language=t.language, cache_get=None, cache_put=None, max_retries=1,
                        base_url=ctx().cfg.tmdb_api_base)
    try:
        client.test_connection()
    except TMDBError as e:
        fail(422 if e.code == "auth" else 502, e.code, e.message)
    finally:
        client.close()
    ctx().secrets.set(TMDB_SECRET, body.key.strip())
    return {"ok": True, "masked": mask(body.key.strip())}


@router.delete("/settings/tmdb/key")
def clear_tmdb_key():
    ctx().secrets.set(TMDB_SECRET, None)
    return {"ok": True, "env_fallback": bool(ctx().cfg.tmdb_api_key)}


@router.post("/settings/tmdb/test")
def test_tmdb(db: Session = Depends(get_db)):
    client = ctx().tmdb_client(db)
    if client is None:
        fail(409, "no_tmdb_key", "No TMDB API key is configured.")
    try:
        return client.test_connection()
    except TMDBError as e:
        fail(502 if e.code != "auth" else 422, e.code, e.message)
    finally:
        client.close()


@router.get("/settings/tmdb/cache")
def cache_info(db: Session = Depends(get_db)):
    n_img, size_img = image_cache_size(ctx().cfg.image_dir)
    return {"responses": db.query(func.count(TMDBCache.key)).scalar(), "images": n_img, "image_bytes": size_img,
            "stream_bytes": disk_usage(ctx().cfg.stream_dir)}


@router.delete("/settings/tmdb/cache")
def clear_cache(db: Session = Depends(get_db), images: bool = True, responses: bool = True):
    if responses:
        db.query(TMDBCache).delete()
        db.commit()
    if images:
        shutil.rmtree(ctx().cfg.image_dir, ignore_errors=True)
        ctx().cfg.image_dir.mkdir(parents=True, exist_ok=True)
    return {"ok": True}


class PasswordIn(BaseModel):
    new_password: str | None = Field(None, max_length=200)
    current_password: str | None = Field(None, max_length=200)


@router.post("/settings/host-password")
def set_password(body: PasswordIn, request: Request):
    stored = ctx().secrets.get(sec.HOST_PASSWORD_KEY)
    if stored and not sec.is_loopback(sec.client_ip(request)) and not sec.verify_password(
            body.current_password or "", stored):
        fail(403, "bad_credentials", "Enter the current host password.")
    if body.new_password:
        if len(body.new_password) < 6:
            fail(422, "weak", "Use at least 6 characters.")
        ctx().secrets.set(sec.HOST_PASSWORD_KEY, sec.hash_password(body.new_password))
    else:
        ctx().secrets.set(sec.HOST_PASSWORD_KEY, None)
    return {"ok": True, "host_password_set": bool(body.new_password)}


@router.post("/settings/history/reset")
def reset_history(db: Session = Depends(get_db)):
    """Repeat rules forget previously played movies (statistics and history are kept)."""
    settings.update_section(db, "history", {"reset_at": datetime.now(UTC).isoformat()})
    db.commit()
    return {"ok": True}


# ------------------------------------------------------------------ network

def _lan_restart_needed(db: Session) -> bool:
    want = settings.network(db).lan_enabled
    bound = ctx().cfg.lan_bind if want else "127.0.0.1"
    return getattr(ctx(), "bound_host", None) not in (None, bound)


@router.get("/network")
def network(request: Request, db: Session = Depends(get_db)):
    net = settings.network(db)
    bound = getattr(ctx(), "bound_host", "127.0.0.1")
    lan_live = bound not in ("127.0.0.1", "localhost", "::1")
    port = ctx().cfg.port
    addrs = maintenance.lan_addresses()
    urls = [f"http://{a}:{port}/join" for a in addrs] if lan_live else []
    games = db.query(Game).filter(Game.status.in_(("setup", "active"))).order_by(Game.created_at.desc()).all()
    return {"lan_enabled": net.lan_enabled, "lan_active": lan_live, "bound_host": bound, "port": port,
            "addresses": addrs, "join_urls": urls, "restart_required": _lan_restart_needed(db),
            "local_url": f"http://127.0.0.1:{port}/", "devices": hub.devices(),
            "active_games": [{"id": g.id, "name": g.name, "join_code": g.join_code} for g in games],
            "host_password_set": bool(ctx().secrets.get(sec.HOST_PASSWORD_KEY))}


@router.post("/system/restart")
def restart():
    server = getattr(ctx(), "server", None)
    if server is None:
        fail(409, "not_supported", "Restart Movie Roulette from its start script to apply this change.")
    ctx().restart_requested = True
    server.should_exit = True
    return {"ok": True}


@router.get("/system/info")
def info(db: Session = Depends(get_db)):
    return {"version": __version__, "python": sys.version.split()[0], "platform": platform.platform(),
            "ffmpeg": tool_version("ffmpeg"), "ffprobe": tool_version("ffprobe"),
            "ffmpeg_path": find_tool("ffmpeg"), "data_dir": str(ctx().cfg.data_dir),
            "database": str(ctx().cfg.db_path)}


# ------------------------------------------------------------------ database maintenance

@router.get("/database/backups")
def backups():
    return maintenance.list_backups(ctx().cfg.backup_dir)


@router.post("/database/backups")
def make_backup():
    p = maintenance.backup(ctx().cfg.db_path, ctx().cfg.backup_dir)
    return {"name": p.name}


class RestoreIn(BaseModel):
    name: str = Field(max_length=100)


@router.post("/database/restore")
def restore(body: RestoreIn):
    if runner.busy:
        fail(409, "busy", "Wait for the library scan to finish.")
    ctx().streams.stop_all()
    try:
        return maintenance.restore(ctx().cfg.db_path, ctx().cfg.backup_dir, body.name)
    except ValueError as e:
        fail(422, "bad_backup", str(e))


@router.get("/database/integrity")
def integrity():
    return maintenance.integrity(ctx().cfg.db_path)
