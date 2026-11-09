"""FastAPI application factory."""
from __future__ import annotations

import asyncio
import contextlib
import logging
from contextlib import asynccontextmanager
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import __version__, db, migrate
from . import security as sec
from .api import auth, categories_api, games_api, library, media, players, stats_api, system, ws
from .config import get_config
from .context import ctx
from .logging_setup import setup as setup_logging
from .services import settings
from .services.realtime import hub
from .services.scanner import runner

log = logging.getLogger("movie_roulette")
UNSAFE = {"POST", "PUT", "PATCH", "DELETE"}


async def _auto_rescan_loop() -> None:
    """Starts a library rescan every N minutes when enabled in Settings > Library."""
    import time

    last = time.monotonic()
    while True:
        await asyncio.sleep(30)
        try:
            with db.session_scope() as s:
                minutes = settings.library(s).auto_rescan_minutes
            if minutes and time.monotonic() - last >= minutes * 60 and not runner.busy:
                last = time.monotonic()
                with db.session_scope() as s:
                    from .models import LibraryDirectory

                    if s.query(LibraryDirectory).filter_by(enabled=True).count():
                        runner.start("auto", ctx().tmdb_client)
        except Exception:
            log.exception("auto rescan check failed")


def create_app(*, run_migrations: bool = True) -> FastAPI:
    cfg = get_config()
    setup_logging(cfg.log_dir, cfg.log_level)
    if run_migrations:
        migrate.upgrade(cfg.db_path)
    db.init_engine(cfg.db_path)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        hub.bind(asyncio.get_running_loop())
        ctx().streams.cleanup_all()
        task = asyncio.create_task(_auto_rescan_loop())
        log.info("Movie Roulette %s ready", __version__)
        yield
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        runner.stop()
        ctx().streams.stop_all()
        await hub.close_all()
        runner.wait(5)
        log.info("Movie Roulette stopped")

    app = FastAPI(title="Movie Roulette", version=__version__, lifespan=lifespan, docs_url="/api/docs",
                  openapi_url="/api/openapi.json", redoc_url=None)

    @app.middleware("http")
    async def guard(request: Request, call_next):
        # 1. DNS-rebinding protection
        if not sec.host_header_ok(request.headers.get("host")):
            return JSONResponse({"detail": {"code": "bad_host", "message": "Unexpected Host header."}}, 403)
        # 2. LAN access is opt-in: refuse other devices unless enabled (also enforced by the bind address)
        ip = sec.client_ip(request)
        if not sec.is_loopback(ip) and not getattr(ctx(), "lan_allowed", False):
            return JSONResponse({"detail": {"code": "lan_disabled",
                                            "message": "Network access is turned off on the host."}}, 403)
        # 3. Cross-site request protection for state-changing calls
        if request.method in UNSAFE and request.url.path.startswith("/api/"):
            origin = request.headers.get("origin")
            if origin and urlparse(origin).netloc.lower() != (request.headers.get("host") or "").lower() \
                    and origin != cfg.dev_origin:
                return JSONResponse({"detail": {"code": "bad_origin", "message": "Cross-site request refused."}},
                                    403)
            ctype = request.headers.get("content-type", "")
            if int(request.headers.get("content-length") or 0) > 0 and "application/json" not in ctype:
                return JSONResponse({"detail": {"code": "bad_content_type", "message": "JSON required."}}, 415)
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault("X-Frame-Options", "DENY")
        return response

    @app.exception_handler(RequestValidationError)
    async def validation_handler(_request: Request, exc: RequestValidationError):
        errs = exc.errors()
        msg = "; ".join(f"{'.'.join(str(x) for x in e.get('loc', [])[1:])}: {e.get('msg')}" for e in errs[:4])
        return JSONResponse({"detail": {"code": "invalid", "message": msg or "Invalid request."}}, 422)

    @app.exception_handler(Exception)
    async def unhandled(_request: Request, exc: Exception):
        log.exception("unhandled error")
        return JSONResponse({"detail": {"code": "server_error",
                                        "message": "Something went wrong on the host. Details are in the log."}}, 500)

    for r in (system.public, auth.router, system.router, library.router, categories_api.router, players.router,
              games_api.router, media.router, stats_api.router, ws.router):
        app.include_router(r)

    dist = cfg.frontend_dist
    if (dist / "index.html").is_file():
        if (dist / "assets").is_dir():
            app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

        @app.get("/{full_path:path}", include_in_schema=False)
        async def spa(full_path: str):
            if full_path.startswith(("api/", "ws/")):
                raise HTTPException(404)
            candidate = (dist / full_path).resolve()
            if full_path and sec.is_within(candidate, dist) and candidate.is_file():
                return FileResponse(candidate)
            return FileResponse(dist / "index.html", headers={"Cache-Control": "no-cache"})
    else:
        @app.get("/", include_in_schema=False)
        async def no_frontend():
            return JSONResponse({"message": "Backend running. Build the frontend (npm run build in frontend/)."})

    return app
