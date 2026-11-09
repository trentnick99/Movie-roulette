"""Command line entry point.

    python -m app                 start Movie Roulette and open it in the browser
    python -m app --no-browser    start without opening a browser
    python -m app migrate         apply database migrations only
    python -m app check           report dependencies (Python packages, FFmpeg, frontend build)
"""
from __future__ import annotations

import argparse
import json
import logging
import socket
import sys
import threading
import time
import urllib.request
import webbrowser

from . import __version__


def _port_in_use(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


def _existing_instance(port: int) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=2) as r:
            return json.loads(r.read().decode()).get("app") == "movie-roulette"
    except Exception:
        return False


def check() -> int:
    from .config import get_config
    from .services.media_tools import find_tool, tool_version

    cfg = get_config()
    ok = True
    print(f"Movie Roulette {__version__}")
    print(f"Python        {sys.version.split()[0]}")
    for tool in ("ffmpeg", "ffprobe"):
        path = find_tool(tool)
        print(f"{tool:<13} {tool_version(tool) if path else 'MISSING - install FFmpeg (see README)'}")
        ok = ok and bool(path)
    front = (cfg.frontend_dist / "index.html").is_file()
    print(f"Frontend      {'built' if front else 'NOT BUILT - run npm run build in frontend/'}")
    print(f"Data folder   {cfg.data_dir}")
    return 0 if ok and front else 1


def run(open_browser: bool = True) -> int:
    import uvicorn

    from . import db, migrate
    from . import security as sec
    from .config import get_config
    from .context import ctx
    from .logging_setup import setup as setup_logging
    from .main import create_app
    from .services import settings

    cfg = get_config()
    setup_logging(cfg.log_dir, cfg.log_level)
    log = logging.getLogger("movie_roulette")
    if _port_in_use(cfg.port):
        if _existing_instance(cfg.port):
            print("Movie Roulette is already running - opening it.")
            if open_browser:
                webbrowser.open(f"http://127.0.0.1:{cfg.port}/")
            return 0
        print(f"Port {cfg.port} is used by another program. Set MR_PORT in .env to a free port.")
        return 2
    migrate.upgrade(cfg.db_path)
    first = True
    while True:
        db.init_engine(cfg.db_path)
        with db.session_scope() as s:
            lan = settings.network(s).lan_enabled
        host = cfg.lan_bind if lan else "127.0.0.1"
        c = ctx()
        c.bound_host = host  # type: ignore[attr-defined]
        c.lan_allowed = lan  # type: ignore[attr-defined]
        c.restart_requested = False
        token = sec.new_launch_token()
        app = create_app(run_migrations=False)
        server = uvicorn.Server(uvicorn.Config(app, host=host, port=cfg.port, log_level="warning",
                                               proxy_headers=False, ws_ping_interval=20, ws_ping_timeout=30,
                                               timeout_graceful_shutdown=5))
        c.server = server  # type: ignore[attr-defined]
        url = f"http://127.0.0.1:{cfg.port}/"
        print(f"\n  Movie Roulette {__version__} is running at {url}")
        print("  LAN access: " + (f"ON (other devices: port {cfg.port})" if lan else "off (this computer only)"))
        print("  Close this window or press Ctrl+C to stop.\n")
        if open_browser and first:
            def _open(srv=server, link=f"{url}?launch={token}") -> None:
                for _ in range(100):
                    if srv.started:
                        webbrowser.open(link)
                        return
                    time.sleep(0.1)
            threading.Thread(target=_open, daemon=True).start()
        first = False
        try:
            server.run()
        except KeyboardInterrupt:
            break
        if not c.restart_requested:
            break
        log.info("restarting to apply network settings")
        time.sleep(0.5)
    db.dispose()
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="movie-roulette")
    p.add_argument("command", nargs="?", default="run", choices=["run", "migrate", "check"])
    p.add_argument("--no-browser", action="store_true")
    a = p.parse_args(argv)
    if a.command == "migrate":
        from . import migrate
        from .config import get_config

        migrate.upgrade(get_config().db_path)
        print("Database is up to date.")
        return 0
    if a.command == "check":
        return check()
    return run(open_browser=not a.no_browser)


if __name__ == "__main__":
    sys.exit(main())
